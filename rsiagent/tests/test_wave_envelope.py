"""Wave envelope tests: transport parsing, contradiction rules, saturation gating."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import wave_envelope as we


def envelope(**overrides):
    value = {"decision": "WAVE", "rationale": "coverage is thin",
             "projects": [{"project_id": "p1", "instruction": "do a thing"}]}
    value.update(overrides)
    return json.dumps(value)


class ParseTests(unittest.TestCase):
    def test_valid_wave(self):
        decision = we.parse_wave_handoff(envelope())
        self.assertEqual(decision.decision, "WAVE")
        self.assertEqual(decision.projects[0].project_id, "p1")

    def test_saturated_and_stalled_carry_no_projects(self):
        for verdict in ("SATURATED", "STALLED"):
            decision = we.parse_wave_handoff(envelope(decision=verdict, projects=[]))
            self.assertEqual(decision.decision, verdict)

    def test_contradictory_claims_are_refused(self):
        self.assertIsNone(we.parse_wave_handoff(envelope(decision="SATURATED")))
        self.assertIsNone(we.parse_wave_handoff(envelope(projects=[])))

    def test_unknown_decision_refused(self):
        self.assertIsNone(we.parse_wave_handoff(envelope(decision="MAYBE")))

    def test_empty_rationale_refused(self):
        self.assertIsNone(we.parse_wave_handoff(envelope(rationale="   ")))

    def test_projects_must_be_a_list(self):
        self.assertIsNone(we.parse_wave_handoff(envelope(projects={"a": 1})))

    def test_project_id_must_be_a_safe_slug(self):
        for bad in ("Upper", "../etc", "", "has space", "x" * 80):
            self.assertIsNone(we.parse_wave_handoff(
                envelope(projects=[{"project_id": bad, "instruction": "i"}])), bad)

    def test_project_needs_an_instruction(self):
        self.assertIsNone(we.parse_wave_handoff(
            envelope(projects=[{"project_id": "p1", "instruction": "  "}])))

    def test_fenced_json_is_accepted(self):
        decision = we.parse_wave_handoff("```json\n" + envelope() + "\n```")
        self.assertIsNotNone(decision)

    def test_prose_around_json_is_accepted(self):
        decision = we.parse_wave_handoff("here you go:\n" + envelope() + "\nthanks")
        self.assertIsNotNone(decision)

    def test_garbage_returns_none(self):
        self.assertIsNone(we.parse_wave_handoff("not json at all"))
        self.assertIsNone(we.parse_wave_handoff(None))


class GatingTests(unittest.TestCase):
    def test_wave_continues_under_the_limit(self):
        decision = we.parse_wave_handoff(envelope())
        self.assertEqual(we.should_continue(decision, 1, 3), (True, ""))

    def test_wave_limit_reached(self):
        decision = we.parse_wave_handoff(envelope())
        self.assertEqual(we.should_continue(decision, 3, 3),
                         (False, "WAVE_LIMIT_REACHED"))

    def test_saturation_and_stall_stop(self):
        for verdict in ("SATURATED", "STALLED"):
            decision = we.parse_wave_handoff(envelope(decision=verdict, projects=[]))
            self.assertFalse(we.should_continue(decision, 0, 3)[0])

    def test_unusable_handoff_stops(self):
        self.assertFalse(we.should_continue(None, 0, 3)[0])


if __name__ == "__main__":
    unittest.main()
