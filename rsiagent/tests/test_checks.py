"""Checks module: the ported falsifiability gate and machine execution."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import checks


class FakeBranch:
    def __init__(self, outputs=None):
        self.outputs = outputs or {}
        self.ran = []

    def run(self, command, timeout=None):
        self.ran.append(command)
        return 0, self.outputs.get(command, "PASS")


class GateTests(unittest.TestCase):
    def _validate(self, probes, **kwargs):
        raw = [{"desc": "c{}".format(i), "probe": p} for i, p in enumerate(probes)]
        return checks.validate(raw, **kwargs)

    def test_read_only_content_probe_is_accepted(self):
        accepted, rejections, dropped = self._validate(["sha256sum /root/proof.txt | grep ee1b"])
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejections, [])

    def test_mutating_probe_is_rejected(self):
        for probe in ["rm -rf /root/x", "mv /root/a /root/b", "sed -i s/a/b/ /root/f",
                      "cp /tmp/staged /root/deliverable", "echo x > /root/f"]:
            accepted, rejections, _ = self._validate([probe])
            self.assertEqual(accepted, [], probe)
            self.assertTrue(rejections, probe)

    def test_scratch_writes_are_allowed(self):
        accepted, rejections, _ = self._validate(
            ["cp /root/proof.txt /tmp/copy && sha256sum /tmp/copy | grep ee1b"])
        self.assertEqual(len(accepted), 1, rejections)

    def test_devnull_redirect_is_harmless(self):
        accepted, rejections, _ = self._validate(
            ["test -f /root/proof.txt 2>/dev/null && echo PASS"])
        self.assertEqual(len(accepted), 1, rejections)

    def test_vacuous_probes_are_rejected(self):
        for probe in ["echo PASS", "true", "printf done"]:
            accepted, rejections, _ = self._validate([probe])
            self.assertEqual(accepted, [], probe)
            self.assertTrue(rejections, probe)

    def test_self_report_probe_is_rejected(self):
        accepted, rejections, _ = self._validate(["grep PASS /root/evidence.txt"])
        self.assertEqual(accepted, [])
        self.assertIn("self-authored report", rejections[0])

    def test_task_named_file_exempts_the_witness_gate(self):
        accepted, rejections, _ = self._validate(
            ["grep -c RESOLVED /root/evidence.txt"],
            instruction="write the summary to /root/evidence.txt")
        self.assertEqual(len(accepted), 1, rejections)

    def test_interpreter_body_mutation_is_rejected(self):
        accepted, rejections, _ = self._validate(
            ["python3 -c \"import shutil; shutil.rmtree('/root/data'); print('PASS')\""])
        self.assertEqual(accepted, [])
        self.assertIn("interpreter-body", rejections[0])

    def test_capacity_evicts_existence_first(self):
        probes = ["test -f /root/a", "ls /root", "sha256sum /root/a | grep abc",
                  "diff /root/a /root/b"]
        accepted, _, dropped = self._validate(probes, max_checks=2)
        self.assertEqual(len(accepted), 2)
        self.assertEqual(len(dropped), 2)
        kept = {c.probe for c in accepted}
        self.assertIn("sha256sum /root/a | grep abc", kept)
        self.assertIn("diff /root/a /root/b", kept)

    def test_empty_list_is_a_soundness_failure(self):
        accepted, rejections, _ = checks.validate([])
        self.assertEqual(accepted, [])
        self.assertTrue(rejections)


class RunTests(unittest.TestCase):
    def test_pass_requires_pass_and_no_fail(self):
        branch = FakeBranch({"a": "PASS", "b": "PASS but FAIL later", "c": "nothing"})
        accepted = [checks.Check("a", "a"), checks.Check("b", "b"), checks.Check("c", "c")]
        results = checks.run_checks(accepted, branch)
        self.assertEqual([r.passed for r in results], [True, False, False])
        self.assertEqual(branch.ran, ["a", "b", "c"])

    def test_summary_names_each_probe(self):
        accepted = [checks.Check("file", "test -f /x")]
        results = checks.run_checks(accepted, FakeBranch({"test -f /x": "FAIL"}))
        text = checks.summarize(results)
        self.assertIn("FAIL", text)
        self.assertIn("file", text)


if __name__ == "__main__":
    unittest.main()
