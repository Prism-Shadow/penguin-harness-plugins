"""Parsing tests for the draft-then-comment-then-redraft reply shape."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import model_host as mh


class EmbeddedObjectTests(unittest.TestCase):
    def test_objects_separated_by_prose_are_all_found(self):
        text = ('{"a": 1}\n\nthat is not right, let me redo it\n\n{"b": 2}\n\n'
                'Hmm, no output?')
        self.assertEqual(mh.parse_embedded_objects(text), [{"a": 1}, {"b": 2}])

    def test_prose_without_objects_is_empty(self):
        self.assertEqual(mh.parse_embedded_objects("no json here"), [])
        self.assertEqual(mh.parse_embedded_objects(""), [])

    def test_braces_inside_prose_do_not_break_the_scan(self):
        text = 'note } here {"a": 1} and {"b": 2}'
        self.assertEqual(mh.parse_embedded_objects(text), [{"a": 1}, {"b": 2}])


class RoleObjectTests(unittest.TestCase):
    def test_two_objects_with_prose_take_the_last(self):
        text = ('{"vm_run": "printf \'alpha\' > /root/proof.txt"}\n\n'
                '{"vm_run": "printf \'alpha\' > /root/proof.txt"}\n\n'
                'No output? Wait I need react to output.')
        self.assertEqual(mh.ModelHost._role_object("actor", text),
                         {"vm_run": "printf 'alpha' > /root/proof.txt"})

    def test_the_last_object_wins_when_they_differ(self):
        text = '{"answer": "draft"}\n\nactually:\n\n{"answer": "final"}'
        self.assertEqual(mh.ModelHost._role_object("actor", text),
                         {"answer": "final"})

    def test_a_single_object_with_prose_still_parses(self):
        text = 'here you go:\n{"answer": "a"}\nthanks'
        self.assertEqual(mh.ModelHost._role_object("actor", text), {"answer": "a"})

    def test_repeated_identical_objects_still_take_one(self):
        text = '{"answer": "a"}\n{"answer": "a"}'
        self.assertEqual(mh.ModelHost._role_object("actor", text), {"answer": "a"})

    def test_no_object_is_refused(self):
        with self.assertRaises(mh.LLMError):
            mh.ModelHost._role_object("actor", "I cannot do that")
        with self.assertRaises(mh.LLMError):
            mh.ModelHost._role_object("actor", "{broken")

    def test_a_fenced_block_is_used(self):
        text = 'plan:\n```json\n{"answer": "a"}\n```\n'
        self.assertEqual(mh.ModelHost._role_object("actor", text), {"answer": "a"})


if __name__ == "__main__":
    unittest.main()
