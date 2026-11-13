import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from penguin_readonly import create_readonly_session
from penguin_sessions import PenguinSessions


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class Opener:
    def __init__(self, payload):
        self.payload = payload
        self.request = None
    def open(self, request, timeout):
        self.request = request
        return Response(json.dumps(self.payload).encode())


class ReadonlyTests(unittest.TestCase):
    def test_cookie_and_fail_closed_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "cli-session.json").write_text(
                json.dumps({"server":"http://127.0.0.1:1234","token":"test-cookie"}))
            payload = {"session":{"sessionId":"s", "projectId":"p", "agentId":"a",
                       "approvalMode":"deny-all", "sandbox":{"mode":"read-only",
                       "confinementSupported":True}}}
            opener = Opener(payload)
            sid = create_readonly_session("p","a","/work","custom","model",
                                          root=temp, opener=opener)
            self.assertEqual(sid, "s")
            self.assertEqual(opener.request.get_header("Cookie"), "penguin_session=test-cookie")
            self.assertIsNone(opener.request.get_header("Authorization"))
            body = json.loads(opener.request.data)
            self.assertEqual(body["sandbox"]["mode"], "read-only")
            payload["session"]["sandbox"]["confinementSupported"] = False
            with self.assertRaisesRegex(RuntimeError, "did not confirm"):
                create_readonly_session("p","a","/work","custom","model",root=temp,opener=opener)

    def test_local_api_token_is_explicit_and_does_not_use_expired_cookie(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "cli-session.json").write_text(json.dumps({
                "server":"http://127.0.0.1:1234", "token":"expired-cookie",
                "expiresAt":"2000-01-01T00:00:00Z"}))
            Path(temp, "api-token").write_text("synthetic-api-token")
            Path(temp, "server.lock").write_text('{"port":1234}')
            payload = {"session":{"sessionId":"s", "projectId":"p", "agentId":"a",
                       "approvalMode":"deny-all", "sandbox":{"mode":"read-only",
                       "confinementSupported":True}}}
            opener = Opener(payload)
            sid = create_readonly_session("p","a","/work","custom","model",
                                          root=temp, opener=opener, auth_mode="local-api-token")
            self.assertEqual(sid, "s")
            self.assertEqual(opener.request.get_header("Authorization"), "Bearer synthetic-api-token")
            self.assertTrue(opener.request.full_url.startswith("http://localhost:1234/"))
            self.assertIsNone(opener.request.get_header("Cookie"))

    def test_expired_local_sign_in_fails_before_request(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "cli-session.json").write_text(json.dumps({
                "server":"http://127.0.0.1:1234", "token":"test-cookie",
                "expiresAt":"2000-01-01T00:00:00Z"}))
            opener = Opener({})
            with self.assertRaisesRegex(RuntimeError, "expired"):
                create_readonly_session("p","a","/work","c","m",root=temp,opener=opener)
            self.assertIsNone(opener.request)

    def test_remote_server_rejected_before_request(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "cli-session.json").write_text(
                json.dumps({"server":"http://example.com","token":"test-cookie"}))
            with self.assertRaisesRegex(ValueError, "loopback"):
                create_readonly_session("p","a","/work","c","m",root=temp,opener=Opener({}))

    def test_private_mask_must_cover_existing_path_before_session_creation(self):
        class SequentialOpener:
            def __init__(self, replies):
                self.replies = list(replies)
                self.requests = []
            def open(self, request, timeout):
                self.requests.append(request)
                return Response(json.dumps(self.replies.pop(0)).encode())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/"cli-session.json").write_text(json.dumps(
                {"server":"http://127.0.0.1:1234","token":"synthetic"}))
            private = root/"private"
            private.mkdir()
            secret = private/"memory.json"
            secret.write_text("synthetic")
            host = {"plugins":[{"name":"sandbox","values":{"maskPaths":[str(private)]}}]}
            session = {"session":{"sessionId":"s", "projectId":"p", "agentId":"a",
                "approvalMode":"deny-all", "sandbox":{"mode":"read-only",
                "confinementSupported":True, "maskPathsSupported":True, "masksPaths":True}}}
            opener = SequentialOpener([host,session,host])
            sid = create_readonly_session("p","a","/work","c","m",root=temp,
                                          opener=opener,private_paths=[str(secret)])
            self.assertEqual(sid,"s")
            self.assertEqual([x.method for x in opener.requests],["GET","POST","GET"])
            changed = SequentialOpener([host,session,{"plugins":[]}])
            with self.assertRaisesRegex(RuntimeError,"configuration changed"):
                create_readonly_session("p","a","/work","c","m",root=temp,
                                        opener=changed,private_paths=[str(secret)])
            missing = SequentialOpener([{"plugins":[]}])
            with self.assertRaisesRegex(ValueError,"not masked"):
                create_readonly_session("p","a","/work","c","m",root=temp,
                                        opener=missing,private_paths=[str(secret)])
            self.assertEqual(len(missing.requests),1)
            unconfirmed = SequentialOpener([host,{"session":{**session["session"],
                "sandbox":{"mode":"read-only","confinementSupported":True}}},host])
            with self.assertRaisesRegex(RuntimeError,"did not confirm"):
                create_readonly_session("p","a","/work","c","m",root=temp,
                                        opener=unconfirmed,private_paths=[str(secret)])

    def test_redirect_does_not_forward_cookie(self):
        import http.server
        import threading
        seen = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append(self.path)
                self.send_response(307)
                self.send_header("Location", "http://127.0.0.1:%d/leak" % self.server.server_port)
                self.end_headers()
            def log_message(self, *args): pass
        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                Path(temp, "cli-session.json").write_text(json.dumps({
                    "server":"http://127.0.0.1:%d" % server.server_port,
                    "token":"synthetic-cookie"}))
                with self.assertRaisesRegex(RuntimeError, "unable to create"):
                    create_readonly_session("p","a","/work","c","m",root=temp)
            self.assertEqual(len(seen), 1)
            self.assertNotIn("/leak", seen)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_factory_creates_once_and_uses_resume(self):
        from types import SimpleNamespace
        created, calls = [], []
        def factory(*args):
            created.append(args)
            return "session-full"
        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {"status":"completed","sessionId":"session-full","text":"ok"}))
        host = PenguinSessions(["penguin"],"p","a","/w","c","m",
                               runner=runner,session_factory=factory)
        sid,_ = host.turn("one")
        host.turn("two",session_id=sid)
        self.assertEqual(len(created),1)
        self.assertTrue(all("--session" in x and "--workspace" not in x for x in calls))
        with self.assertRaisesRegex(ValueError,"deny-all"):
            host.turn("three",approve="allow-all")


if __name__ == "__main__":
    unittest.main()
