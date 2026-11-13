"""Opt-in real-model role smoke test. Read bearer token from stdin."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from model_host import ChatClient, ModelHost

key = sys.stdin.read().strip()
if not key:
    raise SystemExit("missing bearer token on stdin")
client = ChatClient(os.environ.get("RSI_BASE_URL", "http://100.64.0.88:7864/v1"),
                    key, os.environ.get("RSI_MODEL", "deepseek-v4.1-flash"), timeout=45)
host = ModelHost(client, actor_max_tokens=512)
task = "What is 17 + 25? Return an integer."
candidate = host.run_actor(task, [])
verdict = host.verify(task, candidate.public)
memory = host.consolidate([], task, candidate, verdict)
decision = host.curriculum_review(task, verdict, memory)
required = {"condition", "action", "outcome", "support", "verdict", "source_task"}
if not required.issubset(memory[0]):
    raise SystemExit("missing structured memory fields")
print(json.dumps({"model": client.model, "verdict": verdict,
                  "memory_fields_present": sorted(required),
                  "curriculum": "ready" if decision is None else "practice",
                  "role_calls": client.calls, "limitations":
                  "text-only role smoke; not GDPevo or host-isolation verification"}))
