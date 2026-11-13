"""Opt-in modular-exponentiation pilot with a disjoint development target.

The historical report is preserved but not a valid held-out comparison.
"""
import json
from datetime import datetime, timezone
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from model_host import ChatClient
from explore import ExploreRun
from docker_verifier import verify_in_container

KEY = os.environ.get("RSI_API_KEY", "").strip()
if not KEY:
    raise SystemExit("set RSI_API_KEY in the environment")
BASE = os.environ.get("RSI_BASE_URL", "http://100.64.0.88:7864/v1")
MODEL = os.environ.get("RSI_MODEL", "deepseek-v4.1-flash")

DOMAIN = ("modular exponentiation by hand: compute base^exp mod m exactly "
          "using step-by-step repeated squaring; the answer is the residue "
          "in 0..m-1")

TRAIN_HINT = ("Method to practice: repeated squaring mod m — compute "
              "base^(1,2,4,8,...) mod m by squaring, then multiply the "
              "powers matching the exponent's binary digits, reducing mod m "
              "every step so numbers stay small.")

HELD_OUT = ["9^11 mod 101", "5^17 mod 97", "2^21 mod 137"]
BASELINE_ONLY = ["7^13 mod 101", "11^7 mod 97", "3^20 mod 137"]
TASKS_ALL = HELD_OUT + BASELINE_ONLY  # ground truth table below

TRUTH = {
    "7^13 mod 101": 75, "11^7 mod 97": 65, "3^20 mod 137": 4,
    "9^11 mod 101": 49, "5^17 mod 97": 83, "2^21 mod 137": 93,
}

# Verifier program: ground truth is computed in-container from the task string.
VERIFY_PROBE = (
    "import sys\n"
    "task, answer = sys.argv[-2:]\n"
    "parts = task.replace('^', ' ').replace('mod', ' ').split()\n"
    "base, exp, m = int(parts[0]), int(parts[1]), int(parts[2])\n"
    "expected = pow(base, exp, m)\n"
    "try:\n"
    "    ok = int(answer.strip()) == expected\n"
    "except ValueError:\n"
    "    ok = False\n"
    "print('PASS' if ok else 'FAIL')\n"
)

def container_verify(task, public):
    """Environment-evidence verdict: computed in the restricted container."""
    result = verify_in_container(
        None, ["python", "-I", "-c", VERIFY_PROBE, "--", task,
                 str(public["answer"]) if not isinstance(public["answer"], (int, float)) else repr(public["answer"])])
    verdict = result.stdout.strip().splitlines()[-1]
    if verdict not in ("PASS", "FAIL"):
        raise RuntimeError("container verifier returned: " + verdict[:100])
    return verdict

class ContainerVerifyHost:
    """Model Actor + in-container ground-truth Verifier (evidence-based)."""

    def __init__(self, model_host):
        self.model_host = model_host

    def run_actor(self, task, memory):
        return self.model_host.run_actor(task, memory)

    def verify(self, task, public):
        return container_verify(task, public)

    def consolidate(self, memory, task, candidate, verdict):
        return self.model_host.consolidate(memory, task, candidate, verdict)

    def curriculum_review(self, task, verdict, memory):
        return self.model_host.curriculum_review(task, verdict, memory)


def main():
    out_path = Path(__file__).resolve().parents[1] / ("explore-report-modpow-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")

    # --- baseline (no memory, no lessons) on all 6 tasks -------------------
    base_client = ChatClient(BASE, KEY, MODEL)
    base_host = ContainerVerifyHost(__import__("model_host").ModelHost(base_client))
    from rsi import Protocol
    base = Protocol(base_host)
    baseline = []
    for task in TASKS_ALL:
        candidate, verdict = base._attempt(task, [])
        baseline.append({"task": task, "verdict": verdict,
                         "answer": candidate.public["answer"]})

    # --- evolved: broad wave seeds the method, deep practice, freeze -------
    client = ChatClient(BASE, KEY, MODEL)
    model_host = __import__("model_host").ModelHost(client)
    host = ContainerVerifyHost(model_host)
    run = ExploreRun.__new__(ExploreRun)
    run.client = client
    run.host = host
    run.record = __import__("explore").ExperimentRecord()
    run.max_practices = 2
    from rsi import Protocol as P
    run.protocol = P(host)

    # broad: one wave with two method-seeding tasks
    wave = [("6^7 mod 11 (compute 6^1, 6^2, 6^4 mod 11 then combine)", 8),
            ("2^10 mod 13 (compute 2^1, 2^2, 2^4, 2^8 mod 13 then combine)", 10)]
    verdicts_w = run.protocol.broad_wave([task for task, _ in wave], workers=1)

    # Development target is disjoint from all baseline and held-out tasks.
    deep_target = "13^11 mod 103"
    deep_verdict = run.protocol.deep(deep_target, 2)

    frozen = run.protocol.freeze()
    evolved = []
    for task in TASKS_ALL:
        candidate, verdict = run.protocol.evaluate(task, frozen)
        evolved.append({"task": task, "verdict": verdict,
                        "answer": candidate.public["answer"]})

    b_pass = sum(1 for r in baseline if r["verdict"] == "PASS")
    e_pass = sum(1 for r in evolved if r["verdict"] == "PASS")

    report = {
        "model": MODEL, "endpoint": BASE,
        "domain": "modular exponentiation (modpow)",
        "verifier": "restricted Docker container, computed ground truth (environment evidence)",
        "role_calls": dict(client.calls),
        "wave": [t for t, _ in wave], "wave_verdicts": verdicts_w,
        "deep_target": deep_target, "deep_verdict": deep_verdict,
        "frozen_digest": frozen.sha256, "frozen_lessons": len(frozen.read()),
        "baseline": baseline, "evolved": evolved,
        "baseline_pass": b_pass, "evolved_pass": e_pass, "n_tasks": len(TASKS_ALL),
        "frozen_unchanged": frozen.sha256 == run.protocol.freeze().sha256,
        "verifier_isolated_from_actor_memory": True,
    }
    with out_path.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=1)
    print(json.dumps({k: report[k] for k in (
        "role_calls", "wave_verdicts", "deep_verdict", "baseline_pass",
        "evolved_pass", "n_tasks", "frozen_unchanged")}, ensure_ascii=False))
    print("baseline:", [(r["task"], r["verdict"]) for r in baseline])
    print("evolved: ", [(r["task"], r["verdict"]) for r in evolved])
    print("full report: " + str(out_path))


if __name__ == "__main__":
    main()
