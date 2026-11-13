"""End-to-end run of the RSIAgent algorithm against a live model endpoint.

Broad wave -> deep target with practice -> freeze -> held-out evaluation,
plus a no-memory baseline on the same held-out tasks for comparison.
The key is read from RSI_API_KEY (never written to disk).
"""
import json
from datetime import datetime, timezone
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from model_host import ChatClient
from explore import ExploreRun

KEY = os.environ.get("RSI_API_KEY", "").strip()
if not KEY:
    raise SystemExit("set RSI_API_KEY in the environment (do not pass it on the command line)")
BASE = os.environ.get("RSI_BASE_URL", "http://100.64.0.88:7864/v1")
MODEL = os.environ.get("RSI_MODEL", "deepseek-v4.1-flash")

DOMAIN = "single-digit arithmetic"   # generic training domain, no benchmark data
TARGET = "12 * 7"
HELD_OUT = ["9 * 6", "13 + 29", "100 - 45"]


def baseline_client():
    # A second protocol with empty memory: Actor sees no lessons, everything
    # else identical. This is the paper's matched no-memory baseline.
    return ExploreRun(ChatClient(BASE, KEY, MODEL), max_practices=0)


def main():
    client = ChatClient(BASE, KEY, MODEL)
    run = ExploreRun(client, max_practices=2, held_out=HELD_OUT)

    run.broad(DOMAIN, wave_size=2)          # 1. broad wave (2 tasks)
    deep_verdict = run.deep(TARGET)          # 2. deep target + practice + retry
    frozen = run.freeze()                    # 3. freeze memory

    evolved = run.evaluate(HELD_OUT, frozen) # 4. held-out with frozen memory

    base = baseline_client()
    base.protocol.memory = []                # no-memory baseline, same tasks
    base_evals = []
    for task in HELD_OUT:
        candidate, verdict = base.protocol._attempt(task, [])
        base_evals.append({"task": task, "verdict": verdict})

    report = run.report()
    report.update({
        "model": MODEL,
        "endpoint": BASE,
        "domain": DOMAIN,
        "target": TARGET,
        "target_verdict": deep_verdict,
        "frozen_digest": frozen.sha256,
        "held_out_evolved": evolved,
        "held_out_baseline": base_evals,
        "frozen_unchanged_after_eval": frozen.sha256 == run.protocol.freeze().sha256,
    })
    out = Path(__file__).resolve().parents[1] / ("explore-report-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    with out.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=1)
    print(json.dumps({k: report[k] for k in (
        "model", "role_calls", "target_verdict", "held_out_evolved",
        "held_out_baseline", "frozen_unchanged_after_eval")}, ensure_ascii=False))
    print("full report: " + str(out))


if __name__ == "__main__":
    main()
