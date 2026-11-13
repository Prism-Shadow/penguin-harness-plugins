"""Verifier runtime tests: clone isolation, one boot per batch, verdict rules."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import verifier_runtime


class FakeClone:
    instances = []

    def __init__(self, base, **kwargs):
        self.base = base
        self.kwargs = kwargs
        self.commands = []
        self.outputs = {}
        self.closed = False
        FakeClone.instances.append(self)

    def start(self):
        return self

    def run(self, command, timeout=None):
        self.commands.append(command)
        return 0, self.outputs.get(command, "PASS")

    def close(self):
        self.closed = True


class VerifierRuntimeTests(unittest.TestCase):
    def setUp(self):
        FakeClone.instances = []
        self._orig = verifier_runtime.VMBranch
        verifier_runtime.VMBranch = FakeClone

    def tearDown(self):
        verifier_runtime.VMBranch = self._orig

    def _branch(self):
        return type("Branch", (), {"overlay": "/tmp/rsi-vm-x/disk.qcow2"})()

    def test_clone_boots_from_the_branch_overlay(self):
        clone = verifier_runtime.clone_overlay(self._branch(), boot_timeout=99)
        self.assertEqual(clone.base, "/tmp/rsi-vm-x/disk.qcow2")
        self.assertEqual(clone.kwargs.get("boot_timeout"), 99)
        clone.close()

    def test_branch_without_overlay_is_refused(self):
        with self.assertRaises(verifier_runtime.VerifierRuntimeError):
            verifier_runtime.clone_overlay(object())

    def test_probe_runs_then_the_clone_is_discarded(self):
        code, output = verifier_runtime.run_probe(self._branch(), "ls /root")
        self.assertEqual(code, 0)
        self.assertEqual(output, "PASS")
        self.assertTrue(FakeClone.instances[0].closed)

    def test_batch_uses_one_clone_and_honours_fail(self):
        def make(*args, **kwargs):
            clone = FakeClone(*args, **kwargs)
            clone.outputs["test -f /root/a"] = "PASS"
            clone.outputs["test -f /root/b"] = "FAIL missing"
            return clone
        verifier_runtime.VMBranch = make
        report = verifier_runtime.verify_candidate(
            self._branch(), ["test -f /root/a", "test -f /root/b"])
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(len(FakeClone.instances), 1, "one boot per batch")
        self.assertEqual(len(report["results"]), 2)

    def test_batch_all_pass(self):
        report = verifier_runtime.verify_candidate(self._branch(), ["a", "b"])
        self.assertEqual(report["verdict"], "PASS")

    def test_silent_probe_is_unverified_not_pass(self):
        def make(*args, **kwargs):
            clone = FakeClone(*args, **kwargs)
            clone.outputs = {"silent": "nothing at all"}
            return clone
        verifier_runtime.VMBranch = make
        report = verifier_runtime.verify_candidate(self._branch(), ["silent"])
        self.assertEqual(report["verdict"], "UNVERIFIED")

    def test_verdict_rules(self):
        self.assertEqual(verifier_runtime.verdict_from("PASS"), "PASS")
        self.assertEqual(verifier_runtime.verdict_from("PASS then FAIL"), "FAIL")
        self.assertEqual(verifier_runtime.verdict_from(""), "UNVERIFIED")
        self.assertEqual(verifier_runtime.verdict_from("OK"), "UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
