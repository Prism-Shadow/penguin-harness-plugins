"""Crash injection: kill a practice mid-run, then resume from the journal.

The child process is killed with SIGKILL, which is the same class of event as a
guest VM vanishing: no unwinding, no chance to flush in-memory state. Whatever
the journal holds at that moment is all the next process may believe.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import practice_journal as pj

TARGET = "crash-recovery target"
TOTAL_STEPS = 4

CHILD = textwrap.dedent("""
    import os, signal, sys, time
    sys.path.insert(0, {runtime!r})
    import practice_journal as pj
    path, step_log, crash_at = sys.argv[1], sys.argv[2], int(sys.argv[3])
    journal = pj.Journal(path, {target!r}).start()
    for index in range({total}):
        if index == crash_at:
            os.kill(os.getpid(), signal.SIGKILL)   # no cleanup, no flush
        journal.append(pj.STEP_COMMITTED, index=index)
        with open(step_log, "a", encoding="utf-8") as handle:
            handle.write(str(index) + "\\n")
        time.sleep(0.02)
    journal.append(pj.VERDICT_SAVED, verdict="PASS",
                   history=[{{"role": "user", "content": "t"}},
                            {{"role": "assistant", "content": "a"}}])
""")


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.journal_path = self.dir / "journal.json"
        self.step_log = self.dir / "steps.txt"

    def tearDown(self):
        self._dir.cleanup()

    def _child(self, crash_at):
        script = self.dir / "child.py"
        script.write_text(CHILD.format(
            runtime=str(Path(__file__).resolve().parents[1] / "runtime"),
            target=TARGET, total=TOTAL_STEPS))
        return subprocess.run(
            [sys.executable, str(script), str(self.journal_path),
             str(self.step_log), str(crash_at)],
            capture_output=True, text=True, timeout=60)

    def _steps_logged(self):
        if not self.step_log.exists():
            return []
        return [int(line) for line in self.step_log.read_text().split()]

    def test_a_killed_run_resumes_at_the_first_uncommitted_step(self):
        result = self._child(crash_at=2)
        self.assertEqual(result.returncode, -signal.SIGKILL,
                         "the child was expected to die by SIGKILL")
        self.assertEqual(self._steps_logged(), [0, 1])

        admission = pj.admit(self.journal_path, target=TARGET)
        self.assertEqual(admission.kind, pj.RESUME_PRACTICE)
        self.assertEqual(admission.steps_committed, 2)

        journal = pj.Journal(self.journal_path, TARGET)
        ran = []
        learned = []

        def step(index):
            ran.append(index)
            with open(self.step_log, "a", encoding="utf-8") as handle:
                handle.write(str(index) + "\n")

        pj.replay(admission, step=step, learn=learned.append, journal=journal,
                  total_steps=TOTAL_STEPS)

        self.assertEqual(ran, [2, 3], "only the missing steps may run")
        self.assertEqual(self._steps_logged(), [0, 1, 2, 3],
                         "every step ran exactly once across both processes")
        self.assertEqual(journal.steps_committed(), TOTAL_STEPS)
        self.assertTrue(learned)

    def test_a_killed_run_before_any_step_restarts_from_zero(self):
        result = self._child(crash_at=0)
        self.assertEqual(result.returncode, -signal.SIGKILL)
        self.assertEqual(self._steps_logged(), [])
        admission = pj.admit(self.journal_path, target=TARGET)
        self.assertEqual(admission.kind, pj.RESTART_PROJECT)
        self.assertEqual(admission.steps_committed, 0)

    def test_a_finished_run_cannot_be_replayed(self):
        result = self._child(crash_at=99)          # never crashes
        self.assertEqual(result.returncode, 0)
        journal = pj.Journal(self.journal_path, TARGET)
        learned = ["PASS"]
        pj.replay(pj.admit(self.journal_path, target=TARGET),
                  step=lambda index: None, learn=learned.append,
                  journal=journal, total_steps=TOTAL_STEPS)
        journal.append(pj.PROJECT_CLOSED)

        admission = pj.admit(self.journal_path, target=TARGET)
        self.assertEqual(admission.kind, pj.ALREADY_COMPLETE)
        ran = []
        pj.replay(admission, step=ran.append, learn=learned.append,
                  journal=journal, total_steps=TOTAL_STEPS)
        self.assertEqual(ran, [], "a closed project must not run again")

    def test_a_corrupted_journal_is_refused_rather_than_guessed(self):
        self._child(crash_at=2)
        raw = self.journal_path.read_text()
        self.journal_path.write_text(raw.replace('"index": 0', '"index": 7'))
        with self.assertRaises(pj.RecoveryRefused):
            pj.admit(self.journal_path, target=TARGET)


if __name__ == "__main__":
    unittest.main()
