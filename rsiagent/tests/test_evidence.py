"""Tests for the evidence-based verifier wiring (runtime/evidence.py)."""
import os
import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from evidence import container_verifier, EvidenceHost

# A ground-truth program: PASS iff the answer is the length of the task string.
LEN_PROGRAM = "print('PASS' if len(answer) == len(task) else 'FAIL')\n"

# FAIL program: PASS iff the answer numerically equals the day difference.
DATE_PROGRAM = (
    "import re\n"
    "from datetime import date\n"
    "dates = re.findall(r'(\\d{4}-\\d{2}-\\d{2})', task)\n"
    "a = date(*map(int, dates[0].split('-')))\n"
    "b = date(*map(int, dates[1].split('-')))\n"
    "expected = max(0, (b - a).days - 1)\n"
    "try:\n"
    "    ok = int(str(answer).strip()) == expected\n"
    "except ValueError:\n"
    "    ok = False\n"
    "print('PASS' if ok else 'FAIL')\n"
)


class EvidenceTests(unittest.TestCase):
    def test_len_verdicts(self):
        verify = container_verifier(LEN_PROGRAM)
        self.assertEqual(verify("abc", {"answer": "xyz"}), "PASS")
        self.assertEqual(verify("abc", {"answer": "toolong"}), "FAIL")

    def test_date_ground_truth(self):
        verify = container_verifier(DATE_PROGRAM)
        task = "days between 2026-03-15 and 2026-11-04 (inclusive of neither)"
        self.assertEqual(verify(task, {"answer": "233"}), "PASS")
        self.assertEqual(verify(task, {"answer": "234"}), "FAIL")
        self.assertEqual(verify(task, {"answer": "not a number"}), "FAIL")

    def test_explicit_candidate_directory_is_read_only_and_private_files_hidden(self):
        with tempfile.TemporaryDirectory(prefix="rsi-evidence-", dir=str(Path.home())) as work:
            root = Path(work)
            root.chmod(0o755)
            candidate = root / "candidate"
            candidate.mkdir(mode=0o755)
            (candidate / "public.txt").write_text("verified")
            (root / "private.txt").write_text("private")
            program = (
                "from pathlib import Path\n"
                "p = Path('/candidate/public.txt')\n"
                "assert p.read_text() == 'verified'\n"
                "assert not Path('/private.txt').exists()\n"
                "try:\n"
                " p.write_text('changed')\n"
                "except OSError:\n"
                " print('PASS')\n"
                "else:\n"
                " print('FAIL')\n"
            )
            verify = container_verifier(program, candidate_dir=candidate)
            self.assertEqual(verify("task", {"answer": "value"}), "PASS")
            self.assertEqual((candidate / "public.txt").read_text(), "verified")
            self.assertIn("caller-selected candidate", verify.verifier_mode)

    def test_unusable_verdict_rejected(self):
        verify = container_verifier("print('MAYBE')\n")
        with self.assertRaises(RuntimeError):
            verify("t", {"answer": "a"})

    def test_evidence_host_routes_verify_only_to_container(self):
        seen = {}
        model_host = type("MH", (), {
            "run_actor": staticmethod(lambda task, memory:
                seen.setdefault("actor", task) or {"public": {"answer": "234"}}),
            "verify": staticmethod(lambda task, public:
                seen.setdefault("model_verify", True) or "PASS"),
            "consolidate": staticmethod(lambda memory, task, cand, verdict: memory),
            "curriculum_review": staticmethod(lambda t, v, m: None),
        })()
        host = EvidenceHost(model_host, container_verifier(DATE_PROGRAM))
        task = "days between 2026-03-15 and 2026-11-04 (inclusive of neither)"
        # candidate 233 is correct -> container PASS, and the ModelHost's
        # model-judgement verify() must never have been called.
        self.assertEqual(host.verify(task, {"answer": "233"}), "PASS")
        self.assertNotIn("model_verify", seen)


if __name__ == "__main__":
    unittest.main()
