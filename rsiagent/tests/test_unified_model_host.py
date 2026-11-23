import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from unified_model_host import UnifiedModelHost
from unified import run_unified


class Client:
    def __init__(self):
        self.calls = []
        self.actor_count = 0
        self.verifier_count = 0

    def complete(self, role, system, user, **kwargs):
        history = kwargs.get("history")
        before = list(history or [])
        self.calls.append((role, user, before, id(history)))
        if role == "actor":
            self.actor_count += 1
            reply = json.dumps({"answer": "answer", "evidence": "evidence",
                                "private": "actor-private"})
        elif role == "verifier":
            self.verifier_count += 1
            reply = json.dumps({"verdict": "FAIL" if self.verifier_count == 1 else "PASS",
                                "report": "public verification report"})
        else:
            reply = json.dumps({"decision": "REVISE" if self.verifier_count == 1 else "HANDOFF",
                                "report": "route report"})
        if history is not None:
            history.extend([{"role": "user", "content": user},
                            {"role": "assistant", "content": reply}])
        return reply


class UnifiedModelHostTests(unittest.TestCase):
    def test_learning_uses_distillation_prompt_and_existing_actor_history(self):
        from types import SimpleNamespace
        from unified import Verification
        class LearningClient:
            def __init__(self):
                self.args = None
            def complete(self, role, system, user, **kwargs):
                self.args = role, system, user, kwargs["history"]
                return json.dumps({"condition":"when needed", "action":"inspect",
                                   "outcome":"failed", "support":"verifier report"})
        client = LearningClient()
        host = UnifiedModelHost(client)
        attempt = SimpleNamespace(actor=[{"role":"assistant", "content":"prior response"}])
        memory = host.learn(attempt, "target", None,
                            Verification("FAIL", "concrete failure"), [])
        self.assertEqual(client.args[0], "actor")
        self.assertIn("distilling experience", client.args[1])
        self.assertNotIn("Solve the task", client.args[1])
        self.assertIs(client.args[3], attempt.actor)
        self.assertIn("concrete failure", client.args[2])
        self.assertEqual(memory[0]["verdict"], "FAIL")

    def test_practice_learns_from_evidence_in_same_actor_session(self):
        from types import SimpleNamespace
        from unified import Verification
        class StubHost(UnifiedModelHost):
            def __init__(self, verdict):
                self.training_validator = None
                self.attempt = SimpleNamespace(actor=[])
                self.verdict = verdict
                self.released = False
            def new_attempt(self, target, memory):
                return self.attempt
            def actor_work(self, attempt, target, memory, feedback):
                attempt.actor.append("actor context")
                return object()
            def verify(self, attempt, target, candidate, feedback):
                return Verification(self.verdict, "observed evidence")
            def learn(self, attempt, target, candidate, verification, memory):
                self.asserted_attempt = attempt
                self.asserted_report = verification.report
                return memory + ["learned"]
            def release(self, attempt):
                self.released = True
        host = StubHost("FAIL")
        self.assertEqual(host.practice([], "practice", ["seed"]), ["seed", "learned"])
        self.assertIs(host.asserted_attempt, host.attempt)
        self.assertEqual(host.asserted_report, "observed evidence")
        self.assertTrue(host.released)
        host = StubHost("UNVERIFIED")
        with self.assertRaisesRegex(Exception, "verification incomplete"):
            host.practice([], "practice", ["seed"])
        self.assertTrue(host.released)
        self.assertFalse(hasattr(host, "asserted_report"))

    def test_route_prompt_is_constrained_by_verdict(self):
        from unified import Verification
        class RouteClient:
            def __init__(self):
                self.prompts = []
            def complete(self, role, system, user, **kwargs):
                self.prompts.append(user)
                return '{"decision":"HANDOFF","report":"checked"}'
        client = RouteClient()
        host = UnifiedModelHost(client)
        host.route([], "target", Verification("PASS", "checked"))
        host.route([], "target", Verification("FAIL", "not checked"))
        self.assertIn("Allowed decisions for this verdict: HANDOFF or VERIFY_MORE", client.prompts[0])
        self.assertIn("Allowed decisions for this verdict: REVISE or EVOLVE", client.prompts[1])

    def test_revision_reuses_role_histories_without_disclosing_actor_memory(self):
        client = Client()
        result = run_unified(UnifiedModelHost(client), "target", ["memory-secret"])
        self.assertEqual(result.status, "PASS")
        actor = [x for x in client.calls if x[0] == "actor"]
        verifier = [x for x in client.calls if x[0] == "verifier"]
        curriculum = [x for x in client.calls if x[0] == "curriculum"]
        self.assertEqual(actor[0][3], actor[1][3])
        self.assertEqual(verifier[0][3], verifier[1][3])
        self.assertEqual(curriculum[0][3], curriculum[1][3])
        self.assertNotEqual(actor[0][3], verifier[0][3])
        self.assertTrue(actor[1][2] and verifier[1][2] and curriculum[1][2])
        for _, user, history, _ in verifier:
            self.assertNotIn("memory-secret", user + json.dumps(history))
            self.assertNotIn("actor-private", user + json.dumps(history))


if __name__ == "__main__":
    unittest.main()
