"""End-to-end: the Actor works inside a disposable VM, the Verifier judges.

Task: create /root/proof.txt with exact content and report its sha256.
The Actor can only act through {"vm_run": ...}; each attempt gets a fresh
overlay, so the file exists only inside that branch's disk.
"""
import json
import os
import sys
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

TASK = ("Create /root/proof.txt containing exactly the text hello-rsi "
        "(no trailing newline), then report the file's sha256 checksum. "
        "Declare done with machine-run checks.")

chat = ChatClient(BASE, KEY, MODEL, timeout=300)
vm_client = VMClient(chat, IMAGE, max_commands=8)
model_host = ModelHost(vm_client, actor_max_tokens=2048)
host = BranchVerifierHost(model_host, vm_client, chat)
protocol = Protocol(host)

candidate, verdict = protocol._attempt(TASK, [])
# _attempt alone does not own the branch; the protocol's wave/deep paths
# release after commit. Do the same here so the VM and its overlay go away.
protocol._release_candidate(candidate)
report = {
    "model": MODEL,
    "image": IMAGE,
    "task": TASK,
    "answer": candidate.public.get("answer"),
    "verifier_verdict": str(verdict),
    "role_calls": dict(chat.calls),
    "branches_released": len(vm_client.completed_branches),
    "overlay_bytes": vm_client.completed_branches,
    "checks": vm_client.last_check_report,
    "inspection": (host.last_inspection.as_dict()
                   if host.last_inspection else None),
}
out = Path(__file__).resolve().parents[1] / "vm-actor-report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
print(json.dumps(report, ensure_ascii=False))
