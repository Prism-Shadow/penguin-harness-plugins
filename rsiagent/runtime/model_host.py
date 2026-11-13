"""Model-driven host adapters for the RSIAgent protocol.

Each adapter is one role (Curriculum, Actor, Verifier) calling an
OpenAI-compatible chat-completions endpoint. The adapters are deliberately
generic — no benchmark, dataset or task vocabulary lives here (RFC-0003:
algorithm and data decoupled). Task content is supplied by the caller as
plain strings; the roles only see what the protocol hands them:

- the Actor sees the task and the memory snapshot (a copy),
- the Verifier sees the task and the Actor's *public* candidate only,
- the Curriculum sees the target, the verdict and the memory (a copy).

Every prompt and raw reply can be captured through ``log`` for the
experiment record; nothing is persisted by the adapters themselves.
"""
import json
import re
import urllib.request
from types import SimpleNamespace


class LLMError(RuntimeError):
    """A role could not produce a usable reply."""


def parse_leading_request(text):
    """Decode an environment-request object at the start of a reply.

    Models sometimes append an invented or pasted observation after their
    request. Only a leading object whose sole key is a known request key is
    accepted; anything else is refused, so hallucinated data can never be
    mistaken for a request or an answer. The real observation is fetched
    afterwards through the allowlist.
    """
    if not isinstance(text, str):
        return None
    start = text.find("{")
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    if not isinstance(value, dict) or len(value) != 1:
        return None
    if set(value) - {"environment_get", "training_judge"}:
        return None
    return value


_TOOL_MARKUP = re.compile(
    r'invoke\s+name="(environment_get|training_judge)"[^>]*>\s*(.*?)\s*</', re.S)


def parse_tool_markup(text):
    """Decode a native tool-call block as an environment request.

    Some providers answer with their own tool-call syntax when no tools are
    declared (DeepSeek emits ``invoke name="environment_get">/path``). The
    request is executed through the same allowlist, never trusted as data.
    """
    if not isinstance(text, str):
        return None
    match = _TOOL_MARKUP.search(text)
    if match is None:
        return None
    name, payload = match.group(1), match.group(2).strip()
    if name == "environment_get" and payload.startswith("/") and " " not in payload:
        return {"environment_get": payload}
    return None


def parse_json_sequence(text):
    """Decode every JSON value in text, in order; None if any part is not JSON.

    Models often batch several requests in one reply:
        {"vm_run": "ls /root"}\n{"vm_run": "cat /root/out.txt"}
    """
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    remaining = text.strip()
    values = []
    while remaining:
        try:
            value, end = decoder.raw_decode(remaining)
        except ValueError:
            return None
        values.append(value)
        remaining = remaining[end:].lstrip()
    return values or None


def parse_leading_object(text):
    """Decode a single JSON object at the start of text.

    Models sometimes append tool-call markup (for example DeepSeek's
    ``<｜｜DSML｜｜>`` block) after an otherwise complete JSON object. Accept
    that object when nothing else in the remainder is an object or array;
    refuse text that encodes two different values.
    """
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    start = min([index for index in (text.find("{"), text.find("[")) if index >= 0],
                default=-1)
    if start < 0:
        return None
    try:
        value, end = decoder.raw_decode(text[start:])
    except ValueError:
        return None
    rest = text[start + end:]
    # Refuse a reply that carries a second, different JSON value; prose or
    # tool-call markup after the object is tolerated. Braces inside that
    # prose (for example "/api/accounts/{account_id}") are not values.
    for position, character in enumerate(rest):
        if character not in "{[":
            continue
        try:
            other, _ = decoder.raw_decode(rest[position:])
        except ValueError:
            continue
        if other != value:
            return None
    return value


def parse_embedded_objects(text):
    """Every JSON object embedded in text, in order; prose between is allowed.

    A model that drafts, comments on its own draft, then redrafts produces
    several objects separated by prose. ``parse_repeated_json`` refuses that
    because the whole text is not JSON, which loses a perfectly usable action.
    """
    if not isinstance(text, str):
        return []
    decoder = json.JSONDecoder()
    found = []
    index = 0
    length = len(text)
    while index < length:
        start = text.find("{", index)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text[start:])
        except ValueError:
            index = start + 1
            continue
        if isinstance(value, dict):
            found.append(value)
        index = start + end
    return found


