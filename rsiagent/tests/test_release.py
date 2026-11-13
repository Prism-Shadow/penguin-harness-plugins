"""The Actor's owned resources are released after an attempt commits."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from model_host import ModelHost


class RecordingClient:
    def __init__(self):
        self.released = []
        self.calls = {}

    def complete(self, role, system, user, **kwargs):
        self.calls[role] = self.calls.get(role, 0) + 1
        if role == "actor":
            return '{"answer":"42","evidence":"computed"}'
        return '{"verdict":"PASS","report":"checked"}'

    def release_history(self, history):
        self.released.append(list(history))


class ReleaseTests(unittest.TestCase):
    def test_release_candidate_frees_the_actor_history(self):
        client = RecordingClient()
        host = ModelHost(client)
        candidate = host.run_actor("task", [])
        history = candidate.private["actor_history"]
        self.assertIsInstance(history, list)
        host.release_candidate(candidate)
        self.assertEqual(client.released, [[]])
        self.assertEqual(candidate.private["actor_history"], [])

    def test_release_is_safe_without_history(self):
        client = RecordingClient()
        host = ModelHost(client)

        class Bare:
            public = {"answer": "x"}
            private = None

        host.release_candidate(Bare())  # must not raise
        self.assertEqual(client.released, [])


if __name__ == "__main__":
    unittest.main()
