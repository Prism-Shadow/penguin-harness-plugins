"""Context management tests: work-log caps, head+tail folding, history folding."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import context_window as cw


class WorklogTests(unittest.TestCase):
    def test_short_log_untouched(self):
        text = "CURRENT STATE: probing\nNEXT STEPS: read the file"
        self.assertEqual(cw.cap_worklog(text, 1000), text)

    def test_cap_cuts_on_a_line_boundary_and_flags_it(self):
        text = "\n".join("line {}".format(i) for i in range(200))
        capped = cw.cap_worklog(text, 100)
        self.assertIn("WORK LOG truncated", capped)
        self.assertLessEqual(len(capped.splitlines()[0]), 100)
        # The kept part is the TOP, not an arbitrary slice.
        self.assertTrue(capped.startswith("line 0"))

    def test_hard_cut_when_no_line_break_near_the_cap(self):
        text = "x" * 5000
        capped = cw.cap_worklog(text, 100)
        self.assertTrue(capped.startswith("x" * 100))
        self.assertIn("WORK LOG truncated", capped)

    def test_none_is_empty(self):
        self.assertEqual(cw.cap_worklog(None, 10), "")


class FoldInputTests(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(cw.fold_input("abc"), "abc")

    def test_long_text_keeps_head_and_tail(self):
        text = "HEAD" + "m" * 10000 + "TRACEBACK_TAIL"
        folded = cw.fold_input(text, head=10, tail=20)
        self.assertTrue(folded.startswith("HEAD"))
        self.assertTrue(folded.endswith("TRACEBACK_TAIL"))
        self.assertIn("trace middle elided", folded)
        self.assertLess(len(folded), len(text))


class HistoryTests(unittest.TestCase):
    def _messages(self, pairs, size=1000):
        out = []
        for n in range(pairs):
            out.append({"role": "user", "content": "question {} ".format(n) + "q" * size})
            out.append({"role": "assistant", "content": "answer {} ".format(n) + "a" * size})
        return out

    def test_under_budget_returns_unchanged(self):
        messages = self._messages(2, size=10)
        folded, count = cw.fold_history(messages, budget_chars=10 ** 6)
        self.assertEqual(count, 0)
        self.assertEqual(len(folded), len(messages))

    def test_over_budget_folds_old_keeps_recent(self):
        messages = self._messages(10, size=1000)
        folded, count = cw.fold_history(messages, budget_chars=5000, keep_recent=4)
        self.assertEqual(count, 1)
        self.assertLess(len(folded), len(messages))
        self.assertIn("earlier exchanges folded", folded[0]["content"])
        # Recent turns survive verbatim.
        self.assertIn("question 9", folded[-2]["content"])

    def test_custom_summarizer_is_used(self):
        messages = self._messages(6, size=1000)
        folded, count = cw.fold_history(messages, budget_chars=2000,
                                        summarizer=lambda blocks: "SUMMARY")
        self.assertIn("SUMMARY", folded[0]["content"])

    def test_token_estimate(self):
        self.assertEqual(cw.estimate_tokens([{"role": "user", "content": "abcd"}]), 1)


if __name__ == "__main__":
    unittest.main()
