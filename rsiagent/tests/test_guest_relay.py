"""Stdio bridge tests: both directions, short reads, half close, failures."""
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

RELAY = Path(__file__).resolve().parents[1] / "runtime" / "guest_relay.py"


class Origin:
    """A one-shot TCP origin that echoes a fixed HTTP response."""

    def __init__(self, body=b"BRIDG", connections=1):
        self.seen = []
        self.body = body
        self.remaining = connections
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self._listener.settimeout(10)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while self.remaining > 0:
            try:
                connection, _ = self._listener.accept()
            except OSError:
                return
            self.remaining -= 1
            with connection:
                connection.settimeout(8)
                data = b""
                try:
                    while b"\r\n\r\n" not in data:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                except OSError:
                    pass
                self.seen.append(data)
                connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: "
                                   + str(len(self.body)).encode() + b"\r\n\r\n"
                                   + self.body)

    def close(self):
        try:
            self._listener.close()
        except OSError:
            pass


def start(origin, *, half_close=False, wait=6.0):
    """Run the bridge against one socketpair and return what the client saw."""
    client, peer = socket.socketpair()
    process = subprocess.Popen(
        [sys.executable, "-u", str(RELAY), str(origin.port)],
        stdin=peer.fileno(), stdout=peer.fileno(), stderr=subprocess.PIPE,
        pass_fds=(peer.fileno(),))
    peer.close()
    time.sleep(0.35)
    client.sendall(b"GET / HTTP/1.1\r\nHost: allowed.example\r\n\r\n")
    if half_close:
        client.shutdown(socket.SHUT_WR)
    client.settimeout(wait)
    reply = b""
    try:
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            reply += chunk
    except socket.timeout:
        pass
    client.close()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5)
    process.stderr.close()
    return reply, process


class BridgeTests(unittest.TestCase):
    def test_request_and_response_cross_in_both_directions(self):
        origin = Origin()
        try:
            reply, process = start(origin)
        finally:
            origin.close()
        self.assertEqual(process.returncode, 0)
        self.assertEqual(origin.seen, [b"GET / HTTP/1.1\r\nHost: allowed.example\r\n\r\n"])
        self.assertIn(b"200 OK", reply)
        self.assertIn(b"BRIDG", reply)

    def test_a_short_request_is_forwarded_without_waiting_for_a_full_buffer(self):
        """A 40-byte request must not be held back for 64 KiB."""
        origin = Origin()
        client, peer = socket.socketpair()
        process = subprocess.Popen(
            [sys.executable, "-u", str(RELAY), str(origin.port)],
            stdin=peer.fileno(), stdout=peer.fileno(), stderr=subprocess.PIPE,
            pass_fds=(peer.fileno(),))
        peer.close()
        try:
            time.sleep(0.35)
            client.sendall(b"GET / HTTP/1.1\r\nHost: a\r\n\r\n")
            deadline = time.time() + 4
            while time.time() < deadline and not origin.seen:
                time.sleep(0.05)
            self.assertTrue(origin.seen, "the request never reached the origin")
        finally:
            client.close()
            process.terminate()
            origin.close()

    def test_half_close_still_delivers_the_response(self):
        origin = Origin()
        try:
            reply, process = start(origin, half_close=True)
        finally:
            origin.close()
        self.assertEqual(process.returncode, 0)
        self.assertIn(b"BRIDG", reply)

    def test_each_process_serves_one_connection(self):
        """One process per guest connection is the property the relay relies on."""
        origin = Origin(connections=3)
        try:
            replies = [start(origin)[0] for _ in range(3)]
        finally:
            origin.close()
        self.assertEqual(len(origin.seen), 3)
        for reply in replies:
            self.assertIn(b"BRIDG", reply)

    def test_a_large_response_is_relayed_completely(self):
        origin = Origin(body=b"x" * 300000)
        try:
            reply, _process = start(origin)
        finally:
            origin.close()
        self.assertIn(b"Content-Length: 300000", reply)
        self.assertEqual(reply.count(b"x"), 300000)

    def test_missing_argument_is_refused(self):
        result = subprocess.run([sys.executable, str(RELAY)], capture_output=True,
                                text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage", result.stderr + result.stdout)

    def test_unreachable_upstream_exits_nonzero(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()                     # nothing is listening there now
        result = subprocess.run([sys.executable, str(RELAY), str(port)],
                                capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("upstream unavailable", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
