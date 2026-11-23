"""Live check: capture a branch VM's screen and read it with a vision model."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from vm_host import VMBranch
from model_host import ChatClient
import eyes
import imagery

KEY = os.environ.get("RSI_API_KEY", "").strip()
if not KEY:
    raise SystemExit("set RSI_API_KEY")
BASE = os.environ.get("RSI_BASE_URL", "http://100.64.0.88:7864/v1")
MODEL = os.environ.get("RSI_MODEL", "deepseek-v4.1-flash")
IMAGE = os.environ.get("RSI_VM_BASE", "/home/yichen/vm-prep.qcow2")

report = {"image": IMAGE}
with VMBranch(IMAGE, boot_timeout=240) as vm:
    # Put something recognisable on the console first.
    vm.run("printf 'RSI-VISION-MARKER-42\\n'")
    png = eyes.capture(vm)
    out_png = Path(__file__).resolve().parents[1] / "vm-screen.png"
    out_png.write_bytes(png)
    report["png_bytes"] = len(png)
    report["png_path"] = str(out_png)
    client = ChatClient(BASE, KEY, MODEL, timeout=180)
    question = "Quote any text visible on this screenshot. If it is only a login prompt or console, say so."
    try:
        answer = imagery.ask_about_image(client, png, question)
        report["vision_answer"] = answer[:400]
    except Exception as exc:
        report["vision_error"] = str(exc)[:300]

out = Path(__file__).resolve().parents[1] / "vm-screen-report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
print(json.dumps(report, ensure_ascii=False)[:900])
