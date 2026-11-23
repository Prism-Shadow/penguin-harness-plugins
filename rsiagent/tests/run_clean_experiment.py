"""Clean experiment: train/holdout strictly disjoint, corrected oracle.

Design invariants (per EXPERIMENT_NOTES.md):
- The deep target is a TRAINING task; held-out tasks never appear in any
  prompt, wave, practice or lesson during exploration.
- Oracle: exclusive of both endpoints = (b - a).days, computed in the
  restricted container (runtime/evidence.py), not by a model.
- Baseline group: no memory, held-out only. Evolved group: frozen memory
  from training only, held-out evaluated read-only with digest check.
- No hint text mentions any held-out date pair.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from model_host import ChatClient, ModelHost
from evidence import container_verifier, EvidenceHost
from explore import ExperimentRecord
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
    "expected = (b - a).days\n"
    "try:\n"
    "    ok = int(str(answer).strip()) == expected\n"
    "except ValueError:\n"
    "    ok = False\n"
    "print('PASS' if ok else 'FAIL')\n"
)

# --- strict split: no date pair appears in both sets -----------------------
WAVE = [
    "days between 2026-01-01 and 2026-01-05 (inclusive of neither)",   # 4
    "days between 2025-02-14 and 2025-03-14 (inclusive of neither)",   # 28
    "days between 2026-07-01 and 2026-07-08 (inclusive of neither)",   # 7
    "days between 2026-04-15 and 2026-06-15 (inclusive of neither)",   # 61
]
DEEP_TARGET = "days between 2025-09-20 and 2025-11-05 (inclusive of neither)"  # 46 (TRAINING)
HELD_OUT = [
    "days between 2026-03-15 and 2026-11-04 (inclusive of neither)",   # 234
    "days between 2025-06-10 and 2026-02-17 (inclusive of neither)",   # 252
    "days between 2024-01-05 and 2024-12-30 (inclusive of neither)",   # 360
]
TRAIN_ALL = WAVE + [DEEP_TARGET]
assert not any(h in t for h in HELD_OUT for t in TRAIN_ALL), "leak: held-out inside training"
assert not any(t in h for h in HELD_OUT for t in TRAIN_ALL), "leak: training inside held-out"

METHOD_HINT = (
    "Method hint: compute each date's day-of-year ordinal, add 366 or 365 "
    "for each full calendar year crossed, then subtract the smaller from "
    "the larger. Do not count either endpoint."
)


def main():
    out_path = Path(__file__).resolve().parents[1] / "explore-report-clean.json"

    def new_protocol():
        client = ChatClient(BASE, KEY, MODEL)
        host = EvidenceHost(ModelHost(client), container_verifier(DATE_PROGRAM))
        return client, Protocol(host)

    # --- group A: baseline, no memory, held-out only ------------------------
    base_client, base = new_protocol()
    baseline = []
    for task in HELD_OUT:
        candidate, verdict = base._attempt(task, [])
        baseline.append({"task": task, "verdict": verdict,
                         "answer": candidate.public["answer"]})

    # --- group B: explore on TRAINING only, then freeze ---------------------
    client, protocol = new_protocol()
    record = ExperimentRecord()

    wave_verdicts = []
    for task in WAVE:
        cand, v = protocol._attempt(task, [])
        protocol._commit(task, cand, v)
        wave_verdicts.append(v)

    # retry wave once with a general method hint (still training tasks only)
    retry_verdicts = []
    for task in WAVE:
        cand, v = protocol._attempt(task + ". " + METHOD_HINT, [])
        protocol._commit(task + ". " + METHOD_HINT, cand, v)
        retry_verdicts.append(v)

    # deep phase on the TRAINING target, with practice budget
    deep_verdict = protocol.deep(DEEP_TARGET + ". " + METHOD_HINT, 2)

    frozen = protocol.freeze()
    snapshot_path = Path(__file__).resolve().parents[1] / "clean-frozen-memory.json"
    frozen.save(snapshot_path)

    evolved = []
    for task in HELD_OUT:
        candidate, verdict = protocol.evaluate(task, frozen)
        evolved.append({"task": task, "verdict": verdict,
                        "answer": candidate.public["answer"]})

    b_pass = sum(1 for r in baseline if r["verdict"] == "PASS")
    e_pass = sum(1 for r in evolved if r["verdict"] == "PASS")

    report = {
        "model": MODEL, "endpoint": BASE,
        "design": "clean: train/holdout disjoint, deep target from training, "
                  "corrected exclusive-endpoint oracle, container ground truth",
        "verifier": "runtime/evidence.py container ground truth",
        "train_tasks": TRAIN_ALL, "held_out": HELD_OUT,
        "role_calls_explore": dict(client.calls),
        "role_calls_baseline": dict(base_client.calls),
        "wave_verdicts": wave_verdicts, "retry_verdicts": retry_verdicts,
        "deep_target": DEEP_TARGET, "deep_verdict": deep_verdict,
        "frozen_digest": frozen.sha256, "frozen_lessons": len(frozen.read()),
        "frozen_memory": frozen.read(),
        "snapshot_file": snapshot_path.name,
        "baseline": baseline, "evolved": evolved,
        "baseline_pass": b_pass, "evolved_pass": e_pass, "n_tasks": len(HELD_OUT),
        "frozen_unchanged": frozen.sha256 == protocol.freeze().sha256,
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({k: report[k] for k in (
        "wave_verdicts", "retry_verdicts", "deep_verdict",
        "baseline_pass", "evolved_pass", "frozen_lessons",
        "frozen_unchanged")}, ensure_ascii=False))
    print("baseline:", [(r["answer"], r["verdict"]) for r in baseline])
    print("evolved: ", [(r["answer"], r["verdict"]) for r in evolved])
    print("report:", out_path)
    print("snapshot:", snapshot_path)


if __name__ == "__main__":
    main()