def parse_repeated_json(text):
    """Decode one JSON value or multiple identical concatenated values."""
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    remaining = text.strip()
    values = []
    while remaining:
        try:
            value, end = decoder.raw_decode(remaining)
        except ValueError:
            return None
        values.append(value)
        remaining = remaining[end:].lstrip()
    try:
        canonical = [json.dumps(value, sort_keys=True, allow_nan=False) for value in values]
    except (TypeError, ValueError):
        return None
    if canonical and all(value == canonical[0] for value in canonical):
        return values[0]
    return None


class Verdict(str):
    """A PASS/FAIL verdict carrying verifier feedback for Actor distillation."""
    def __new__(cls, verdict, report=""):
        value = super().__new__(cls, verdict)
        value.report = report
        return value


class ChatClient:
    """Minimal OpenAI-compatible client with per-role usage accounting."""

    def __init__(self, base_url, api_key, model, timeout=60, log=None):
        self.base_url = str(base_url).rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.log = log
        self.calls = {}  # role -> number of completions

    def complete(self, role, system, user, max_tokens=512, temperature=0.0, history=None):
        messages = ([{"role": "system", "content": system}] +
                    list(history or []) + [{"role": "user", "content": user}])
        # A reasoning model can spend the whole completion budget on thinking
        # and return empty content; one bounded retry with a larger budget
        # recovers it without masking a persistent failure.
        text = ""
        last_text = ""
        last_error = None
        diag = []
        budgets = [max_tokens]
        while budgets[-1] < 16384:
            budgets.append(min(budgets[-1] * 2, 16384))
        for budget in budgets:
            request = urllib.request.Request(
                self.base_url + "/chat/completions",
                data=json.dumps({"model": self.model, "messages": messages,
                                 "max_tokens": budget,
                                 "temperature": temperature}).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + self.api_key})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
            except Exception as exc:  # network / HTTP / JSON
                last_error = exc
                continue
            try:
                text = str((payload["choices"][0]["message"].get("content") or "")).strip()
                finish = payload["choices"][0].get("finish_reason")
            except (KeyError, IndexError, TypeError) as exc:
                last_error = exc
                continue
            if text and finish != "length":
                break
            if text and finish == "length":
                # Truncated JSON is worse than no answer: retry with more room
                # and keep this text only as a last resort.
                last_text = text
                diag.append({"finish": finish, "truncated": True,
                             "usage": payload.get("usage")})
                text = ""
                continue
            text = ""
            diag.append({"finish": finish,
                         "usage": payload.get("usage")})
        if not text and last_text:
            text = last_text
        if not text:
            if last_error is not None:
                raise LLMError(role + " call failed: " + repr(last_error)
                               + " diag: " + repr(diag)) from last_error
            raise LLMError(role + " returned empty content; diag: "
                           + repr(diag))
        self.calls[role] = self.calls.get(role, 0) + 1
        if history is not None:
            history.extend([{"role": "user", "content": user},
                            {"role": "assistant", "content": text}])
        if self.log is not None:
            self.log({"role": role, "system": system, "user": user, "reply": text})
        return text


# --- Role charters: method only, no task vocabulary --------------------------

ACTOR_SYSTEM = (
    "You are the Actor in the RSIAgent exploration protocol. Solve the task "
    "you are given, using the prior lessons only as hints that may be wrong, "
    "outdated or inapplicable — verify against the task itself. Reply with a "
    "single JSON object: your solution as \"answer\" (use the JSON structure required by the task) and "
    "one short sentence of evidence as \"evidence\". No other text.")

VERIFIER_SYSTEM = (
    "You are the independent Verifier in the RSIAgent exploration protocol. "
    "You see only the task and the candidate's public answer — never the "
    "Actor's reasoning, lessons or memory. Solve the task yourself, then "
    "judge the candidate strictly against your own result and the task's "
    "requirements. Reply with one JSON object containing verdict (PASS or FAIL) "
    "and report (specific, observable reasons).")

