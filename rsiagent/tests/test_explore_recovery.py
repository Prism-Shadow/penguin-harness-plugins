"""ExploreRun-level recovery: a restarted run must not redo the Actor's work."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import explore
import practice_journal as pj

TARGET = "fix the off-by-one in the day calculator"


class Host:
    """Same shape as the protocol test double; counts what actually ran."""

    def __init__(self, verdicts=("PASS",), crash_on_consolidate=False, practices=0):
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
        return "practice task {}".format(self.reviews) if self.reviews <= self._practices else None


class ExploreRecoveryTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_a_crash_during_consolidation_is_finished_without_the_actor(self):
        crashed = Host(verdicts=("PASS",), crash_on_consolidate=True)
        first = explore.ExploreRun(None, host=crashed, journal_dir=self.dir)
        with self.assertRaises(RuntimeError):
            first.deep(TARGET)
        self.assertEqual(len(crashed.actor_calls), 1)

        alive = Host(verdicts=())
        second = explore.ExploreRun(None, host=alive, journal_dir=self.dir)
        verdict = second.deep(TARGET)

        self.assertEqual(verdict, "PASS")
        self.assertEqual(alive.actor_calls, [],
                         "a restarted run must not re-run the Actor")
        self.assertEqual(alive.consolidations, [(TARGET, "PASS")])
        self.assertEqual(second.record.entries[-1]["kind"], "deep_recovered")

    def test_a_finished_run_is_not_repeated(self):
        first = explore.ExploreRun(None, host=Host(verdicts=("PASS",)),
                                   journal_dir=self.dir)
        self.assertEqual(first.deep(TARGET), "PASS")

        alive = Host(verdicts=("FAIL",))
        second = explore.ExploreRun(None, host=alive, journal_dir=self.dir)
        self.assertEqual(second.deep(TARGET), "PASS")
        self.assertEqual(alive.actor_calls, [], "a complete project ran again")

    def test_a_budget_stop_is_not_replayed_as_progress(self):
        first = explore.ExploreRun(None, host=Host(verdicts=("FAIL",), practices=99),
                                   journal_dir=self.dir)
        self.assertEqual(first.deep(TARGET), "BUDGET_EXHAUSTED")

        # The run spent its budget on the second attempt, whose verdict was
        # PASS; the recorded verdict is reported as-is, never re-derived.
        alive = Host(verdicts=())
        second = explore.ExploreRun(None, host=alive, journal_dir=self.dir)
        self.assertEqual(second.deep(TARGET), "PASS")
        self.assertEqual(alive.actor_calls, [])

    def test_without_a_journal_directory_nothing_is_written(self):
        run = explore.ExploreRun(None, host=Host(verdicts=("PASS",)))
        self.assertEqual(run.deep(TARGET), "PASS")
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_the_journal_names_the_target_and_stays_a_safe_file_name(self):
        run = explore.ExploreRun(None, host=Host(verdicts=("PASS",)),
                                 journal_dir=self.dir)
        run.deep(TARGET)
        files = list(self.dir.iterdir())
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].name.startswith("deep-"))
        self.assertNotIn("/", files[0].name)
        self.assertEqual(pj.admit(files[0], target=TARGET).kind,
                         pj.ALREADY_COMPLETE)


if __name__ == "__main__":
    unittest.main()
