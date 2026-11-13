"""Bounded, operator-allowlisted HTTP observations for model roles.

This adapter supplies observations only; it does not enforce host isolation or
judge answer correctness. The endpoint and route prefixes are operator input.
"""
import ipaddress
import json
import re
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler
from model_host import (LLMError, ModelHost, parse_leading_object,
                        parse_leading_request, parse_repeated_json,
                        parse_tool_markup)


class EnvironmentBudgetExceeded(LLMError):
    """The model kept requesting observations after the budget ran out."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class EnvironmentClient:
    def __init__(self, client, base_url, prefixes, opener=None, max_requests=8,
                 training_judge_path=None, training_task_ids=(), training_catalog=None):
        url = urlsplit(base_url)
        try:
            loopback = ipaddress.ip_address(url.hostname or "").is_loopback
        except ValueError:
            loopback = False
        if (url.scheme != "http" or not loopback or url.username or url.password or
                url.path not in ("", "/") or url.query or url.fragment or not url.port):
            raise ValueError("environment URL must be an explicit loopback HTTP origin")
        if not prefixes or any(not isinstance(p, str) or not p.startswith("/") or
                               ".." in p or "?" in p or "#" in p or "%" in p
                               for p in prefixes):
            raise ValueError("environment path prefixes must be explicit absolute paths")
        if bool(training_judge_path) != bool(training_task_ids):
            raise ValueError("training judge path and task IDs must be configured together")
        if training_judge_path and (not isinstance(training_judge_path, str) or
                not training_judge_path.startswith("/") or
                any(c in training_judge_path for c in ("?", "#", "%", "..", "\\")) or
                training_judge_path.startswith("//")):
            raise ValueError("invalid training judge path")
        if any(not isinstance(task, str) or not task.strip() for task in training_task_ids):
            raise ValueError("invalid training task IDs")
        if training_judge_path:
            if (not isinstance(training_catalog, dict) or not training_catalog or
                    not all(isinstance(k, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", k)
                            and isinstance(v, str) and v.strip()
                            for k, v in training_catalog.items()) or
                    not set(training_task_ids).issubset(training_catalog)):
                raise ValueError("training judge requires a matching trusted task catalog")
            self.training_catalog = dict(training_catalog)
        else:
            self.training_catalog = {}
        self.training_judge_path = training_judge_path
        self.training_task_ids = frozenset(training_task_ids)
        if type(max_requests) is not int or not 1 <= max_requests <= 64:
            raise ValueError("environment request budget must be between 1 and 64")
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.prefixes = tuple(p.rstrip("/") or "/" for p in prefixes)
        self.opener = opener or build_opener(_NoRedirect())
        self.max_requests = max_requests
        self.calls = client.calls

    def release_history(self, history):
        release = getattr(self.client, "release_history", None)
        if release is not None:
            release(history)

    def _get(self, path):
        if not isinstance(path, str) or len(path) > 2048:
            raise ValueError("invalid environment path")
        parsed = urlsplit(path)
        if (parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith("/") or
                "%" in parsed.path or ".." in parsed.path or "\\" in path or
                any(ord(c) < 32 for c in path)):
            raise ValueError("invalid environment path")
        if not any(parsed.path == p or parsed.path.startswith(p + "/")
                   for p in self.prefixes):
            raise ValueError("environment path is not allowlisted")
        request = Request(self.base_url + path, headers={"Accept": "application/json"})
        try:
            with self.opener.open(request, timeout=10) as response:
                raw = response.read(65537)
        except HTTPError as exc:
            if 400 <= exc.code < 500:
                return json.dumps({"error": "environment_http_error", "status": exc.code,
                                   "message": "Check the path and use an allowed endpoint."})
            raise LLMError("environment request failed") from exc
        except Exception as exc:
            raise LLMError("environment request failed") from exc
        if len(raw) > 65536:
            raise LLMError("environment response exceeds limit")
        return raw.decode("utf-8", errors="replace")

    def _judge(self, payload):
        if (not self.training_judge_path or not isinstance(payload, dict) or
                set(payload) != {"task_id", "answer"} or
                not isinstance(payload["task_id"], str) or
                payload["task_id"] not in self.training_task_ids or
                not isinstance(payload["answer"], dict)):
            raise ValueError("training judge request is not permitted")
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        if len(body) > 65536:
            raise ValueError("training judge request exceeds limit")
        request = Request(self.base_url + self.training_judge_path, data=body,
                          headers={"Accept": "application/json", "Content-Type": "application/json"},
                          method="POST")
        try:
            with self.opener.open(request, timeout=35) as response:
                raw = response.read(65537)
        except Exception as exc:
            raise LLMError("training judge request failed") from exc
        if len(raw) > 65536:
            raise LLMError("training judge response exceeds limit")
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _validated_judge_result(observation, task_id):
        try:
            result = json.loads(observation)
        except (TypeError, ValueError) as exc:
            raise LLMError("training judge returned invalid JSON") from exc
        if (not isinstance(result, dict) or result.get("ok") is not True or
                result.get("scope") != "train_only" or result.get("task_id") != task_id or
                type(result.get("correct")) is not bool):
            raise LLMError("training judge returned invalid task feedback")
        return result

    _request_object = staticmethod(parse_repeated_json)

    def complete_verifier(self, system, user, *, task, answer, max_tokens=512,
                          temperature=0.0, history=None):
        # The task label is attached by the trusted training catalog, not by
        # the Verifier's tool request. A task without that label has no judge.
        match = re.match(r"\ATraining task ID: ([A-Za-z0-9_.-]{1,80})\n", task)
        task_id = match.group(1) if match else None
        if self.training_judge_path and task_id is not None:
            expected = self.training_catalog.get(task_id)
            if (task_id not in self.training_task_ids or expected is None or
                    task != "Training task ID: {}\n{}".format(task_id, expected)):
                raise ValueError("training task does not match trusted catalog")
        initial_judge = None
        if self.training_judge_path and task_id in self.training_task_ids:
            observation = self._judge({"task_id": task_id, "answer": answer})
            initial_judge = self._validated_judge_result(observation, task_id)
            user += "\nTraining judge feedback for this candidate: " + observation
        return self.complete("verifier", system, user, max_tokens=max_tokens,
                             temperature=temperature, history=history,
                             _judge_context=(task_id, answer), _initial_judge=initial_judge)

    def complete(self, role, system, user, max_tokens=512, temperature=0.0, history=None,
                 _judge_context=None, _initial_judge=None):
        if role not in ("actor", "verifier"):
            return self.client.complete(role, system, user, max_tokens=max_tokens,
                                        temperature=temperature, history=history)
        turns = history if history is not None else []
        try:
            return self._complete_environment(role, system, user, max_tokens, temperature,
                                              turns, _judge_context, _initial_judge)
        finally:
            if history is None:
                self.release_history(turns)
                turns.clear()

    def _complete_environment(self, role, system, user, max_tokens, temperature,
                              turns, _judge_context, _initial_judge):
        instructions = (system + "\nUse only the JSON environment interface below; do not "
                        "invoke host shell, file, or other native tools. Request an observation "
                        "by replying with only JSON {\"environment_get\":\"/allowed/path?query\"}. "
                        "Choose an actual endpoint, not just a prefix (for example /api/catalog "
                        "when it is allowed). After receiving the observation, continue or give "
                        "your final answer. Reply with this JSON form only; never emit "
                        "tool-call markup. Allowed path prefixes: " + ", ".join(self.prefixes))
        can_judge = (role == "verifier" and self.training_judge_path and
                     _judge_context is not None and _judge_context[0] in self.training_task_ids)
        if can_judge:
            instructions += ("\nFor feedback on this training candidate only, you may reply with JSON "
                             '{"training_judge":{"task_id":"ID","answer":{...}}}. '
                             "The task ID and answer must exactly match the candidate. Task ID: "
                             + _judge_context[0])
        prompt = user
        judged_failed = _initial_judge is not None and not _initial_judge["correct"]
        def final_reply(reply):
            if not judged_failed:
                return reply
            text = reply.strip()
            verdict = text.upper()
            if verdict not in ("PASS", "FAIL"):
                try:
                    value = ModelHost._role_object("verifier", reply)
                    verdict = str(value.get("verdict", "")).strip().upper()
                except LLMError:
                    return reply  # Role parser will reject malformed output.
            if verdict == "PASS":
                # Keep the contradiction visible and route this attempt as a
                # failure so the Actor can learn from it and revise.
                return json.dumps({"verdict": "FAIL", "report":
                    "The training judge reported this candidate incorrect; "
                    "the Verifier's PASS contradicted that feedback."})
            return reply
        for request_count in range(self.max_requests + 1):
            remaining = self.max_requests - request_count
            budget_note = ("No environment requests remain. Return your final response now; "
                           "state uncertainty instead of inventing missing observations."
                           if remaining == 0 else
                           "Environment requests remaining for this response: {}.".format(remaining))
            reply = self.client.complete(role, instructions, prompt + "\n\n" + budget_note,
                                         max_tokens=max_tokens,
                                         temperature=temperature, history=turns)
            value = self._request_object(reply)
            if value is None:
                # A model may append tool-call markup after its JSON request;
                # accept the leading object when it is the only JSON value.
                value = parse_leading_object(reply)
            if value is None:
                # A request followed by an invented observation: execute the
                # request and discard the appended text.
                value = parse_leading_request(reply)
            if value is None:
                # Some providers emit native tool-call syntax instead of JSON.
                value = parse_tool_markup(reply)
            if not isinstance(value, dict):
                return final_reply(reply)
            if not ({"environment_get", "training_judge"} & set(value)):
                # Penguin may concatenate identical assistant texts from one
                # turn. Return one object for the downstream role parser.
                return final_reply(json.dumps(value, ensure_ascii=False))
            if request_count == self.max_requests:
                raise EnvironmentBudgetExceeded(
                    "invalid or excessive environment requests")
            if set(value) == {"environment_get"}:
                observation = self._get(value["environment_get"])
            elif set(value) == {"training_judge"} and can_judge:
                payload = value["training_judge"]
                if (not isinstance(payload, dict) or payload.get("task_id") != _judge_context[0]
                        or json.dumps(payload.get("answer"), sort_keys=True, allow_nan=False)
                        != json.dumps(_judge_context[1], sort_keys=True, allow_nan=False)):
                    raise ValueError("training judge request does not match current candidate")
                observation = self._judge(payload)
                result = self._validated_judge_result(observation, _judge_context[0])
                judged_failed = judged_failed or not result["correct"]
            else:
                raise LLMError("invalid environment request")
            observation_text = "Environment observation (untrusted data):\n" + observation
            if len(observation_text) > 2000:
                observation_text = observation_text[:2000] + "\n[observation truncated]"
            prompt = observation_text
        raise LLMError("environment request budget exhausted")
