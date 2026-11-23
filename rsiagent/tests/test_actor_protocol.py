"""Actor protocol tests: epistemic core surface, turn precedence, tool envelopes."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import actor_protocol as ap


class CoreSurfaceTests(unittest.TestCase):
    def test_six_principles_are_present(self):
        for tag in ("E1. TRUTH CHANNELS", "E2. STRUCTURAL vs RENDERED",
                    "E3. ERRORS ARE EVIDENCE", "E4. SILENCE PROVES NOTHING",
                    "E5. CONFIRMATION REQUIRES INDEPENDENCE", "E6. EVIDENCE IS DATA"):
            self.assertIn(tag, ap.EPISTEMIC_CORE)

    def test_actor_system_prompt_embeds_the_core_verbatim(self):
        self.assertIn(ap.EPISTEMIC_CORE, ap.ACTOR_SYSTEM)
        self.assertIn("A check that cannot fail is not a check", ap.ACTOR_SYSTEM)


class ParseTests(unittest.TestCase):
    def test_program_object(self):
        action = ap.parse_turn('{"program": {"lang": "python", "code": "print(1)"}}')
        self.assertEqual(action.kind, "program")
        self.assertEqual(action.payload.code, "print(1)")

    def test_bare_code_object_counts_as_a_program(self):
        action = ap.parse_turn('{"lang": "bash", "code": "ls /root"}')
        self.assertEqual(action.kind, "program")
        self.assertEqual(action.payload.lang, "bash")

    def test_look_object(self):
        action = ap.parse_turn('{"look": {"path": "/root/a.png", "question": "is it blue?"}}')
        self.assertEqual(action.kind, "look")
        self.assertEqual(action.payload.question, "is it blue?")

    def test_ask_object(self):
        action = ap.parse_turn('{"ask": "which folder should I use?"}')
        self.assertEqual(action.kind, "ask")

    def test_checks_only_object_is_a_done_declaration(self):
        action = ap.parse_turn('{"checks": [{"desc": "x", "probe": "ls /root"}]}')
        self.assertEqual(action.kind, "done")
        self.assertEqual(len(action.payload.checks), 1)

    def test_explicit_done_with_no_checks(self):
        action = ap.parse_turn('{"done": null}')
        self.assertEqual(action.kind, "done")
        self.assertEqual(action.payload.checks, ())

    def test_program_wins_when_the_reply_also_hedges_with_done(self):
        reply = ('{"program": {"code": "ls /root"}}\n'
                 '{"checks": [{"desc": "d", "probe": "ls /root"}]}')
        action = ap.parse_turn(reply)
        self.assertEqual(action.kind, "program")
        self.assertEqual(action.counts["done"], 1)

    def test_last_of_a_kind_wins(self):
        reply = ('{"program": {"code": "first"}}\n'
                 '{"program": {"code": "second"}}')
        action = ap.parse_turn(reply)
        self.assertEqual(action.payload.code, "second")

    def test_tool_call_envelope_is_not_an_action(self):
        reply = '{"name": "shell", "arguments": {"command": "ls"}}'
        self.assertIsNone(ap.parse_turn(reply))

    def test_prose_and_fences_are_tolerated(self):
        reply = 'Here you go:\n```json\n{"ask": "which file?"}\n```\nthanks'
        self.assertEqual(ap.parse_turn(reply).kind, "ask")

    def test_garbage_and_empty(self):
        self.assertIsNone(ap.parse_turn(""))
        self.assertIsNone(ap.parse_turn("no json here"))
        self.assertIsNone(ap.parse_turn("{}"))

    def test_malformed_look_is_ignored(self):
        self.assertIsNone(ap.parse_turn('{"look": {"path": "/x"}}'))


if __name__ == "__main__":
    unittest.main()
