"""Behavioral tests for role decisions and truthful verification reporting."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from evidence import container_verifier
from explore import ExploreRun
from model_host import LLMError, ModelHost


class Client:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = {}
        self.requests = []
        self.log = None

    def complete(self, role, system, user, **kwargs):
        self.calls[role] = self.calls.get(role, 0) + 1
        self.requests.append((role, system, user))
        return self.responses.pop(0)


class RoleDecisionTests(unittest.TestCase):
    def test_memory_distills_structured_lesson_and_preserves_failure(self):
        data = {"condition": "input is missing", "action": "check input",
                "outcome": "attempt failed", "support": "verifier reported FAIL"}
        client = Client([json.dumps(data)])
        host = ModelHost(client)
        prior = [{"condition": "old"}]
        candidate = SimpleNamespace(public={"answer": "unknown", "evidence": "no file"})
        updated = host.consolidate(prior, "read file", candidate, "FAIL")
        self.assertEqual(prior, [{"condition": "old"}])
        self.assertEqual(updated[-1]["verdict"], "FAIL")
        self.assertEqual(updated[-1]["source_task"], "read file")
        self.assertEqual(updated[-1]["condition"], data["condition"])
        self.assertIn("Verifier verdict: FAIL", client.requests[0][2])
        self.assertEqual(client.requests[0][0], "actor")

    def test_incomplete_lesson_rejected_without_updating_prior(self):
        # The first malformed reply is restated once; two failures are final.
        client = Client(['{"condition":"x"}', "still not a lesson"])
        host = ModelHost(client)
        prior = ["unchanged"]
        with self.assertRaises(LLMError):
            host.consolidate(prior, "task", SimpleNamespace(public={"answer": "x"}),
                             "FAIL")
        self.assertEqual(prior, ["unchanged"])
        self.assertEqual(client.calls["actor"], 2)

    def test_a_malformed_lesson_is_restated_once_and_then_accepted(self):
        good = {"condition": "file written", "action": "read it back",
                "outcome": "bytes matched", "support": "od output"}
        client = Client(["not JSON at all", json.dumps(good)])
        host = ModelHost(client)
        updated = host.consolidate([], "write a file",
                                   SimpleNamespace(public={"answer": "ok"}), "PASS")
        self.assertEqual(updated[-1]["condition"], "file written")
        self.assertEqual(client.calls["actor"], 2)
        self.assertIn("Your previous reply was unusable", client.requests[1][2])

    def test_the_restatement_count_is_configurable(self):
        client = Client(['{"condition":"x"}', '{"condition":"x"}',
                         '{"condition":"x"}'])
        host = ModelHost(client, memory_attempts=3)
        with self.assertRaises(LLMError):
            host.consolidate([], "t", SimpleNamespace(public={"answer": "x"}), "FAIL")
        self.assertEqual(client.calls["actor"], 3)
        with self.assertRaises(ValueError):
            ModelHost(Client([]), memory_attempts=0)

    def test_actor_context_goes_to_distillation_but_not_verifier(self):
        memory = {"condition": "input", "action": "add", "outcome": "42",
                  "support": "verdict PASS"}
        client = Client(['{"answer":42,"evidence":"calculation","internal":"actor-private-marker"}',
                         "PASS", json.dumps(memory)])
        host = ModelHost(client)
        candidate = host.run_actor("20+22", [])
        verdict = host.verify("20+22", candidate.public)
        host.consolidate([], "20+22", candidate, verdict)
        verifier_user = client.requests[1][2]
        memory_user = client.requests[2][2]
        self.assertNotIn("actor-private-marker", verifier_user)
        self.assertIn("actor-private-marker", memory_user)

    def test_curriculum_ready_and_practice(self):
        client = Client(['{"decision":"ready"}',
                         '{"decision":"practice","task":" practice one "}'])
        host = ModelHost(client)
        self.assertIsNone(host.curriculum_review("target", "PASS", []))
        self.assertEqual(host.curriculum_review("target", "FAIL", []), "practice one")

    def test_curriculum_invalid_decision_fails_closed(self):
        host = ModelHost(Client(['{"decision":"practice","task":""}']))
        with self.assertRaises(LLMError):
            host.curriculum_review("target", "FAIL", [])

    def test_default_report_does_not_claim_container_isolation(self):
        report = ExploreRun(Client()).report()
        deviations = " ".join(report["deviations"])
        self.assertIn("model judgment; no container isolation", deviations)
        self.assertNotIn("Verifier isolation is the restricted Docker", deviations)

    def test_evidence_does_not_mount_host_tmp(self):
        with patch("evidence.verify_in_container", return_value=SimpleNamespace(stdout="PASS\n")) as run:
            self.assertEqual(container_verifier("print('PASS')")("task", {"answer": "x"}), "PASS")
        self.assertIsNone(run.call_args.args[0])

    def test_model_verifier_rejects_ambiguous_verdict(self):
        host = ModelHost(Client(["PASS FAIL"]))
        with self.assertRaises(LLMError):
            host.verify("task", {"answer": "answer"})

    def test_verifier_data_goes_through_stdin_not_command(self):
        with patch("evidence.verify_in_container", return_value=SimpleNamespace(stdout="PASS\n")) as run:
            self.assertEqual(container_verifier("print('PASS')")(
                "private task", {"answer": "private answer"}), "PASS")
        self.assertNotIn("private task", str(run.call_args.args))
        self.assertNotIn("private answer", str(run.call_args.args))
        self.assertEqual(json.loads(run.call_args.kwargs["input_data"]),
                         {"task": "private task", "answer": "private answer"})

    def test_extra_verdict_output_is_rejected(self):
        with patch("evidence.verify_in_container", return_value=SimpleNamespace(stdout="junk\nPASS\n")):
            with self.assertRaises(RuntimeError):
                container_verifier("print('PASS')")("task", {"answer": "x"})


if __name__ == "__main__":
    unittest.main()