CURRICULUM_SYSTEM = (
    "You are the Curriculum in the RSIAgent exploration protocol. Review the "
    "target attempt and its verification. Reply with exactly one JSON object: "
    "{\"decision\":\"ready\"} when further practice is not justified, or "
    "{\"decision\":\"practice\",\"task\":\"one focused practice task\"}. "
    "Readiness is not a verdict of task correctness.")

MEMORY_SYSTEM = (
    "You are the Actor distilling experience from a task attempt and its "
    "verifier verdict. Your reply is a LESSON, never the candidate answer and "
    "never a done declaration: do not echo the quoted candidate, actor response "
    "or checks. Reply with exactly one JSON object and nothing else, with these "
    "four nonempty string fields: condition, action, outcome, support. Describe "
    "only the supplied task, candidate, and verdict; do not invent observations, "
    "causal certainty, or a successful outcome for a failed attempt. Generalize "
    "only when justified; include uncertainty in support.")


RECONCILE_SYSTEM = (
    "You are the same Actor reconciling the complete causal memory after "
    "distillation. Review all existing lessons and the draft against your "
    "trajectory and verifier feedback. Remove duplicates and unsupported or "
    "contradicted claims; narrow uncertain conclusions. Return exactly one "
    "JSON object with a lessons array. Each lesson must contain nonempty "
    "condition, action, outcome, support and source_ref fields. Use one exact "
    "source_ref from an input lesson; never invent or merge source references.")


def _clip(value, limit=1200):
    """A quoted value, bounded so a model cannot mistake it for the task."""

    text = "" if value is None else (value if isinstance(value, str)
                                     else json.dumps(value, ensure_ascii=False))
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _short_answer(public, limit=400):
    """The attempt's answer, short enough that a model will not copy it whole."""

    value = public.get("answer") if isinstance(public, dict) else public
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _lessons(memory):
    if not memory:
        return "(none yet)"
    return json.dumps(memory, ensure_ascii=False)


