"""Opt-in model smoke test; API key read from stdin, never persisted."""
import json
import os
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from rsi import Protocol

key = sys.stdin.read().strip()
if not key:
    raise SystemExit("missing bearer key on stdin")
base = os.environ.get("RSI_BASE_URL", "http://100.64.0.88:7864/v1").rstrip("/")
model = os.environ.get("RSI_MODEL", "deepseek-v4.1-flash")
counts = {"curriculum": 0, "actor": 0, "verifier": 0}


def ask(role, prompt):
    counts[role] += 1
    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps({"model": model, "messages": [
            {"role": "user", "content": prompt}],
            "max_tokens": 512, "temperature": 0}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(request, timeout=60) as response:
        message = json.load(response)["choices"][0]["message"]
    text = (message.get("content") or "").strip()
    if not text:
        raise RuntimeError(role + " returned empty content")
    return text


class Host:
    def run_actor(self, task, memory):
        answer = ask("actor", "Solve this arithmetic exercise. Return only the integer answer: "
                     + task + ". Prior lessons: " + json.dumps(memory))
        return SimpleNamespace(public={"answer": answer}, private=None)

    def verify(self, task, public):
        # No actor memory is sent to the verifier. This is an API data boundary,
        # not OS-level isolation.
        response = ask("verifier", "Independently solve the arithmetic task and check the candidate. "
                       "Return exactly PASS or FAIL. Task: " + task +
                       " Candidate answer: " + public["answer"])
        verdict = response.strip().upper()
        if verdict not in ("PASS", "FAIL"):
            raise RuntimeError("verifier returned an invalid verdict")
        return verdict

    def consolidate(self, memory, task, candidate, verdict):
        return memory + [{"task": task, "answer": candidate.public["answer"], "verdict": verdict}]

    def curriculum_review(self, task, verdict, memory):
        response = ask("curriculum", "The target exercise was " + task + " and verifier verdict was "
                       + verdict + ". Choose one short arithmetic practice exercise. "
                       "Return only the exercise.")
        return response


host = Host()
protocol = Protocol(host)
protocol.broad_wave(["2 + 3", "7 - 4"], workers=1)
candidate, verdict = protocol._attempt("6 + 2", protocol.memory)
protocol._commit("6 + 2", candidate, verdict)
practice = host.curriculum_review("6 + 2", verdict, protocol.memory)
candidate, verdict = protocol._attempt(practice, protocol.memory)
protocol._commit(practice, candidate, verdict)
frozen = protocol.freeze()
_, final = protocol.evaluate("3 + 4", frozen)
print(json.dumps({"model": model, "roles_called": counts,
                  "frozen_memory_digest_unchanged": frozen.sha256 == protocol.freeze().sha256,
                  "final_verdict": final,
                  "limitations": "synthetic arithmetic; no OS-level isolation; no benchmark score"}))
