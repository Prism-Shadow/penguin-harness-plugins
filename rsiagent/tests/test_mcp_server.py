import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
import mcp_server


class McpTests(unittest.TestCase):
    def test_stdio_handshake_and_tool_discovery(self):
        requests = [
            {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}},
            {"jsonrpc":"2.0","method":"notifications/initialized"},
            {"jsonrpc":"2.0","id":2,"method":"tools/list"},
        ]
        result = subprocess.run([sys.executable, str(ROOT/"runtime/mcp_server.py")],
            input="".join(json.dumps(x)+"\n" for x in requests), text=True,
            capture_output=True, check=True)
        replies = [json.loads(x) for x in result.stdout.splitlines()]
        self.assertEqual(len(replies), 2)
        self.assertEqual({x["name"] for x in replies[1]["result"]["tools"]},
                         {"rsi_train", "rsi_evaluate"})

    def test_tool_uses_penguin_host_and_forbids_unknown_arguments(self):
        values = {key:"value" for key in mcp_server._REQUIRED}
        calls = []
        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout='{"phase":"train"}', stderr="")
        result = mcp_server.call_tool("rsi_train", values, runner=runner)
        self.assertEqual(result["phase"], "train")
        self.assertIn("penguin", calls[0][0])
        self.assertNotIn("--base-url", calls[0][0])
        self.assertIn("--penguin-read-only", calls[0][0])
        with self.assertRaises(ValueError):
            mcp_server.call_tool("rsi_train", {**values, "executable":"/tmp/evil"}, runner=runner)
        self.assertEqual(len(calls), 1)

    def test_read_only_option_is_explicit_and_type_checked(self):
        values = {key:"value" for key in mcp_server._REQUIRED}
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='{"phase":"train"}', stderr="")
        mcp_server.call_tool("rsi_train", {**values, "read_only":True}, runner=runner)
        self.assertIn("--penguin-read-only", calls[0])
        mcp_server.call_tool("rsi_train", {**values, "read_only":False}, runner=runner)
        self.assertNotIn("--penguin-read-only", calls[1])
        with self.assertRaisesRegex(ValueError, "boolean"):
            mcp_server.call_tool("rsi_train", {**values, "read_only":"true"}, runner=runner)

    def test_local_admin_token_requires_operator_environment(self):
        values = {key:"value" for key in mcp_server._REQUIRED}
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='{"phase":"train"}', stderr="")
        with patch.dict("os.environ", {"RSI_PENGUIN_LOCAL_API_TOKEN":"1"}):
            mcp_server.call_tool("rsi_train", values, runner=runner)
            mcp_server.call_tool("rsi_train", {**values,"read_only":False}, runner=runner)
        self.assertIn("--penguin-local-api-token", calls[0])
        self.assertNotIn("--penguin-local-api-token", calls[1])

    def test_private_paths_require_read_only(self):
        values = {key:"value" for key in mcp_server._REQUIRED}
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='{"phase":"train"}', stderr="")
        mcp_server.call_tool("rsi_train", {**values, "private_paths":["/private"]}, runner=runner)
        self.assertEqual(calls[0][calls[0].index("--private-path")+1], "/private")
        with self.assertRaisesRegex(ValueError, "require read_only"):
            mcp_server.call_tool("rsi_train", {**values, "private_paths":["/private"],
                                              "read_only":False}, runner=runner)

    def test_environment_access_is_operator_controlled(self):
        values = {key: "value" for key in mcp_server._REQUIRED}
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='{"phase":"train"}', stderr="")
        environment = {"RSI_ENVIRONMENT_URL": "http://127.0.0.1:9003",
                       "RSI_ENVIRONMENT_PATHS": '["/api/catalog", "/api/tickets"]'}
        with patch.dict("os.environ", environment):
            mcp_server.call_tool("rsi_train", values, runner=runner)
            with self.assertRaises(ValueError):
                mcp_server.call_tool("rsi_train", {**values, "environment_url":"http://evil"}, runner=runner)
        self.assertIn("--environment-url", calls[0])
        self.assertEqual(calls[0].count("--environment-path"), 2)
        with patch.dict("os.environ", {"RSI_ENVIRONMENT_URL":"http://127.0.0.1:9003",
                                      "RSI_ENVIRONMENT_PATHS":"not-json"}):
            with self.assertRaisesRegex(ValueError, "JSON array"):
                mcp_server.call_tool("rsi_train", values, runner=runner)
        self.assertEqual(len(calls), 1)

    def test_operator_environment_request_budget(self):
        values = {key: "value" for key in mcp_server._REQUIRED}
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")
        env = {"RSI_ENVIRONMENT_URL":"http://127.0.0.1:9003",
               "RSI_ENVIRONMENT_PATHS":'["/api/catalog"]',
               "RSI_ENVIRONMENT_MAX_REQUESTS":"32"}
        with patch.dict("os.environ", env):
            mcp_server.call_tool("rsi_train", values, runner=runner)
        self.assertEqual(calls[0][calls[0].index("--environment-max-requests")+1], "32")
        with patch.dict("os.environ", {**env, "RSI_ENVIRONMENT_MAX_REQUESTS":"65"}):
            with self.assertRaisesRegex(ValueError, "budget"):
                mcp_server.call_tool("rsi_train", values, runner=runner)
        self.assertEqual(len(calls), 1)

    def test_training_judge_is_never_forwarded_to_evaluation(self):
        values = {key: "value" for key in mcp_server._REQUIRED}
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")
        env = {"RSI_ENVIRONMENT_URL":"http://127.0.0.1:9003",
               "RSI_ENVIRONMENT_PATHS":'["/api/catalog"]',
               "RSI_TRAINING_JUDGE_PATH":"/api/judge",
               "RSI_TRAINING_TASK_IDS":'["train_001"]'}
        with patch.dict("os.environ", env):
            mcp_server.call_tool("rsi_train", values, runner=runner)
            mcp_server.call_tool("rsi_evaluate", {**values,"expected_digest":"a" * 64}, runner=runner)
        self.assertIn("--training-judge-path", calls[0])
        self.assertIn("train_001", calls[0])
        self.assertNotIn("--training-judge-path", calls[1])
        self.assertNotIn("--training-task-id", calls[1])

    def test_answer_output_only_for_evaluation(self):
        values = {key: "value" for key in mcp_server._REQUIRED}
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")
        mcp_server.call_tool("rsi_evaluate", {**values,"expected_digest":"a"*64,
                                             "answer_output":"/app/answer.json"}, runner=runner)
        self.assertIn("--answer-output", calls[0])
        with self.assertRaisesRegex(ValueError, "requires an evaluation"):
            mcp_server.call_tool("rsi_train", {**values,"answer_output":"/app/answer.json"}, runner=runner)

    def test_evaluation_requires_trusted_digest(self):
        with self.assertRaises(ValueError):
            mcp_server.call_tool("rsi_evaluate", {key:"value" for key in mcp_server._REQUIRED})


if __name__ == "__main__":
    unittest.main()
