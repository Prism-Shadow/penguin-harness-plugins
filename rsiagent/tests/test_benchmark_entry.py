import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import benchmark_entry


class BenchmarkEntryTests(unittest.TestCase):
    def test_evaluate_uses_readonly_roles_and_public_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "input/payloads").mkdir(parents=True)
            (root / "input/prompt.txt").write_text("solve")
            (root / "input/payloads/item.json").write_text('{"ticket":"1"}')
            (root / "reference.json").write_text("hidden-answer")
            captured = {}
            def fake_main(argv):
                captured["args"] = argv
                captured["input"] = json.loads(Path(argv[argv.index("--input") + 1]).read_text())
                return 0
            args = ["--project","p","--workspace",str(root),"--provider","custom","--model","m",
                    "--actor-agent","a","--verifier-agent","v","--curriculum-agent","c",
                    "--snapshot",str(root/"snapshot"),"--expected-digest","a"*64,
                    "--report",str(root/"report"),"--answer-output",str(root/"answer.json"),
                    "--environment-url","http://127.0.0.1:9003","--environment-path","/api/tickets"]
            with patch("benchmark_entry.cli.main", fake_main):
                self.assertEqual(benchmark_entry.main(args), 0)
            self.assertIn("--penguin-read-only", captured["args"])
            self.assertIn("--answer-output", captured["args"])
            self.assertNotIn("--training-judge-path", captured["args"])
            self.assertIn('"ticket":"1"', captured["input"]["tasks"][0])
            self.assertNotIn("hidden-answer", captured["input"]["tasks"][0])

if __name__ == "__main__":
    unittest.main()
