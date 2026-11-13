"""Practice-journal tests: durable writes, chain integrity, strict admission."""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import practice_journal as pj

TARGET = "learn to fix the day-of-week bug"


class JournalTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.path = self.dir / "journal.json"

    def tearDown(self):
        self._dir.cleanup()

    def _journal(self):
        return pj.Journal(self.path, TARGET).start()

    def test_entries_chain_and_report_their_index(self):
        journal = self._journal()
        journal.append(pj.STEP_COMMITTED, index=0)
        journal.append(pj.STEP_COMMITTED, index=1)
        entries = journal.entries()
        self.assertEqual([entry.index for entry in entries], [1, 2, 3])
        self.assertEqual([entry.kind for entry in entries],
                         [pj.PRACTICE_STARTED, pj.STEP_COMMITTED,
                          pj.STEP_COMMITTED])
        self.assertEqual(journal.steps_committed(), 2)

    def test_a_truncated_tail_is_detected(self):
        journal = self._journal()
        journal.append(pj.STEP_COMMITTED, index=0)
        raw = self.path.read_text()
        self.path.write_text(raw[: len(raw) // 2])
        with self.assertRaises(pj.RecoveryRefused):
            journal.entries()

    def test_a_tampered_payload_is_detected(self):
        journal = self._journal()
        journal.append(pj.STEP_COMMITTED, index=0)
        state = json.loads(self.path.read_text())
        state["entries"][0]["payload"]["index"] = 99
        self.path.write_text(json.dumps(state))
        with self.assertRaises(pj.RecoveryRefused) as caught:
            journal.entries()
        self.assertIn("chain is broken", str(caught.exception))

    def test_a_removed_entry_is_detected(self):
        journal = self._journal()
        journal.append(pj.STEP_COMMITTED, index=0)
        journal.append(pj.STEP_COMMITTED, index=1)
        state = json.loads(self.path.read_text())
        state["entries"].pop(0)
        self.path.write_text(json.dumps(state))
        with self.assertRaises(pj.RecoveryRefused):
            journal.entries()

    def test_a_missing_journal_is_refused_not_invented(self):
        journal = pj.Journal(self.path, TARGET)
        with self.assertRaises(pj.RecoveryRefused):
            journal.entries()

    def test_atomic_write_leaves_the_old_file_when_the_write_fails(self):
        pj.atomic_json(self.path, {"value": 1})
        original = self.path.read_text()

        class Boom:
            def __init__(self, *args, **kwargs):
                raise OSError("disk full")

        real = pj.tempfile.mkstemp
        pj.tempfile.mkstemp = Boom
        try:
            with self.assertRaises(OSError):
                pj.atomic_json(self.path, {"value": 2})
        finally:
            pj.tempfile.mkstemp = real
        self.assertEqual(self.path.read_text(), original)

    def test_no_temporary_files_are_left_behind(self):
        pj.atomic_json(self.path, {"value": 1})
        leftovers = [name for name in os.listdir(self.dir)
                     if name.startswith(".")]
        self.assertEqual(leftovers, [])


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_manifest_matches_then_rejects_a_change(self):
        (self.dir / "memory.json").write_text('{"m": 1}')
        record = pj.manifest({"memory.json": (self.dir / "memory.json").read_bytes()})
        self.assertTrue(pj.verify_manifest(self.dir, record))
        (self.dir / "memory.json").write_text('{"m": 2}')
        with self.assertRaises(pj.RecoveryRefused):
            pj.verify_manifest(self.dir, record)

    def test_a_missing_artifact_is_refused(self):
        with self.assertRaises(pj.RecoveryRefused):
            pj.verify_manifest(self.dir, {"absent.json": {"bytes": 1, "sha256": "x"}})


class EvidenceTests(unittest.TestCase):
    def test_paired_and_actor_leads(self):
        good = [{"role": "user", "content": "t"}, {"role": "assistant", "content": "a"}]
        self.assertTrue(pj.paired(good))
        self.assertTrue(pj.actor_leads(good))
        self.assertFalse(pj.paired(good + [{"role": "user", "content": "x"}]))
        self.assertFalse(pj.actor_leads([{"role": "assistant", "content": "a"}]))

    def test_interleaved_history_is_not_paired(self):
        bad = [{"role": "user", "content": "t"},
               {"role": "user", "content": "t2"},
               {"role": "assistant", "content": "a"},
               {"role": "assistant", "content": "a2"}]
        self.assertFalse(pj.paired(bad))


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.path = self.dir / "journal.json"

    def tearDown(self):
        self._dir.cleanup()

    def _journal(self):
        return pj.Journal(self.path, TARGET).start()

    def test_no_journal_means_restart_and_nothing_may_be_skipped(self):
        admission = pj.admit(self.path, target=TARGET)
        self.assertEqual(admission.kind, pj.RESTART_PROJECT)
        self.assertEqual(admission.steps_committed, 0)
        self.assertIn("nothing durable", admission.reason)

    def test_an_empty_journal_restarts(self):
        self._journal()
        admission = pj.admit(self.path, target=TARGET)
        self.assertEqual(admission.kind, pj.RESTART_PROJECT)

    def test_committed_steps_resume_from_that_count(self):
        journal = self._journal()
        journal.append(pj.STEP_COMMITTED, index=0)
        journal.append(pj.STEP_COMMITTED, index=1)
        admission = pj.admit(self.path, target=TARGET)
        self.assertEqual(admission.kind, pj.RESUME_PRACTICE)
        self.assertEqual(admission.steps_committed, 2)
        self.assertTrue(admission.runs_steps)

    def test_a_verdict_without_learning_redoes_only_learning(self):
        journal = self._journal()
        journal.append(pj.STEP_COMMITTED, index=0)
        journal.append(pj.VERDICT_SAVED, verdict="FAIL",
                       history=[{"role": "user", "content": "t"},
                                {"role": "assistant", "content": "a"}])
        admission = pj.admit(self.path, target=TARGET)
        self.assertEqual(admission.kind, pj.REDO_LEARNING)
        self.assertEqual(admission.verdict, "FAIL")
        self.assertFalse(admission.runs_steps, "Actor work must not repeat")
        self.assertTrue(admission.runs_learning)

    def test_a_terminal_verdict_with_an_incomplete_history_is_refused(self):
        journal = self._journal()
        journal.append(pj.VERDICT_SAVED, verdict="PASS",
                       history=[{"role": "assistant", "content": "a"}])
        with self.assertRaises(pj.RecoveryRefused) as caught:
            pj.admit(self.path, target=TARGET)
        self.assertIn("incomplete Actor history", str(caught.exception))

    def test_an_infrastructure_stop_is_not_a_verdict(self):
        journal = self._journal()
        journal.append(pj.STEP_COMMITTED, index=0)
        journal.append(pj.INFRASTRUCTURE_STOP, reason="qemu died")
        admission = pj.admit(self.path, target=TARGET)
        self.assertEqual(admission.kind, pj.INFRASTRUCTURE)
        self.assertFalse(admission.runs_steps)
        self.assertFalse(admission.runs_learning)

    def test_a_closed_project_cannot_be_redone(self):
        journal = self._journal()
        journal.append(pj.VERDICT_SAVED, verdict="PASS", history=[])
        journal.append(pj.LEARNING_COMMITTED, verdict="PASS")
        journal.append(pj.PROJECT_CLOSED)
        admission = pj.admit(self.path, target=TARGET)
        self.assertEqual(admission.kind, pj.ALREADY_COMPLETE)
        self.assertFalse(admission.runs_steps)

    def test_a_different_target_is_refused(self):
        self._journal().append(pj.STEP_COMMITTED, index=0)
        with self.assertRaises(pj.RecoveryRefused) as caught:
            pj.admit(self.path, target="a different target")
        self.assertIn("different target", str(caught.exception))

    def test_a_journal_that_does_not_start_with_started_is_refused(self):
        self._journal().append(pj.STEP_COMMITTED, index=0)
        state = json.loads(self.path.read_text())
        # Rewrite a well-formed chain whose first entry is a step.
        journal = pj.Journal(self.path, TARGET)
        state["entries"] = []
        state["head"] = "0" * 64
        self.path.write_text(json.dumps(state))
        journal.append(pj.STEP_COMMITTED, index=0)
        with self.assertRaises(pj.RecoveryRefused):
            pj.admit(self.path, target=TARGET)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.path = self.dir / "journal.json"

    def tearDown(self):
        self._dir.cleanup()

    def test_resume_runs_only_the_missing_steps(self):
        journal = pj.Journal(self.path, TARGET).start()
        journal.append(pj.STEP_COMMITTED, index=0)
        journal.append(pj.STEP_COMMITTED, index=1)
        admission = pj.admit(self.path, target=TARGET)
        ran = []
        learned = []
        result = pj.replay(admission, step=ran.append, learn=learned.append,
                           journal=journal, total_steps=4)
        self.assertEqual(ran, [2, 3])            # steps 0 and 1 never repeat
        self.assertEqual(result.steps_run, 2)
        self.assertEqual(result.skipped, 2)
        self.assertTrue(result.learning_ran)
        self.assertEqual(journal.steps_committed(), 4)

    def test_redo_learning_runs_no_steps(self):
        journal = pj.Journal(self.path, TARGET).start()
        journal.append(pj.STEP_COMMITTED, index=0)
        journal.append(pj.VERDICT_SAVED, verdict="FAIL",
                       history=[{"role": "user", "content": "t"},
                                {"role": "assistant", "content": "a"}])
        admission = pj.admit(self.path, target=TARGET)
        ran = []
        learned = []
        result = pj.replay(admission, step=ran.append, learn=learned.append,
                           journal=journal, total_steps=4)
        self.assertEqual(ran, [])
        self.assertEqual(learned, ["FAIL"])
        self.assertTrue(result.learning_ran)
        self.assertEqual(journal.steps_committed(), 1)

    def test_infrastructure_admission_runs_nothing(self):
        journal = pj.Journal(self.path, TARGET).start()
        journal.append(pj.INFRASTRUCTURE_STOP, reason="qemu died")
        admission = pj.admit(self.path, target=TARGET)
        ran, learned = [], []
        result = pj.replay(admission, step=ran.append, learn=learned.append,
                           journal=journal, total_steps=3)
        self.assertEqual((ran, learned), ([], []))
        self.assertFalse(result.learning_ran)

    def test_a_replayed_run_can_finish_and_close(self):
        journal = pj.Journal(self.path, TARGET).start()
        journal.append(pj.STEP_COMMITTED, index=0)
        admission = pj.admit(self.path, target=TARGET)
        pj.replay(admission, step=lambda index: None, learn=lambda verdict: None,
                  journal=journal, total_steps=1)
        journal.append(pj.PROJECT_CLOSED)
        again = pj.admit(self.path, target=TARGET)
        self.assertEqual(again.kind, pj.ALREADY_COMPLETE)


if __name__ == "__main__":
    unittest.main()
