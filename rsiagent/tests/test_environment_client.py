import json
import io
import tempfile
from contextlib import redirect_stdout
from unittest.mock import patch
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from environment_client import EnvironmentClient
from model_host import LLMError
import cli


class Handler(BaseHTTPRequestHandler):
    requests = []
    def do_GET(self):
        self.requests.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ticket":"observed"}')
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append((self.path, body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "scope": "train_only",
                                    "task_id": body.get("task_id"),
                                    "correct": False,
                                    "score": 0.5}).encode())
    def log_message(self, *args):
        pass


class FakeClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = {}
        self.prompts = []
    def complete(self, role, system, user, **kwargs):
        self.calls[role] = self.calls.get(role, 0) + 1
        self.prompts.append((role, user))
        return next(self.replies)


class EnvironmentClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:" + str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
    def test_actor_observes_real_local_http_before_answer(self):
        raw = FakeClient(['{"environment_get":"/api/tickets/1?view=full"}',
                          '{"answer":"observed"}'])
        client = EnvironmentClient(raw, self.origin, ["/api/tickets"])
        self.assertEqual(json.loads(client.complete("actor", "solve", "task"))["answer"], "observed")
        self.assertIn('"ticket":"observed"', raw.prompts[1][1])
        self.assertEqual(raw.calls, {"actor": 2})
    def test_cli_training_uses_environment_observations(self):
        class TrainingModel:
            observations = 0
            def __init__(self, *args):
                self.calls = {}
                self.log = None
            def complete(self, role, system, user, **kwargs):
                self.calls[role] = self.calls.get(role, 0) + 1
                if role == "curriculum" and "BROAD wave" in system:
                    return '{"tasks":["practice"]}'
                if role == "curriculum":
                    return '{"decision":"HANDOFF","report":"checked"}'
                if role == "verifier" and "independent Verifier." in system:
                    return '{"verdict":"PASS","report":"checked"}'
                if role == "verifier":
                    return 'PASS'
                if "reconciling the complete causal memory" in system:
                    return '{"lessons":[{"condition":"case","action":"inspect","outcome":"done","support":"verified","source_ref":"L0001"}]}'
                if "distilling experience" in system:
                    return '{"condition":"case","action":"inspect","outcome":"done","support":"verified"}'
                if 'Environment observation' in user:
                    self.__class__.observations += 1
                    return '{"answer":"observed","evidence":"ticket"}'
                return '{"environment_get":"/api/tickets/1"}'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.json"
            source.write_text('{"domain":"tickets","target":"target","wave_size":1,"max_practices":0}')
            with patch("cli.ChatClient", TrainingModel), redirect_stdout(io.StringIO()):
                status = cli.main(["train", "--base-url", "http://127.0.0.1/v1", "--model", "fake",
                                   "--environment-url", self.origin, "--environment-path", "/api/tickets",
                                   "--input", str(source), "--snapshot", str(root / "snapshot"),
                                   "--report", str(root / "report")], key_stream=io.StringIO("key"))
            self.assertEqual(status, 0)
            self.assertGreaterEqual(TrainingModel.observations, 2)
            self.assertEqual(json.loads((root / "report").read_text())["target_status"], "PASS")

    def test_only_verifier_can_call_allowlisted_training_judge(self):
        raw = FakeClient(['{"verdict":"FAIL","report":"the judge scored 0.5"}'])
        client = EnvironmentClient(raw, self.origin, ["/api/tickets"],
                                   training_judge_path="/api/judge", training_task_ids=["train_001"],
                                   training_catalog={"train_001": "training target"})
        out = client.complete_verifier(
            "verify", "candidate",
            task="Training task ID: train_001\ntraining target", answer={"x": 1})
        self.assertEqual(json.loads(out)["verdict"], "FAIL")
        # The initial judge feedback is injected into the Verifier's prompt.
        self.assertIn('"score": 0.5', raw.prompts[0][1])
        self.assertIn("Training judge feedback", raw.prompts[0][1])
        with self.assertRaisesRegex(ValueError, "not permitted"):
            client._judge({"task_id":"test_001", "answer":{}})
        actor = EnvironmentClient(FakeClient(['{"training_judge":{"task_id":"train_001","answer":{}}}']),
                                  self.origin, ["/api/tickets"],
                                  training_judge_path="/api/judge", training_task_ids=["train_001"],
                                  training_catalog={"train_001": "training target"})
        with self.assertRaises(LLMError):
            actor.complete("actor", "solve", "task")

    def test_cli_rejects_training_judge_during_evaluation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.json"
            source.write_text('{"tasks":["heldout"]}')
            with patch("cli.ChatClient", return_value=FakeClient([])), self.assertRaisesRegex(
                    ValueError, "unavailable during evaluation"):
                cli.main(["evaluate", "--base-url", "http://127.0.0.1/v1", "--model", "fake",
                          "--input", str(source), "--snapshot", str(root / "snapshot"),
                          "--report", str(root / "report"), "--expected-digest", "a" * 64,
                          "--environment-url", self.origin, "--environment-path", "/api/tickets",
                          "--training-judge-path", "/api/judge", "--training-task-id", "train_001"],
                         key_stream=io.StringIO("key"))

    def test_unlisted_or_escape_paths_are_rejected(self):
        client = EnvironmentClient(FakeClient([]), self.origin, ["/api/tickets"])
        for path in ["/api/admin", "/api/tickets2", "/api/tickets/../admin", "//evil/x", "/api/tickets/%2e%2e/x"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                client._get(path)
    def test_environment_request_budget_is_bounded(self):
        for invalid in (0, 65, True):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "budget"):
                EnvironmentClient(FakeClient([]), self.origin, ["/api/tickets"], max_requests=invalid)
        self.assertEqual(EnvironmentClient(FakeClient([]), self.origin, ["/api/tickets"],
                                           max_requests=32).max_requests, 32)

    def test_external_origin_and_unbounded_requests_rejected(self):
        with self.assertRaises(ValueError):
            EnvironmentClient(FakeClient([]), "http://example.com:80", ["/api"])
        client = EnvironmentClient(FakeClient(['{"environment_get":"/api/tickets"}'] * 2),
                                   self.origin, ["/api/tickets"], max_requests=1)
        with self.assertRaises(LLMError):
            client.complete("actor", "solve", "task")

if __name__ == "__main__":
    unittest.main()
