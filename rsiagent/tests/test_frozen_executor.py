import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from frozen_executor import run_with_frozen_snapshot
from rsi import FrozenMemory


class FrozenExecutorTests(unittest.TestCase):
    def test_rejects_untrusted_digest_before_starting_container(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "snapshot.json"
            FrozenMemory.create(["secret"]).save(path)
            with patch("frozen_executor.subprocess.run") as runner:
                with self.assertRaisesRegex(ValueError, "trusted digest"):
                    run_with_frozen_snapshot(path, "0"*64, ["true"])
                runner.assert_not_called()

    def test_failed_execution_still_detects_changed_snapshot(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "snapshot.json"
            frozen = FrozenMemory.create(["original"])
            frozen.save(path)
            def fail_after_change(*args, **kwargs):
                path.write_text("corrupted")
                raise subprocess.CalledProcessError(1, args[0])
            with patch("frozen_executor.subprocess.run", side_effect=fail_after_change):
                with self.assertRaisesRegex(RuntimeError, "snapshot changed"):
                    run_with_frozen_snapshot(path, frozen.sha256, ["true"])

    @unittest.skipUnless(os.environ.get("RSI_DOCKER_TEST") == "1",
                         "set RSI_DOCKER_TEST=1 for container integration")
    def test_snapshot_is_readable_but_not_writable_in_container(self):
        with tempfile.TemporaryDirectory(prefix="rsi-frozen-", dir=str(Path.home())) as temp:
            root = Path(temp)
            root.chmod(0o755)
            path = root / "snapshot.json"
            frozen = FrozenMemory.create(["lesson"])
            frozen.save(path)
            before = path.read_bytes()
            probe = ("import pathlib\n"
                     "p = pathlib.Path('/frozen/memory.json')\n"
                     "assert p.read_text()\n"
                     "try:\n p.write_text('changed')\n"
                     "except OSError:\n print('READ_ONLY')\n"
                     "else:\n raise AssertionError('writable')\n")
            result = run_with_frozen_snapshot(path, frozen.sha256,
                                              ["python", "-I", "-c", probe])
            self.assertIn("READ_ONLY", result.stdout)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
