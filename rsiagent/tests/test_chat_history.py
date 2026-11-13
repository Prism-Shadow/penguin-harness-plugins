import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from model_host import ChatClient, LLMError


class Response(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


class ChatHistoryTests(unittest.TestCase):
    def test_history_sent_and_appended_only_after_success(self):
        history = [{"role": "user", "content": "old question"},
                   {"role": "assistant", "content": "old answer"}]
        requests = []
        def open_request(request, timeout):
            requests.append(json.loads(request.data))
            return Response(json.dumps({"choices":[{"message":{"content":"new answer"}}]}).encode())
        client = ChatClient("http://example.invalid/v1", "secret", "model")
        with patch("urllib.request.urlopen", side_effect=open_request):
            result = client.complete("actor", "system", "new question", history=history)
        self.assertEqual(result, "new answer")
        self.assertEqual([m["content"] for m in requests[0]["messages"]],
                         ["system", "old question", "old answer", "new question"])
        self.assertEqual(history[-2:], [{"role":"user", "content":"new question"},
                                        {"role":"assistant", "content":"new answer"}])

    def test_failed_call_does_not_mutate_history(self):
        history = [{"role":"user", "content":"old"}]
        client = ChatClient("http://example.invalid/v1", "secret", "model")
        with patch("urllib.request.urlopen", side_effect=OSError("offline")):
            with self.assertRaises(LLMError):
                client.complete("actor", "system", "new", history=history)
        self.assertEqual(history, [{"role":"user", "content":"old"}])


if __name__ == "__main__":
    unittest.main()