class ModelHost:
    """Protocol host wiring the three roles to a ChatClient."""

    verifier_mode = "model judgment; no container isolation"

    def __init__(self, client, actor_max_tokens=4096, curriculum_history=None,
                 reconcile_memory=False, memory_attempts=2):
        if type(memory_attempts) is not int or memory_attempts < 1:
            raise ValueError("memory_attempts must be a positive integer")
        self.memory_attempts = memory_attempts
        self.client = client
        self.actor_max_tokens = actor_max_tokens
        self.curriculum_history = curriculum_history if curriculum_history is not None else []
        self.reconcile_memory = reconcile_memory

    def run_actor(self, task, memory):
        # Keep the Actor's session through verification and later distillation.
        # EnvironmentClient appends tool observations to this same history.
        history = []
        reply = self.client.complete(
            "actor", ACTOR_SYSTEM,
            "Task: {}\n\nPrior lessons (hints, verify before use): {}".format(task, _lessons(memory)),
            history=history, max_tokens=self.actor_max_tokens)
        try:
            value = self._role_object("actor", reply)
        except LLMError:
            # Keep legacy plain-text answers, but never turn a broken JSON or
            # Markdown-fenced object into a misleading plain-text answer.
            if reply.strip().startswith(("{", "[", "```")):
                raise
            value = {"answer": reply}
        if not isinstance(value, dict):
            raise LLMError("actor reply is not an answer object: " + reply[:200])
        if "answer" not in value and value and not (
                {"environment_get", "training_judge"} & set(value)):
            # The task defined its own answer schema; the object is the answer.
            value = {"answer": value}
        answer = value.get("answer")
        if answer is None or (isinstance(answer, str) and not answer.strip()):
            raise LLMError("actor reply has an empty answer")
        return SimpleNamespace(
            public={"answer": answer,
                    "evidence": str(value.get("evidence", ""))[:400]},
            # Retained for the same Actor's later distillation. Protocol sends
            # only public to the Verifier, never this private response.
            private={"actor_reply": reply, "actor_history": history})

    def verify(self, task, public):
        user = "Task: {}\nCandidate answer: {}\nVerdict:".format(
            task, json.dumps(public["answer"], ensure_ascii=False))
        if hasattr(self.client, "complete_verifier"):
            reply = self.client.complete_verifier(VERIFIER_SYSTEM, user,
                                                  task=task, answer=public["answer"])
        else:
            reply = self.client.complete("verifier", VERIFIER_SYSTEM, user)
        text = reply.strip()
        if text.upper() in ("PASS", "FAIL"):
            # Older hosts can return only a verdict; do not invent findings.
            return Verdict(text.upper(), "No detailed verifier report was supplied.")
        value = self._role_object("verifier", reply)
        verdict, report = value.get("verdict"), value.get("report")
        if (not isinstance(verdict, str) or verdict.strip().upper() not in ("PASS", "FAIL") or
                not isinstance(report, str) or not report.strip()):
            raise LLMError("verifier must return PASS/FAIL with a nonempty report")
        return Verdict(verdict.strip().upper(), report.strip())

    @staticmethod
    def _role_object(role, reply):
        text = reply.strip()
        if "```" in text:
            # One fenced block with an optional language tag; surrounding
            # prose is allowed. More than one block is ambiguous and refused.
            blocks = re.findall(r"```(?:[A-Za-z0-9_.+-]+)?[ \t]*\n(.*?)```", text, re.S)
            if len(blocks) != 1:
                raise LLMError(role + " response has an invalid JSON fence")
            text = blocks[0].strip()
        value = parse_repeated_json(text)
        if value is None:
            # Several objects with prose between: the last one is the reply's
            # final intent, the same convention the turn parser uses.
            embedded = parse_embedded_objects(text)
            if embedded:
                value = embedded[-1]
        if value is None:
            # Tolerate prose around one object, but reject conflicting objects.
            first, last = text.find("{"), text.rfind("}")
            if first < 0 or last <= first:
                raise LLMError(role + " response is not JSON: " + text[:300])
            try:
                value = json.loads(text[first:last + 1])
            except json.JSONDecodeError as exc:
                raise LLMError(role + " response is not JSON: " + text[:300]) from exc
        if not isinstance(value, dict):
            raise LLMError(role + " response must be a JSON object")
        return value

    def release_candidate(self, candidate):
        """Forget the Actor's coordinator-side history after an attempt."""
        private = getattr(candidate, "private", None)
        history = private.get("actor_history") if isinstance(private, dict) else None
        if isinstance(history, list):
            release = getattr(self.client, "release_history", None)
            if release is not None:
                release(history)
            history.clear()

    def reconcile(self, memory, lesson, history):
        """Reconcile a draft without asking the model to reproduce provenance."""
        draft = list(memory) + [lesson]
        sources, visible = {}, []
        for index, item in enumerate(draft):
            if not isinstance(item, dict) or not isinstance(item.get("source_task"), str) or item.get("verdict") not in ("PASS", "FAIL"):
                raise LLMError("memory cannot be reconciled without verified provenance")
            ref = "L{:04d}".format(index + 1)
            sources[ref] = (item["source_task"], str(item["verdict"]))
            visible.append({"source_ref": ref, **{k: item.get(k, "") for k in
                            ("condition", "action", "outcome", "support")}})
        reply = self.client.complete("actor", RECONCILE_SYSTEM,
            "Draft memory: {}\nReconcile the complete memory against the "
            "available trajectory and verifier evidence.".format(json.dumps(visible, ensure_ascii=False)),
            history=history, max_tokens=4096)
        value = self._role_object("memory reconciliation", reply)
        lessons = value.get("lessons")
        fields = ("condition", "action", "outcome", "support", "source_ref")
        if (not isinstance(lessons, list) or len(lessons) > 200 or
                not all(isinstance(item, dict) and all(isinstance(item.get(k), str) and item[k].strip()
                        for k in fields) and item["source_ref"] in sources for item in lessons)):
            raise LLMError("memory reconciliation has invalid lessons or provenance")
        result = []
        for item in lessons:
            source_task, verdict = sources[item["source_ref"]]
            entry = {key: item[key].strip() for key in fields if key != "source_ref"}
            entry.update(source_task=source_task, verdict=verdict)
            result.append(entry)
        return result

    def consolidate(self, memory, task, candidate, verdict):
        private = getattr(candidate, "private", None) or {}
        history = private.get("actor_history")
        if not isinstance(history, list):
            # Compatibility with host-supplied candidates that have no session.
            history = []
        fields = ("condition", "action", "outcome", "support")
        report = (getattr(verdict, "report",
                          "No detailed verifier report was supplied."))
        shape = ("Write one lesson as {{\"condition\": ..., \"action\": ..., "
                 "\"outcome\": ..., \"support\": ...}} — four nonempty "
                 "strings, no other keys, no prose:")
        # The first prompt keeps the Actor's own reply so the same Actor distils
        # what it just did. A model shown a large JSON blob tends to continue
        # that blob instead of answering (measured: it returned the Actor's own
        # {"answer", "checks"} object), so the restatement quotes only the
        # answer.
        prompt = (
            "Task: {}\nCandidate (data, do not copy): {}\n"
            "Actor response (data, do not copy): {}\nVerifier verdict: {}\n"
            "Verifier report: {}\nPrior memory: {}\n\n{}".format(
                task,
                _short_answer(candidate.public),
                _clip(private.get("actor_reply")),
                verdict, report, _lessons(memory), shape))
        retry_prompt = (
            "Task: {}\nAnswer the attempt produced (data, do not copy): {}\n"
            "Verifier verdict: {}\nVerifier report: {}\nPrior memory: {}\n\n"
            "Your previous reply was unusable. {}".format(
                task, _short_answer(candidate.public), verdict, report,
                _lessons(memory), shape))
        lesson = None
        last_reply = ""
        for attempt in range(self.memory_attempts):
            # The first attempt keeps the Actor's own session so the same Actor
            # distils what it just did. A restatement uses a clean context: a
            # long VM transcript otherwise drowns the instruction and the model
            # continues the transcript instead of answering. The trajectory is
            # already quoted in the prompt, so nothing is lost.
            reply = self.client.complete(
                "actor", MEMORY_SYSTEM, prompt if attempt == 0 else retry_prompt,
                history=history, max_tokens=3072)
            # Pick the lesson-shaped object, not merely the last object in the
            # reply: a drafting model often appends a remark object after the
            # lesson, and "last wins" would take the remark.
            usable = [item for item in parse_embedded_objects(reply)
                      if all(isinstance(item.get(k), str) and item[k].strip()
                             for k in fields)]
            if usable:
                lesson = usable[-1]
                break
            last_reply = reply
        if lesson is None:
            raise LLMError("memory lesson lacks a required nonempty field; "
                           "last reply was: " + (last_reply or "")[:200])
        # The verifier verdict is retained separately from the model-generated
        # lesson; the lesson is not itself an independently verified fact.
        lesson = {k: lesson[k].strip() for k in fields}
        lesson["verdict"] = verdict
        lesson["source_task"] = task
        if self.reconcile_memory:
            return self.reconcile(memory, lesson, history)
        return list(memory) + [lesson]

    def curriculum_review(self, task, verdict, memory):
        reply = self.client.complete(
            "curriculum", CURRICULUM_SYSTEM,
            "Target task: {}\nVerifier verdict: {}\nLessons so far: {}\n\n"
            "Choose readiness or one focused practice task:".format(task, verdict, _lessons(memory)),
            history=self.curriculum_history)
        decision = self._role_object("curriculum", reply)
        if decision.get("decision") == "ready":
            return None
        if decision.get("decision") == "practice":
            practice = decision.get("task")
            if isinstance(practice, str) and practice.strip():
                return practice.strip()
        raise LLMError("curriculum must return ready or a nonempty practice task")
