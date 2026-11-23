"""VMClient tests with a fake branch (no QEMU boot)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from model_host import LLMError
from vm_client import VMClient


class FakeBranch:
    instances = []

    def __init__(self, base_image, **kwargs):
        self.base_image = base_image
        self.commands = []
        self.outputs = {}
        self.closed = False
        self.overlay_bytes = 204800
        FakeBranch.instances.append(self)

    def start(self):
        return self

    def run(self, command, timeout=None):
        self.commands.append(command)
        return 0, self.outputs.get(command, "PASS")

    def close(self):
        self.closed = True


class ScriptedClient:
    """Replies in order; records the prompts it saw."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.calls = {}

    def complete(self, role, system, user, max_tokens=512, temperature=0.0,
                 history=None, **kwargs):
        self.calls[role] = self.calls.get(role, 0) + 1
        self.prompts.append((role, user))
        if not self.replies:
            raise AssertionError("unexpected extra completion")
        return self.replies.pop(0)


DONE_WITH_CHECK = '{"answer":"6.5.0","checks":[{"desc":"kernel","probe":"uname -r | grep 6.5"}]}'


class VMClientTests(unittest.TestCase):
    def setUp(self):
        FakeBranch.instances = []

    def _client(self, replies, **kwargs):
        kwargs.setdefault("branch_factory", FakeBranch)
        return VMClient(ScriptedClient(replies), "/base.qcow2", **kwargs)

    def test_command_loop_feeds_output_back(self):
        client = self._client(['{"vm_run":"uname -r"}', DONE_WITH_CHECK])
        history = []
        reply = client.complete("actor", "solve", "task", history=history)
        self.assertIn('"answer"', reply)
        branch = FakeBranch.instances[0]
        self.assertEqual(branch.commands, ["uname -r", "uname -r | grep 6.5"])
        self.assertIn("ran:uname -r" if False else "Exit code: 0",
                      client.client.prompts[1][1])
        self.assertEqual(client.client.calls["actor"], 2)
        self.assertTrue(client.last_check_report["results"][0]["passed"])

    def test_command_budget_is_bounded(self):
        client = self._client(['{"vm_run":"true"}', '{"vm_run":"true"}'],
                              max_commands=1)
        with self.assertRaises(LLMError):
            client.complete("actor", "solve", "task", history=[])

    def test_non_actor_roles_pass_through(self):
        client = self._client(['PASS'])
        self.assertEqual(client.complete("verifier", "s", "u", history=None), "PASS")
        self.assertEqual(FakeBranch.instances, [])

    def test_release_closes_the_branch_and_frees_the_disk(self):
        client = self._client([DONE_WITH_CHECK])
        history = []
        client.complete("actor", "s", "u", history=history)
        branch = FakeBranch.instances[0]
        client.release_history(history)
        self.assertTrue(branch.closed)
        self.assertEqual(client.completed_branches, [204800])

    def test_each_attempt_gets_its_own_branch(self):
        client = self._client([DONE_WITH_CHECK, DONE_WITH_CHECK])
        client.complete("actor", "s", "u", history=[])
        client.complete("actor", "s", "u", history=[])
        self.assertEqual(len(FakeBranch.instances), 2)

    def test_bad_vm_run_payload_is_refused(self):
        client = self._client(['{"vm_run":""}'])
        with self.assertRaises(LLMError):
            client.complete("actor", "s", "u", history=[])

    def test_a_non_action_reply_cannot_become_a_done(self):
        """A garbled echo of the command output is not an answer."""
        echo = '{"exit_code": 0, "stdout": "5\\n616c 7068 61  alpha\\n"}'
        client = self._client([echo, DONE_WITH_CHECK])
        reply = client.complete("actor", "solve", "task", history=[])
        self.assertIn('"answer"', reply)
        self.assertIn("neither a shell request nor a done declaration",
                      client.client.prompts[1][1])
        self.assertEqual(client.client.calls["actor"], 2)

    def test_a_run_of_non_actions_exhausts_the_turns_instead_of_answering(self):
        echo = '{"exit_code": 0, "stdout": "nothing"}'
        client = self._client([echo, echo, echo], max_commands=2)
        with self.assertRaises(LLMError):
            client.complete("actor", "solve", "task", history=[])

    def test_without_require_checks_a_text_answer_is_still_allowed(self):
        client = self._client(["the answer is 42"], require_checks=False)
        self.assertEqual(client.complete("actor", "solve", "task", history=[]),
                         "the answer is 42")

    def test_actor_requires_a_history_list(self):
        client = self._client([])
        with self.assertRaises(LLMError):
            client.complete("actor", "s", "u", history=None)

    # --- model-authored checks ------------------------------------------
    def test_failed_check_sends_the_actor_back_to_work(self):
        class Flaky(FakeBranch):
            """The probe fails once, then passes after the Actor fixes the work."""
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.runs = 0

            def run(self, command, timeout=None):
                self.commands.append(command)
                self.runs += 1
                return 0, ("FAIL" if self.runs == 1 else "PASS")

        client = self._client([DONE_WITH_CHECK, DONE_WITH_CHECK],
                              branch_factory=Flaky)
        reply = client.complete("actor", "s", "u", history=[])
        self.assertIn('"answer"', reply)
        self.assertIn("checks failed", client.client.prompts[1][1].lower())
        self.assertTrue(client.last_check_report["results"][0]["passed"])

    def test_actor_that_never_passes_its_checks_is_stopped(self):
        class AlwaysFails(FakeBranch):
            def run(self, command, timeout=None):
                self.commands.append(command)
                return 0, "FAIL"

        client = self._client([DONE_WITH_CHECK] * 20, max_commands=2,
                              branch_factory=AlwaysFails)
        with self.assertRaises(LLMError):
            client.complete("actor", "s", "u", history=[])

    def test_rejected_check_is_reported_back(self):
        client = self._client([
            '{"answer":"done","checks":[{"desc":"mutates","probe":"rm -rf /root/x"}]}',
            DONE_WITH_CHECK,
        ])
        client.complete("actor", "s", "u", history=[])
        second_prompt = client.client.prompts[1][1]
        self.assertIn("rejected", second_prompt.lower())
        self.assertEqual(client.last_check_report["rejected"], [])

    def test_checks_are_required_by_default(self):
        client = self._client(['{"answer":"done"}', DONE_WITH_CHECK])
        client.complete("actor", "s", "u", history=[])
        self.assertIn("without checks", client.client.prompts[1][1])

    def test_checks_can_be_waived(self):
        client = self._client(['{"answer":"done"}'], require_checks=False)
        self.assertEqual(client.complete("actor", "s", "u", history=[]), '{"answer":"done"}')


if __name__ == "__main__":
    unittest.main()
