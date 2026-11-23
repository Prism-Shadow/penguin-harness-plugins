"""End-to-end: two branches of one wave, working in parallel VMs.

Both tasks create the *same* path with *different* content:

    /root/proof.txt  ->  containing exactly alpha
    /root/proof.txt  ->  containing exactly beta

If the branches shared a disk, or if a verifier inspected the other attempt's
branch, at least one verdict must come out FAIL: the inspector would find the
wrong bytes. All PASS is the evidence that per-branch isolation and per-attempt
verifier state both hold under real concurrency.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from model_host import ChatClient, ModelHost
from verifier_agent import BranchVerifierHost
from rsi import Protocol
from vm_client import VMClient

KEY = os.environ.get("RSI_API_KEY", "").strip()
if not KEY:
    raise SystemExit("set RSI_API_KEY")
BASE = os.environ.get("RSI_BASE_URL", "http://100.64.0.88:7864/v1")
MODEL = os.environ.get("RSI_MODEL", "deepseek-v4.1-flash")
IMAGE = os.environ.get("RSI_VM_BASE", "/home/yichen/vm-prep.qcow2")
WORKERS = int(os.environ.get("RSI_WAVE_WORKERS", "2"))

WORD = {"alpha": "alpha", "beta": "beta"}
TASKS = [
    "Create /root/proof.txt containing exactly the text alpha (no trailing "
    "newline), then report the file's size in bytes. Declare done with "
    "machine-run checks.",
    "Create /root/proof.txt containing exactly the text beta (no trailing "
    "newline), then report the file's size in bytes. Declare done with "
    "machine-run checks.",
]

report = {"model": MODEL, "image": IMAGE, "workers": WORKERS, "tasks": TASKS}
started = time.time()

chat = ChatClient(BASE, KEY, MODEL, timeout=300)
vm_client = VMClient(chat, IMAGE, max_commands=12)
model_host = ModelHost(vm_client, actor_max_tokens=2048)
host = BranchVerifierHost(model_host, vm_client, chat)
# One seeded lesson: a wave in the real flow follows some experience, and an
# empty memory is a separate (untested here) consolidation edge.
SEED = [{"condition": "a file was just written",
         "action": "read it back with od and stat",
         "outcome": "the exact bytes and size were confirmed",
         "support": "independent read of the written file",
         "verdict": "PASS", "source_task": "seed"}]
protocol = Protocol(host, memory=SEED)

try:
    verdicts = protocol.broad_wave(TASKS, workers=WORKERS)
    report["verdicts"] = [str(verdict) for verdict in verdicts]
    report["answers"] = protocol.last_wave_answers
    report["checks"] = vm_client.check_reports
finally:
    vm_client.close_all()

report["wall_seconds"] = round(time.time() - started, 1)
report["role_calls"] = dict(chat.calls)
report["branches_released"] = len(vm_client.completed_branches)
report["overlay_bytes"] = vm_client.completed_branches
report["inspections"] = [inspection.as_dict() for inspection in host.inspections]
report["holds"] = (
    report["verdicts"] == ["PASS", "PASS"]
    and len(report["inspections"]) == len(TASKS)
    and all(item["accept"] for item in report["inspections"])
)

out = Path(__file__).resolve().parents[1] / "vm-parallel-report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
print(json.dumps(report, ensure_ascii=False)[:2500])
