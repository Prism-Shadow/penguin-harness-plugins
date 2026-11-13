"""Protocol-level recovery: the journal is written by deep() and read by recover().

The bug this guards against is a resumed run that repeats Actor work, or claims
a verdict it never computed. Each test asserts on the calls the host actually
received, not just on the returned verdict.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import practice_journal as pj
from rsi import Protocol

TARGET = "fix the off-by-one in the day calculator"


class Host:
    """A host that can be made to fail at a chosen point."""

    def __init__(self, verdicts=("FAIL",), crash_on_consolidate=False,
                 practices=2):
        self.verdicts = list(verdicts)
        self.actor_calls = []
        self.consolidations = []
        self.reviews = 0
        self.crash_on_consolidate = crash_on_consolidate
        self._practices = practices

    def run_actor(self, task, memory):
        self.actor_calls.append(task)
        return SimpleNamespace(public={"task": task, "answer": "a"})

    def verify(self, task, public):
        return self.verdicts.pop(0) if self.verdicts else "PASS"

    def consolidate(self, memory, task, candidate, verdict):
        if self.crash_on_consolidate:
            self.crash_on_consolidate = False
            raise RuntimeError("the guest died during consolidation")
        self.consolidations.append((task, str(verdict)))
        return memory + [task]

    def curriculum_review(self, task, verdict, memory):
        self.reviews += 1
        if self.reviews > self._practices:
            return None
        return "practice task {}".format(self.reviews)


class JournalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "journal.json"

    def tearDown(self):
        self._dir.cleanup()

    def _journal(self):
        return pj.Journal(self.path, TARGET).start()

    def test_a_finished_run_journals_a_closed_project(self):
        host = Host(verdicts=("PASS",), practices=0)
        protocol = Protocol(host)
        journal = self._journal()
        self.assertEqual(protocol.deep(TARGET, 2, journal=journal), "PASS")
        kinds = [entry.kind for entry in journal.entries()]
        self.assertEqual(kinds, [pj.PRACTICE_STARTED, pj.VERDICT_SAVED,
                                 pj.LEARNING_COMMITTED, pj.PROJECT_CLOSED])
        self.assertEqual(pj.admit(self.path, target=TARGET).kind,
                         pj.ALREADY_COMPLETE)

    def test_a_crash_before_learning_is_recovered_without_repeating_the_actor(self):
        host = Host(verdicts=("PASS",), crash_on_consolidate=True, practices=0)
        protocol = Protocol(host)
        journal = self._journal()
        with self.assertRaises(RuntimeError):
            protocol.deep(TARGET, 0, journal=journal)

        admission = pj.admit(self.path, target=TARGET)
        self.assertEqual(admission.kind, pj.REDO_LEARNING)
        self.assertEqual(len(host.actor_calls), 1)

        alive = Host(verdicts=(), practices=0)
        recovered = Protocol(alive)
        result = recovered.recover(TARGET, journal)

        self.assertEqual(result["kind"], pj.REDO_LEARNING)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(alive.actor_calls, [],
                         "recovery must not run the Actor again")
        self.assertEqual(alive.consolidations, [(TARGET, "PASS")],
                         "learning may commit exactly once")
        self.assertEqual(pj.admit(self.path, target=TARGET).kind,
                         pj.ALREADY_COMPLETE)

    def test_recovery_is_idempotent(self):
        host = Host(verdicts=("PASS",), crash_on_consolidate=True, practices=0)
        with self.assertRaises(RuntimeError):
            Protocol(host).deep(TARGET, 0, journal=self._journal())
        journal = pj.Journal(self.path, TARGET)
        recovered = Protocol(Host(verdicts=(), practices=0))
        recovered.recover(TARGET, journal)
        again = recovered.recover(TARGET, journal)
        self.assertEqual(again["kind"], pj.ALREADY_COMPLETE)
        self.assertEqual(recovered.host.actor_calls, [])

    def test_budget_exhaustion_is_recorded_and_never_replayed(self):
        host = Host(verdicts=("FAIL",), practices=99)
        protocol = Protocol(host)
        journal = self._journal()
        self.assertEqual(protocol.deep(TARGET, 1, journal=journal),
                         "BUDGET_EXHAUSTED")
        self.assertTrue(journal.has(pj.BUDGET_STOP))
        admission = pj.admit(self.path, target=TARGET)
        self.assertEqual(admission.kind, pj.STOPPED)
        self.assertFalse(admission.runs_steps)
        self.assertFalse(admission.runs_learning)

    def test_practice_steps_are_counted_and_resume_honours_them(self):
        host = Host(verdicts=("FAIL", "PASS"), practices=1)
        protocol = Protocol(host)
        journal = self._journal()
        protocol.deep(TARGET, 2, journal=journal)
        self.assertGreaterEqual(journal.steps_committed(), 1)

        fresh = Host(verdicts=(), practices=0)
        protocol2 = Protocol(fresh)
        protocol2.deep(TARGET, 0, journal=None, resume_from=1)
        self.assertEqual(fresh.actor_calls, [TARGET],
                         "resume_from skips only the counting, never the attempt")

    def test_recover_on_a_fresh_journal_refuses_to_invent_progress(self):
        journal = self._journal()
        host = Host(verdicts=(), practices=0)
        result = Protocol(host).recover(TARGET, journal)
        self.assertEqual(result["kind"], pj.RESTART_PROJECT)
        self.assertEqual(host.actor_calls, [])
        self.assertEqual(host.consolidations, [])


if __name__ == "__main__":
    unittest.main()
