"""Cross-process artifact contract tested with a deterministic fake model."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import cli


class FakeClient:
    def __init__(self, base_url, api_key, model):
        self.calls = {}
        self.log = None

    def complete(self, role, system, user, **kwargs):
        self.calls[role] = self.calls.get(role, 0) + 1
        if role == "curriculum" and "planning a BROAD wave" in system:
            return '{"tasks":["training task one","training task two"]}'
        if role == "curriculum" and "RSIAgent Curriculum." in system:
            return '{"decision":"HANDOFF","report":"evidence supports handoff"}'
        if role == "curriculum":
            return '{"decision":"ready"}'
        if role == "verifier" and "independent Verifier." in system:
            return '{"verdict":"PASS","report":"checked candidate"}'
        if role == "verifier":
            return "PASS"
        if "reconciling the complete causal memory" in system:
            return json.dumps({"lessons": [{"condition": "training context",
                                            "action": "inspect",
                                            "outcome": "completed",
                                            "support": "verifier PASS",
                                            "source_ref": "L0001"}]})
        if "distilling experience" in system:
            return json.dumps({"condition":"training context", "action":"inspect",
                               "outcome":"completed", "support":"verifier PASS"})
        return '{"answer":"candidate","evidence":"observed"}'


class CliTests(unittest.TestCase):
    def test_train_then_fresh_evaluation_reuses_snapshot_without_learning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            train = root / "train.json"
            tasks = root / "tasks.json"
            snapshot = root / "memory.json"
            train_report = root / "train-report.json"
            eval_report = root / "eval-report.json"
            train.write_text(json.dumps({"domain":"training domain", "target":"training target",
                                         "held_out_exclusions":["heldout"], "max_practices":1}))
            tasks.write_text(json.dumps({"tasks":["heldout"]}))
            common = ["--base-url","http://127.0.0.1/v1","--model","test-model"]
            with patch("cli.ChatClient", FakeClient), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["train", *common, "--input", str(train),
                                           "--snapshot", str(snapshot), "--report", str(train_report)],
                                          key_stream=io.StringIO("secret")), 0)
                expected = json.loads(train_report.read_text())["snapshot_digest"]
                self.assertEqual(cli.main(["evaluate", *common, "--input", str(tasks),
                                           "--snapshot", str(snapshot), "--report", str(eval_report),
                                           "--expected-digest", expected],
                                          key_stream=io.StringIO("secret")), 0)
            train_result = json.loads(train_report.read_text())
            eval_result = json.loads(eval_report.read_text())
            self.assertEqual(train_result["snapshot_digest"], eval_result["snapshot_digest"])
            self.assertEqual(eval_result["role_calls"], {"actor":1, "verifier":1})
            self.assertEqual(eval_result["results"][0]["verdict"], "PASS")
            self.assertEqual(eval_result["results"][0]["answer"], "candidate")
            self.assertNotIn("actor_reply", eval_report.read_text())
            self.assertNotIn("secret", train_report.read_text() + eval_report.read_text())
            self.assertEqual(os.stat(snapshot).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(eval_report).st_mode & 0o777, 0o600)

    def test_evaluation_writes_public_object_for_benchmark(self):
        from rsi import FrozenMemory
        class ObjectClient(FakeClient):
            def complete(self, role, system, user, **kwargs):
                if role == "actor":
                    self.calls[role] = self.calls.get(role, 0) + 1
                    return '{"answer":{"ticket_id":"T1"},"evidence":"public"}'
                return super().complete(role, system, user, **kwargs)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tasks = root / "tasks.json"
            tasks.write_text('{"tasks":["heldout"]}')
            snapshot = root / "memory.json"
            frozen = FrozenMemory.create([])
            frozen.save(snapshot)
            output = root / "answer.json"
            args = ["evaluate", "--base-url", "http://127.0.0.1/v1", "--model", "test",
                    "--input", str(tasks), "--snapshot", str(snapshot), "--report", str(root / "report"),
                    "--expected-digest", frozen.sha256, "--answer-output", str(output)]
            with patch("cli.ChatClient", ObjectClient), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(args, key_stream=io.StringIO("secret")), 0)
                self.assertEqual(json.loads(output.read_text()), {"ticket_id":"T1"})
                with self.assertRaises(FileExistsError):
                    cli.main(args, key_stream=io.StringIO("secret"))
            self.assertEqual(json.loads(output.read_text()), {"ticket_id":"T1"})

    def test_evaluation_rejects_replaced_self_consistent_snapshot(self):
        from rsi import FrozenMemory
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tasks = root / "tasks.json"
            tasks.write_text('{"tasks":["heldout"]}')
            original = FrozenMemory.create(["trusted"])
            replacement = FrozenMemory.create(["replacement"])
            snapshot = root / "memory.json"
            replacement.save(snapshot)
            with patch("cli.ChatClient", FakeClient), self.assertRaisesRegex(
                    ValueError, "trusted training digest"):
                cli.main(["evaluate", "--base-url", "http://127.0.0.1/v1",
                          "--model", "test", "--input", str(tasks), "--snapshot", str(snapshot),
                          "--report", str(root / "report.json"), "--expected-digest",
                          original.sha256], key_stream=io.StringIO("secret"))
            self.assertFalse((root / "report.json").exists())

    def test_evaluation_rejects_snapshot_replaced_during_execution(self):
        from rsi import FrozenMemory
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tasks = root / "tasks.json"
            tasks.write_text('{"tasks":["heldout"]}')
            snapshot = root / "memory.json"
            original = FrozenMemory.create(["trusted"])
            replacement = FrozenMemory.create(["replacement"])
            original.save(snapshot)
            class ReplacingClient(FakeClient):
                def complete(self, *args, **kwargs):
                    if args[0] == "actor":
                        snapshot.unlink()
                        replacement.save(snapshot)
                    return super().complete(*args, **kwargs)
            report = root / "report.json"
            with patch("cli.ChatClient", ReplacingClient), self.assertRaisesRegex(
                    RuntimeError, "snapshot changed while evaluation"):
                cli.main(["evaluate", "--base-url", "http://127.0.0.1/v1",
                          "--model", "test", "--input", str(tasks), "--snapshot", str(snapshot),
                          "--report", str(report), "--expected-digest", original.sha256],
                         key_stream=io.StringIO("secret"))
            self.assertFalse(report.exists())

    def test_evaluation_requires_trusted_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "requires a trusted"):
                cli.main(["evaluate", "--base-url", "http://127.0.0.1/v1",
                          "--model", "test", "--input", str(root / "tasks.json"),
                          "--snapshot", str(root / "memory.json"),
                          "--report", str(root / "report.json")], key_stream=io.StringIO("secret"))

    def test_penguin_host_runs_without_api_key_and_requires_distinct_roles(self):
        class FakeSessions:
            def __init__(self, executable, project, agent, workspace, provider, model):
                self.agent = agent
        class FakeRoles(FakeClient):
            def __init__(self, roles):
                if len({role.agent for role in roles.values()}) != 3:
                    raise ValueError("roles must use distinct agent IDs")
                super().__init__("", "", "")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            train = root / "train.json"
            train.write_text('{"domain":"practice","target":"target","max_practices":0}')
            args = ["train","--host","penguin","--model","test",
                    "--project","p","--workspace",str(root),"--provider","custom",
                    "--actor-agent","a","--verifier-agent","v","--curriculum-agent","c",
                    "--input",str(train),"--snapshot",str(root/"snapshot"),
                    "--report",str(root/"report")]
            with patch("cli.PenguinSessions", FakeSessions), patch("cli.PenguinRoleClient", FakeRoles), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(args, key_stream=io.StringIO("")), 0)
            report = json.loads((root/"report").read_text())
            self.assertEqual(report["host"], "penguin")
            self.assertEqual(report["target_status"], "PASS")

    def test_existing_snapshot_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "train.json"
            config.write_text('{"domain":"x","target":"y"}')
            snapshot = root / "memory.json"
            snapshot.write_text("unchanged")
            with patch("cli.ChatClient", FakeClient), self.assertRaises(FileExistsError):
                cli.main(["train","--base-url","http://127.0.0.1/v1","--model","test",
                          "--input",str(config),"--snapshot",str(snapshot),
                          "--report",str(root / "report.json")], key_stream=io.StringIO("secret"))
            self.assertEqual(snapshot.read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()
