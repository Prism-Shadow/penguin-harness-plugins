import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from rsi import Protocol


class Host:
    def __init__(self):
        self.inputs = []
        self.reviewed = 0

    def run_actor(self, task, memory):
        self.inputs.append((task, list(memory)))
        return SimpleNamespace(public={"task": task}, private="secret")

    def verify(self, task, public):
        assert "private" not in public
        return "FAIL" if task == "bad" else "PASS"

    def consolidate(self, memory, task, candidate, verdict):
        return memory + [task]

    def curriculum_review(self, task, verdict, memory):
        self.reviewed += 1
        return "practice" if self.reviewed == 1 else None


class Tests(unittest.TestCase):
    def test_wave_uses_shared_snapshot_and_commits_in_order(self):
        host = Host()
        p = Protocol(host, ["seed"])
        self.assertEqual(p.broad_wave(["a", "b"], workers=2), ["PASS", "PASS"])
        self.assertEqual([m for _, m in host.inputs], [["seed"], ["seed"]])
        self.assertEqual(p.memory, ["seed", "a", "b"])

    def test_failed_verification_blocks_wave(self):
        host = Host()
        host.verify = lambda task, public: "UNVERIFIED"
        p = Protocol(host, ["seed"])
        with self.assertRaises(RuntimeError):
            p.broad_wave(["a"])
        self.assertEqual(p.memory, ["seed"])

    def test_failed_consolidation_does_not_partially_commit_wave(self):
        host = Host()
        def consolidate(memory, task, candidate, verdict):
            if task == "b":
                raise RuntimeError("distillation failed")
            return memory + [task]
        host.consolidate = consolidate
        p = Protocol(host, ["seed"])
        with self.assertRaisesRegex(RuntimeError, "distillation failed"):
            p.broad_wave(["a", "b"])
        self.assertEqual(p.memory, ["seed"])
        self.assertEqual(p.events, [])

    def test_deep_practices_and_retries_target(self):
        host = Host()
        p = Protocol(host)
        self.assertEqual(p.deep("target", 1), "PASS")
        self.assertEqual([task for task, _ in host.inputs],
                         ["target", "practice", "target"])

    def test_frozen_evaluation_does_not_update_memory(self):
        host = Host()
        p = Protocol(host, ["seed"])
        frozen = p.freeze()
        before = frozen.sha256
        p.evaluate("heldout", frozen)
        self.assertEqual(p.memory, ["seed"])
        self.assertEqual(frozen.read(), ["seed"])
        self.assertEqual(frozen.sha256, before)
        self.assertEqual(host.reviewed, 0)


if __name__ == "__main__":
    unittest.main()
