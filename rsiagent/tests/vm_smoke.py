"""Prove per-branch VM isolation and rollback with the prepared base image.

Branch A writes a marker file; branch B (a fresh overlay on the same base)
must not see it. Both branches boot from the same base, so the only state a
branch keeps is its own overlay, which is deleted on close.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from vm_host import VMBranch

BASE = os.environ.get("RSI_VM_BASE", "/home/yichen/vm-prep.qcow2")
MARKER = "/root/rsi-isolation-marker"

report = {"base": BASE, "branches": []}

t0 = time.time()
with VMBranch(BASE) as a:
    boot_a = time.time() - t0
    code, out = a.run("uname -r")
    code2, host = a.run("hostname")
    a.run("echo branch-a > " + MARKER)
    code3, back = a.run("cat " + MARKER)
    report["branches"].append({
        "name": "A", "boot_seconds": round(boot_a, 1),
        "uname": out, "hostname": host, "exit": [code, code2, code3],
        "marker_written": back, "overlay_bytes": a.overlay_bytes})

t1 = time.time()
with VMBranch(BASE) as b:
    boot_b = time.time() - t1
    code, ls = b.run("ls -l " + MARKER)
    code2, up = b.run("cat /proc/uptime | cut -d' ' -f1")
    report["branches"].append({
        "name": "B", "boot_seconds": round(boot_b, 1),
        "marker_present": code == 0, "ls": ls,
        "uptime_seconds": up, "overlay_bytes": b.overlay_bytes})

report["isolation_holds"] = report["branches"][1]["marker_present"] is False
out_path = Path(__file__).resolve().parents[1] / "vm-isolation-report.json"
out_path.write_text(json.dumps(report, indent=1, ensure_ascii=False))
print(json.dumps({"isolation_holds": report["isolation_holds"],
                  "boot_a_s": report["branches"][0]["boot_seconds"],
                  "boot_b_s": report["branches"][1]["boot_seconds"],
                  "overlay_kib": [report["branches"][0]["overlay_bytes"] // 1024,
                                  report["branches"][1]["overlay_bytes"] // 1024],
                  "uname": report["branches"][0]["uname"],
                  "a_marker": report["branches"][0]["marker_written"]},
                 ensure_ascii=False))
