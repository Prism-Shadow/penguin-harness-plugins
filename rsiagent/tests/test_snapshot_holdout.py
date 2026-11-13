"""Snapshot persistence and explicitly declared held-out task boundaries."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from explore import ExploreRun, run
from rsi import FrozenMemory


class Client:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = {}
        self.log = None

    def complete(self, role, system, user, **kwargs):
        self.calls[role] = self.calls.get(role, 0) + 1
        return self.responses.pop(0)


class Host:
    def __init__(self, practice=None):
        self.tasks = []
        self.practice = practice

    def run_actor(self, task, memory):
        self.tasks.append(task)
        return SimpleNamespace(public={"answer": "ok"})

    def verify(self, task, public):
        return "PASS"

    def consolidate(self, memory, task, candidate, verdict):
        return memory + [{"task": task, "verdict": verdict}]

    def curriculum_review(self, task, verdict, memory):
        return self.practice


class SnapshotHoldoutTests(unittest.TestCase):
    def test_snapshot_round_trip_private_and_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            snapshot = FrozenMemory.create([{"condition": "when x", "outcome": "y"}])
            snapshot.save(path)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            loaded = FrozenMemory.load(path)
            self.assertEqual(loaded.sha256, snapshot.sha256)
            self.assertEqual(loaded.read(), snapshot.read())
            loaded.read()[0]["condition"] = "mutated copy"
            self.assertEqual(loaded.read()[0]["condition"], "when x")
            with self.assertRaises(FileExistsError):
                snapshot.save(path)

    def test_modified_snapshot_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            FrozenMemory.create(["original"]).save(path)
            data = json.loads(path.read_text())
            data["sha256"] = "0" * 64
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                FrozenMemory.load(path)

    def test_target_cannot_be_a_heldout_task(self):
        client = Client()
        with self.assertRaises(ValueError):
            run("domain", "  TEST task ", ["test TASK"], client)
        self.assertEqual(client.calls, {})

    def test_broad_plan_rejects_heldout_before_actor(self):
        client = Client(['{"tasks":["public practice", "heldout task"]}'])
        host = Host()
        experiment = ExploreRun(client, host=host, held_out=["Heldout Task"])
        with self.assertRaises(ValueError):
            experiment.broad("public domain")
        self.assertEqual(host.tasks, [])

    def test_curriculum_cannot_schedule_exact_heldout_practice(self):
        client = Client()
        host = Host(practice=" HELDOUT   task ")
        experiment = ExploreRun(client, host=host, held_out=["heldout task"])
        with self.assertRaises(ValueError):
            experiment.deep("training target")
        self.assertEqual(host.tasks, ["training target"])


if __name__ == "__main__":
    unittest.main()
