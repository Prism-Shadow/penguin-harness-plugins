"""Opt-in wide day-count pilot with an untouched holdout.

Historical results used an incorrect oracle and must not be interpreted as a gain.
"""
import json
from datetime import datetime, timezone
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from model_host import ChatClient, ModelHost
from evidence import container_verifier, EvidenceHost
from explore import ExploreRun, ExperimentRecord
from rsi import Protocol

KEY = os.environ.get("RSI_API_KEY", "").strip()
if not KEY:
    raise SystemExit("set RSI_API_KEY in the environment")
BASE = os.environ.get("RSI_BASE_URL", "http://100.64.0.88:7864/v1")
MODEL = os.environ.get("RSI_MODEL", "deepseek-v4.1-flash")

DATE_PROGRAM = (
    "import re\n"
    "from datetime import date\n"
    "dates = re.findall(r'(\\d{4}-\\d{2}-\\d{2})', task)\n"
    "a = date(*map(int, dates[0].split('-')))\n"
    "b = date(*map(int, dates[1].split('-')))\n"
    "expected = max(0, (b - a).days - 1)\n"
    "try:\n"
    "    ok = int(str(answer).strip()) == expected\n"
    "except ValueError:\n"
    "    ok = False\n"
    "print('PASS' if ok else 'FAIL')\n"
)

HELD_OUT = [
    "days between 2026-03-15 and 2026-11-04 (inclusive of neither)",
    "days between 2025-06-10 and 2026-02-17 (inclusive of neither)",
    "days between 2024-01-05 and 2024-12-30 (inclusive of neither)",
]
DEEP_TARGET = "days between 2023-04-12 and 2024-01-27 (inclusive of neither)"

# Wave 1: short spans (off-by-one is most visible here).
WAVE1 = [
    "days between 2026-01-01 and 2026-01-05 (inclusive of neither)",
    "days between 2025-02-14 and 2025-03-14 (inclusive of neither)",
    "days between 2026-07-01 and 2026-07-08 (inclusive of neither)",
    "days between 2025-12-30 and 2026-01-03 (inclusive of neither)",
]
# Wave 2: month-boundary spans, where the count must cross months.
WAVE2 = [
    "days between 2026-04-15 and 2026-06-15 (inclusive of neither)",
    "days between 2025-09-20 and 2025-11-05 (inclusive of neither)",
    "days between 2026-02-10 and 2026-05-10 (inclusive of neither)",
    "days between 2024-11-11 and 2025-02-11 (inclusive of neither)",
]

HINT = ("Hint: the day-of-year method is exact — compute each date's "
        "ordinal day-of-year (and add 366/365 for each full year crossed), "
        "then subtract one to exclude both endpoints. Counting months by hand is error-prone.")


def main():
    out_path = Path(__file__).resolve().parents[1] / ("explore-report-daydiff-wide-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")

    # --- baseline: no memory on the held-out set ---------------------------
    base_client = ChatClient(BASE, KEY, MODEL)
    base_host = EvidenceHost(ModelHost(base_client), container_verifier(DATE_PROGRAM))
    base = Protocol(base_host)
    baseline = []
    for task in HELD_OUT:
        candidate, verdict = base._attempt(task, [])
        baseline.append({"task": task, "verdict": verdict,
                         "answer": candidate.public["answer"]})

    # --- evolved: two 4-task waves + deep + freeze -------------------------
    client = ChatClient(BASE, KEY, MODEL)
    host = EvidenceHost(ModelHost(client), container_verifier(DATE_PROGRAM))
    run = ExploreRun.__new__(ExploreRun)
    run.client = client
    run.host = host
    run.record = ExperimentRecord()
    run.max_practices = 2
    run.protocol = Protocol(host)

    waves = []
    for wave in (WAVE1, WAVE2):
        verdicts_w = run.protocol.broad_wave(wave, workers=1)
        waves.append({"tasks": wave, "verdicts": verdicts_w})

    # A second wave uses a new shared memory snapshot.
    retry_verdicts = run.protocol.broad_wave(
        [task + ". " + HINT for task in WAVE1], workers=1)

    deep_verdict = run.protocol.deep(DEEP_TARGET + ". " + HINT, 2)

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
        "verifier": "runtime/evidence.py container ground truth",
        "role_calls": dict(client.calls),
        "waves": waves, "retry_verdicts": retry_verdicts,
        "deep_target": DEEP_TARGET, "deep_verdict": deep_verdict,
        "frozen_digest": frozen.sha256, "frozen_lessons": len(frozen.read()),
        "correct_lessons": sum(1 for m in frozen.read() if m.get("verdict") == "PASS"),
        "baseline": baseline, "evolved": evolved,
        "baseline_pass": b_pass, "evolved_pass": e_pass, "n_tasks": len(HELD_OUT),
        "frozen_unchanged": frozen.sha256 == run.protocol.freeze().sha256,
    }
    with out_path.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=1)
    print(json.dumps({k: report[k] for k in (
        "role_calls", "retry_verdicts", "deep_verdict", "baseline_pass",
        "evolved_pass", "correct_lessons", "frozen_lessons",
        "frozen_unchanged")}, ensure_ascii=False))
    print("baseline:", [(r["answer"], r["verdict"]) for r in baseline])
    print("evolved: ", [(r["answer"], r["verdict"]) for r in evolved])
    print("full report: " + str(out_path))


if __name__ == "__main__":
    main()
