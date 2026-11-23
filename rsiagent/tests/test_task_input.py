import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from task_input import build_task


class TaskInputTests(unittest.TestCase):
    def test_only_public_input_is_included(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "input/payloads").mkdir(parents=True)
            (root / "input/prompt.txt").write_text("solve ticket")
            (root / "input/payloads/template.json").write_text('{"ticket":null}')
            (root / "reference.json").write_text("PRIVATE_REFERENCE")
            task = build_task(root)["tasks"][0]
            self.assertIn("solve ticket", task)
            self.assertIn('"ticket":null', task)
            self.assertNotIn("PRIVATE_REFERENCE", task)
    def test_symlink_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "input/payloads").mkdir(parents=True)
            (root / "input/prompt.txt").write_text("solve")
            (root / "private").write_text("secret")
            (root / "input/payloads/link").symlink_to(root / "private")
            with self.assertRaisesRegex(ValueError, "symlinks"):
                build_task(root)

if __name__ == "__main__":
    unittest.main()
