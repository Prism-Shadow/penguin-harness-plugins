import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from docker_verifier import verify_in_container


@unittest.skipUnless(os.environ.get("RSI_DOCKER_TEST") == "1",
                     "set RSI_DOCKER_TEST=1 for container integration test")
class DockerBoundaryTests(unittest.TestCase):
    def test_private_memory_hidden_and_candidate_read_only(self):
        with tempfile.TemporaryDirectory(prefix="rsi-boundary-", dir=str(Path.home())) as work:
            root = Path(work)
            root.chmod(0o755)
            candidate = root / "candidate"
            candidate.mkdir(mode=0o755)
            (candidate / "result.txt").write_text("original")
            private = root / "actor-private.txt"
            private.write_text("secret")
            probe = "import pathlib\np=pathlib.Path('/candidate/result.txt')\nassert p.read_text() == 'original'\nassert not pathlib.Path('/actor-private.txt').exists()\nassert not pathlib.Path('/candidate/../actor-private.txt').exists()\nprint('private_hidden')\ntry:\n    p.write_text('changed')\nexcept OSError:\n    print('candidate_readonly')\nelse:\n    raise AssertionError('writable')\n"
            probe += "\nassert not pathlib.Path(" + repr(str(private)) + ").exists()\n"
            probe += "assert sorted(x.name for x in pathlib.Path('/candidate').iterdir()) == ['result.txt']\n"
            probe += "\nassert any(line.strip() == 'NoNewPrivs:\t1' for line in open('/proc/self/status'))\n"
            probe += "assert set(__import__('os').listdir('/sys/class/net')) == {'lo'}\n"
            try:
                result = verify_in_container(candidate, ["python", "-I", "-c", probe])
            except subprocess.CalledProcessError as exc:
                self.fail("Docker verifier failed: " + (exc.stderr or str(exc)))
            self.assertIn("private_hidden", result.stdout)
            self.assertIn("candidate_readonly", result.stdout)
            self.assertEqual((candidate / "result.txt").read_text(), "original")

    def test_invalid_directory_fails_before_container(self):
        with self.assertRaises(FileNotFoundError):
            verify_in_container("/nonexistent-rsi-test-dir", ["true"])


if __name__ == "__main__":
    unittest.main()
