import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from penguin_sessions import PenguinSessions, PenguinSessionError, PenguinRoleClient


class SessionTests(unittest.TestCase):
    def test_start_then_resume_preserves_id_and_omits_initial_options(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {"sessionId":"session-one", "status":"completed", "text":"ok"}))
        host = PenguinSessions(["node", "penguin.js"], "project", "actor", "/work",
                               "provider", "model", runner=runner)
        sid, text = host.turn("first")
        self.assertEqual((sid, text), ("session-one", "ok"))
        host.turn("second", session_id=sid)
        self.assertIn("--workspace", calls[0])
        self.assertIn("--session", calls[1])
        self.assertNotIn("--workspace", calls[1])
        self.assertNotIn("--model-id", calls[1])
        self.assertNotIn("--provider", calls[1])

    def test_role_client_reuses_only_matching_role_history(self):
        class Role:
            def __init__(self, agent):
                self.agent, self.calls = agent, []
            def turn(self, message, session_id=None, approve=None):
                self.calls.append((session_id, approve, message))
                return self.agent + "-session", "ok"
        roles = {name: Role(name) for name in ("actor", "verifier", "curriculum")}
        client = PenguinRoleClient(roles, actor_approve="read-only")
        actor_history, verifier_history = [], []
        client.complete("actor", "actor instructions", "first", history=actor_history)
        client.complete("verifier", "verify", "public", history=verifier_history)
        client.complete("actor", "actor instructions", "second", history=actor_history)
        self.assertEqual(roles["actor"].calls[1][0], "actor-session")
        self.assertEqual(roles["actor"].calls[0][1], "read-only")
        self.assertEqual(roles["verifier"].calls[0][1], "deny-all")
        self.assertIsNone(roles["verifier"].calls[0][0])
        with self.assertRaisesRegex(PenguinSessionError, "across roles"):
            client.complete("verifier", "verify", "public", history=actor_history)

    def test_role_client_rejects_shared_agent(self):
        role = SimpleNamespace(agent="same")
        with self.assertRaisesRegex(ValueError, "distinct agent"):
            PenguinRoleClient({"actor":role, "verifier":role, "curriculum":role})

    def test_unified_controller_uses_separate_host_role_sessions(self):
        from unified_model_host import UnifiedModelHost
        from unified import run_unified
        class Role:
            def __init__(self, agent):
                self.agent, self.calls = agent, []
            def turn(self, message, session_id=None, approve=None):
                self.calls.append((session_id, approve, message))
                if self.agent == "actor":
                    text = '{"answer":"42","evidence":"calculated"}'
                elif self.agent == "verifier":
                    text = '{"verdict":"PASS","report":"candidate matches"}'
                else:
                    text = '{"decision":"HANDOFF","report":"report is sufficient"}'
                return self.agent + "-session", text
        roles = {name: Role(name) for name in ("actor", "verifier", "curriculum")}
        client = PenguinRoleClient(roles)
        result = run_unified(UnifiedModelHost(client), "task", ["actor secret"])
        self.assertEqual(result.status, "PASS")
        self.assertEqual(client.calls, {"actor":1, "verifier":1, "curriculum":1})
        self.assertNotIn("actor secret", roles["verifier"].calls[0][2])
        self.assertEqual(roles["verifier"].calls[0][1], "deny-all")

    def test_detached_or_wrong_session_fails_closed(self):
        for payload in ({"sessionId":"s", "status":"running", "text":""},
                        {"sessionId":"s", "status":"completed", "text":"  "},
                        {"sessionId":"wrong", "status":"completed", "text":"ok"}):
            runner = lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(payload))
            host = PenguinSessions(["penguin"], "p", "a", "/w", "c", "m", runner=runner)
            with self.assertRaises(PenguinSessionError):
                host.turn("test", session_id="s")


if __name__ == "__main__":
    unittest.main()
