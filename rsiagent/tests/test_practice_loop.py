"""Practice-loop resilience tests: liveness vs verdict vs infrastructure."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import practice_loop as pl


class LivenessTests(unittest.TestCase):
    def test_short_actionless_run_does_not_recover(self):
        state = pl.PracticeState()
        for _ in range(pl.MAX_ACTIONLESS_SEGMENTS):
            self.assertIsNone(state.note_actionless())
        self.assertEqual(state.actionless, pl.MAX_ACTIONLESS_SEGMENTS)

    def test_actionless_beyond_allowance_asks_for_recovery(self):
        state = pl.PracticeState()
        for _ in range(pl.MAX_ACTIONLESS_SEGMENTS):
            state.note_actionless()
        directive = state.note_actionless()
        self.assertIn("recover", directive)
        self.assertEqual(state.actionless_recoveries, 1)
        self.assertEqual(state.actionless, 0)

    def test_productive_segment_resets_the_counter(self):
        state = pl.PracticeState()
        state.note_actionless()
        state.note_actionless()
        state.note_product("vm_run")
        self.assertEqual(state.actionless, 0)
        self.assertEqual(state.productive_segments, 1)

    def test_productive_segment_resets_infra_allowance(self):
        state = pl.PracticeState()
        state.note_infrastructure(RuntimeError("vm hiccup"))
        state.note_product()
        state.note_infrastructure(RuntimeError("vm hiccup"))
        self.assertEqual(state.infra_retries, 1)


class InfrastructureTests(unittest.TestCase):
    def test_retries_within_allowance(self):
        state = pl.PracticeState()
        for _ in range(pl.MAX_INFRA_RETRIES):
            self.assertEqual(state.note_infrastructure(RuntimeError("timeout")), "retry")

    def test_exhausted_allowance_raises_infrastructure_error(self):
        state = pl.PracticeState()
        for _ in range(pl.MAX_INFRA_RETRIES):
            state.note_infrastructure(RuntimeError("timeout"))
        with self.assertRaises(pl.PracticeInfrastructureError):
            state.note_infrastructure(RuntimeError("timeout"))

    def test_classification(self):
        state = pl.PracticeState()
        self.assertEqual(state.classify(pl.PracticeInfrastructureError("x")), "infrastructure")
        self.assertEqual(state.classify(TimeoutError("x")), "infrastructure")
        self.assertEqual(state.classify(ConnectionError("x")), "infrastructure")
        self.assertEqual(state.classify(pl.PracticeBoundaryError("x")), "boundary")
        self.assertEqual(state.classify(ValueError("x")), "boundary")


class BudgetTests(unittest.TestCase):
    def test_actions_and_verifications_are_bounded(self):
        budget = pl.PracticeBudget(max_actions=2, max_verifications=1)
        self.assertTrue(budget.spend_action())
        self.assertTrue(budget.spend_action())
        self.assertFalse(budget.spend_action())
        self.assertTrue(budget.spend_verification())
        self.assertFalse(budget.spend_verification())
        self.assertTrue(budget.exhausted)

    def test_budget_stop_is_not_success(self):
        state = pl.PracticeState()
        state.note_product()
        budget = pl.PracticeBudget(max_actions=1)
        budget.spend_action()
        self.assertEqual(pl.stop_reason(state, budget), "BUDGET_EXHAUSTED")

    def test_infrastructure_stop_is_not_a_task_verdict(self):
        state = pl.PracticeState()
        state.infra_retries = 2
        budget = pl.PracticeBudget()
        self.assertEqual(pl.stop_reason(state, budget), "INFRASTRUCTURE")

    def test_no_reason_when_work_continues(self):
        state = pl.PracticeState()
        state.note_product()
        self.assertIsNone(pl.stop_reason(state, pl.PracticeBudget()))


if __name__ == "__main__":
    unittest.main()
