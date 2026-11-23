"""Regression tests for pilot scoring and train/test separation."""
import ast
import re
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def constants(filename):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    result = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                result[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
    return result


class PilotIntegrityTests(unittest.TestCase):
    def test_date_oracles_exclude_both_endpoints(self):
        for filename, key in [("test_evidence.py", "DATE_PROGRAM"),
                              ("run_e2e_daydiff_wide.py", "DATE_PROGRAM")]:
            program = constants(filename)[key]
            self.assertIn("(b - a).days - 1", program)
        self.assertEqual((date(2026, 11, 4) - date(2026, 3, 15)).days - 1, 233)

    def test_date_development_targets_are_not_heldout(self):
        for filename in ("run_e2e_daydiff.py", "run_e2e_daydiff_wide.py"):
            values = constants(filename)
            self.assertNotIn(values["DEEP_TARGET"], values["HELD_OUT"])
            training = values.get("TRAIN_TASKS", values.get("WAVE1", []) + values.get("WAVE2", []))
            self.assertTrue(set(training).isdisjoint(values["HELD_OUT"]))

    def test_modpow_reference_table_matches_python(self):
        values = constants("run_e2e_modpow.py")
        for task, answer in values["TRUTH"].items():
            match = re.fullmatch(r"(\d+)\^(\d+) mod (\d+)", task)
            self.assertIsNotNone(match)
            self.assertEqual(answer, pow(*map(int, match.groups())))

    def test_experiment_scripts_never_overwrite_historical_reports(self):
        for name in ("run_e2e.py", "run_e2e_daydiff.py",
                     "run_e2e_daydiff_wide.py", "run_e2e_modpow.py"):
            self.assertIn('.open("x", encoding="utf-8")', (ROOT / name).read_text())


if __name__ == "__main__":
    unittest.main()
