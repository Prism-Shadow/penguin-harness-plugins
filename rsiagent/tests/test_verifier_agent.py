"""Verifier agent tests: verdict rules, findings as data, probe guard, punch list."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import verifier_agent as va


class FakeClone:
    instances = []

    def __init__(self, base, **kwargs):
        self.outputs = {}
        self.commands = []
        self.closed = False
        FakeClone.instances.append(self)

    def start(self):
        return self

    def run(self, command, timeout=None):
        self.commands.append(command)
        return 0, self.outputs.get(command, "PASS")

    def close(self):
        self.closed = True


class ScriptedClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.calls = {}

    def complete(self, role, system, user, **kwargs):
        self.calls[role] = self.calls.get(role, 0) + 1
        self.prompts.append((role, user))
        if not self.replies:
            raise AssertionError("unexpected extra completion")
        return self.replies.pop(0)


class FindingsTests(unittest.TestCase):
    def test_items_are_cleaned_and_partial_becomes_could_not_confirm(self):
        findings = va.Findings("text", "doubt", [
            {"req": "a", "status": "met", "evidence": "ok"},
            {"req": "b", "status": "partially-met"},
            {"req": "", "status": "met"},
            {"req": "c", "status": "bogus"},
        ])
        self.assertEqual(findings.items,
                         (("a", "met", "ok"), ("b", "could-not-confirm", "")))

    def test_carry_findings_keeps_side_channels(self):
        source = va.Findings("old", "a doubt", [{"req": "r", "status": "violated"}])
        moved = va.carry_findings("new text", source)
        self.assertEqual(str(moved), "new text")
        self.assertEqual(moved.doubts, "a doubt")
        self.assertEqual(moved.items, (("r", "violated", ""),))

    def test_punchlist_orders_violated_first(self):
        findings = va.Findings("", "", [
            {"req": "settled", "status": "met"},
            {"req": "unclear", "status": "could-not-confirm"},
            {"req": "broken", "status": "violated", "evidence": "ls shows nothing"},
        ])
        text = va.items_punchlist(findings)
        self.assertLess(text.index("VIOLATED"), text.index("COULD NOT CONFIRM"))
        self.assertIn("1 other requirement(s) it judged met", text)

    def test_empty_punchlist_when_nothing_unsettled(self):
        findings = va.Findings("", "", [{"req": "a", "status": "met"}])
        self.assertEqual(va.items_punchlist(findings), "")


class ProbeGuardTests(unittest.TestCase):
    def test_read_only_accepted_and_mutating_rejected(self):
        self.assertTrue(va.probe_ok("ls -l /root/proof.txt"))
        self.assertFalse(va.probe_ok("rm -rf /root/x"))
        self.assertFalse(va.probe_ok(""))

    def test_informative_rules(self):
        self.assertTrue(va.probe_informative("PASS"))
        self.assertFalse(va.probe_informative(""))
        self.assertFalse(va.probe_informative("command timed out"))
        self.assertFalse(va.probe_informative("[channel error 500]"))
        self.assertFalse(va.probe_informative("bash: foo: command not found"))
        self.assertFalse(va.probe_informative("ls: cannot access '/x': No such file"))


class VerifyTests(unittest.TestCase):
    def setUp(self):
        FakeClone.instances = []
        self._orig = va.verifier_runtime.clone_overlay
        va.verifier_runtime.clone_overlay = lambda branch, **kw: FakeClone("b", **kw)

    def tearDown(self):
        va.verifier_runtime.clone_overlay = self._orig

    def _branch(self):
        return type("Branch", (), {"overlay": "/tmp/x/disk.qcow2"})()

    def test_pass_without_doubts_accepts(self):
        client = ScriptedClient(['{"verdict":"pass","findings":"all met","doubts":"",'
                                 '"items":[{"req":"file","status":"met"}]}'])
        result = va.verify_candidate("task", self._branch(), client)
        self.assertEqual(result.verdict, "PASS")
        self.assertTrue(result.accept)

    def test_pass_with_doubts_does_not_accept(self):
        client = ScriptedClient(['{"verdict":"pass","findings":"x","doubts":"size unclear"}'])
        result = va.verify_candidate("task", self._branch(), client)
        self.assertFalse(result.accept)

    def test_wrong_never_accepts(self):
        client = ScriptedClient(['{"verdict":"wrong","findings":"missing file"}'])
        result = va.verify_candidate("task", self._branch(), client)
        self.assertFalse(result.accept)
        self.assertEqual(result.verdict, "WRONG")

    def test_unverified_accepts_only_when_allowed(self):
        reply = '{"verdict":"unverified","findings":"could not probe"}'
        strict = va.verify_candidate("t", self._branch(), ScriptedClient([reply]))
        lenient = va.verify_candidate("t", self._branch(), ScriptedClient([reply]),
                                      accept_unverified=True)
        self.assertFalse(strict.accept)
        self.assertTrue(lenient.accept)

    def test_probes_run_in_the_clone_and_reject_mutations(self):
        clone = FakeClone("b")
        clone.outputs["ls /root"] = "proof.txt"
        va.verifier_runtime.clone_overlay = lambda branch, **kw: clone
        client = ScriptedClient([
            '{"probes":["ls /root","rm -rf /root"]}',
            '{"verdict":"pass","findings":"ok"}'])
        result = va.verify_candidate("task", self._branch(), client)
        self.assertEqual(clone.commands, ["ls /root"])
        self.assertEqual(result.probes, ("ls /root",))
        self.assertIn("READ-ONLY", client.prompts[1][1])
        self.assertTrue(clone.closed)

    def test_budget_exhaustion_is_unverified_not_pass(self):
        client = ScriptedClient(['{"probes":["ls /root"]}'] * 3)
        result = va.verify_candidate("task", self._branch(), client, max_rounds=3)
        self.assertEqual(result.verdict, "UNVERIFIED")
        self.assertFalse(result.accept)


if __name__ == "__main__":
    unittest.main()
