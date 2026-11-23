import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from unified import Verification, run_unified


class Host:
    def __init__(self, verdicts, routes, practice=()):
        self.verdicts, self.routes, self.decisions = iter(verdicts), iter(routes), iter(practice)
        self.attempts, self.released, self.actions, self.checks = [], [], [], []

    def new_curriculum(self, target):
        return object()

    def new_attempt(self, target, memory):
        attempt = object()
        self.attempts.append(attempt)
        return attempt

    def actor_work(self, attempt, target, memory, feedback):
        self.actions.append((attempt, target, list(memory), feedback))
        return object()

    def verify(self, attempt, target, candidate, feedback):
        self.checks.append((attempt, candidate, feedback))
        return Verification(next(self.verdicts), "observable evidence")

    def route(self, curriculum, target, verification):
        return (next(self.routes), "routing evidence")

    def learn(self, attempt, target, candidate, verification, memory):
        return memory + ["learned"]

    def practice_decision(self, curriculum, target, verification, memory):
        return next(self.decisions)

    def practice(self, curriculum, project, memory):
        return memory + [project]

    def release(self, attempt):
        self.released.append(attempt)


class UnifiedTests(unittest.TestCase):
    def test_revise_keeps_attempt_and_pass_requires_handoff(self):
        h = Host(["FAIL", "PASS"], ["REVISE", "HANDOFF"])
        r = run_unified(h, "target", [])
        self.assertEqual(r.status, "PASS")
        self.assertEqual(len(h.attempts), 1)
        self.assertIs(h.actions[0][0], h.actions[1][0])
        self.assertEqual(h.actions[1][3], "observable evidence")
        self.assertEqual(h.released, h.attempts)

    def test_verify_more_keeps_candidate_and_verifier_attempt(self):
        h = Host(["PASS", "PASS"], ["VERIFY_MORE", "HANDOFF"])
        r = run_unified(h, "target", [])
        self.assertEqual(r.status, "PASS")
        self.assertEqual(len(h.actions), 1)
        self.assertIs(h.checks[0][0], h.checks[1][0])
        self.assertIs(h.checks[0][1], h.checks[1][1])
        self.assertEqual(h.checks[1][2], "routing evidence")

    def test_evolve_practices_until_ready_before_fresh_target(self):
        h = Host(["FAIL", "PASS"], ["EVOLVE", "HANDOFF"],
                 [("PROJECT", "one"), ("PROJECT", "two"), ("READY_FOR_RETRY", "ready")])
        r = run_unified(h, "target", ["seed"])
        self.assertEqual(r.status, "PASS")
        self.assertEqual(r.memory, ["seed", "learned", "one", "two"])
        self.assertEqual(len(h.attempts), 2)
        self.assertEqual([x[1] for x in h.actions], ["target", "target"])
        self.assertEqual(h.actions[1][2], r.memory)
        self.assertEqual(h.released, h.attempts)

    def test_budget_and_invalid_routes_do_not_claim_pass(self):
        h = Host(["PASS"], ["VERIFY_MORE"])
        self.assertEqual(run_unified(h, "t", [], max_verifications=1).status,
                         "BUDGET_EXHAUSTED")
        self.assertEqual(h.released, h.attempts)
        h = Host(["FAIL"], ["HANDOFF"])
        with self.assertRaisesRegex(RuntimeError, "invalid Curriculum"):
            run_unified(h, "t", [])
        self.assertEqual(h.released, h.attempts)

    def test_unverified_stops_without_learning_or_route(self):
        h = Host(["UNVERIFIED"], [])
        r = run_unified(h, "t", ["seed"])
        self.assertEqual(r.status, "UNVERIFIED")
        self.assertEqual(r.memory, ["seed"])
        self.assertEqual(h.released, h.attempts)


if __name__ == "__main__":
    unittest.main()
