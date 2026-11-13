"""Opt-in day-count pilot; excludes both endpoints and keeps holdout separate.

Historical results used an incorrect oracle and must not be interpreted as a gain.
"""
import json
from datetime import datetime, timezone
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from model_host import ChatClient, ModelHost
from explore import ExploreRun, ExperimentRecord
from rsi import Protocol
from docker_verifier import verify_in_container

KEY = os.environ.get("RSI_API_KEY", "").strip()
if not KEY:
    raise SystemExit("set RSI_API_KEY in the environment")
BASE = os.environ.get("RSI_BASE_URL", "http://100.64.0.88:7864/v1")
MODEL = os.environ.get("RSI_MODEL", "deepseek-v4.1-flash")

HELD_OUT = [
    "days between 2026-03-15 and 2026-11-04 (inclusive of neither)",
    "days between 2025-06-10 and 2026-02-17 (inclusive of neither)",
    "days between 2024-01-05 and 2024-12-30 (inclusive of neither)",
]
TRAIN_TASKS = [
    "days between 2026-01-01 and 2026-01-05 (inclusive of neither)",
    "days between 2025-02-14 and 2025-03-14 (inclusive of neither)",
]
DEEP_TARGET = "days between 2023-04-12 and 2024-01-27 (inclusive of neither)"

# Verifier: counts dates strictly between A and B.
VERIFY_PROBE = (
    "import sys\n"
    "task, answer = sys.argv[-2:]\n"
    "from datetime import date\n"
    "import re\n"
    "dates = re.findall(r\"(\\d{4}-\\d{2}-\\d{2})\", task)\n"
    "a = date(*map(int, dates[0].split(\"-\")))\n"
    "b = date(*map(int, dates[1].split(\"-\")))\n"
    "expected = max(0, (b - a).days - 1)\n"
    "try:\n"
    "    ok = int(str(answer).strip()) == expected\n"
    "except ValueError:\n"
    "    ok = False\n"
    "print(\"PASS\" if ok else \"FAIL\")\n"
)

def container_verify(task, public):
    result = verify_in_container(
        None, ["python", "-I", "-c", VERIFY_PROBE, "--", task, str(public["answer"])])
    verdict = result.stdout.strip().splitlines()[-1]
    if verdict not in ("PASS", "FAIL"):
        raise RuntimeError("container verifier returned: " + verdict[:100])
    return verdict

class ContainerVerifyHost:
    """Model Actor + in-container date-arithmetic Verifier (evidence-based)."""

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
    out_path = Path(__file__).resolve().parents[1] / ("explore-report-daydiff-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")

    # --- baseline: no memory, all tasks ------------------------------------
    base_client = ChatClient(BASE, KEY, MODEL)
    base = Protocol(ContainerVerifyHost(ModelHost(base_client)))
    baseline = []
    for task in HELD_OUT:
        candidate, verdict = base._attempt(task, [])
        baseline.append({"task": task, "verdict": verdict,
                         "answer": candidate.public["answer"]})

    # --- evolved: wave + deep + freeze + held-out ---------------------------
    client = ChatClient(BASE, KEY, MODEL)
    host = ContainerVerifyHost(ModelHost(client))
    run = ExploreRun.__new__(ExploreRun)
    run.client = client
    run.host = host
    run.record = ExperimentRecord()
    run.max_practices = 2
    run.protocol = Protocol(host)

    verdicts_w = run.protocol.broad_wave(TRAIN_TASKS, workers=1)

    deep_verdict = run.protocol.deep(DEEP_TARGET, 2)

    frozen = run.protocol.freeze()
    evolved = []
    for task in HELD_OUT:
        candidate, verdict = run.protocol.evaluate(task, frozen)
        evolved.append({"task": task, "verdict": verdict,
                        "answer": candidate.public["answer"]})

    b_pass = sum(1 for r in baseline if r["verdict"] == "PASS")
    e_pass = sum(1 for r in evolved if r["verdict"] == "PASS")

    report = {
        "model": MODEL, "endpoint": BASE,
        "domain": "day-count differences (exclusive of both endpoints)",
        "verifier": "restricted Docker container, computed ground truth (environment evidence)",
        "role_calls": dict(client.calls),
        "train_tasks": TRAIN_TASKS, "wave_verdicts": verdicts_w,
        "deep_target": DEEP_TARGET, "deep_verdict": deep_verdict,
        "frozen_digest": frozen.sha256, "frozen_lessons": len(frozen.read()),
        "frozen_memory": frozen.read(),
        "baseline": baseline, "evolved": evolved,
        "baseline_pass": b_pass, "evolved_pass": e_pass, "n_tasks": len(HELD_OUT),
        "frozen_unchanged": frozen.sha256 == run.protocol.freeze().sha256,
        "verifier_isolated_from_actor_memory": True,
    }
    with out_path.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=1)
    print(json.dumps({k: report[k] for k in (
        "role_calls", "wave_verdicts", "deep_verdict", "baseline_pass",
        "evolved_pass", "n_tasks", "frozen_unchanged")}, ensure_ascii=False))
    print("baseline:", [(r["task"][:44], r["answer"], r["verdict"]) for r in baseline])
    print("evolved: ", [(r["task"][:44], r["answer"], r["verdict"]) for r in evolved])
    print("full report: " + str(out_path))


if __name__ == "__main__":
    main()
