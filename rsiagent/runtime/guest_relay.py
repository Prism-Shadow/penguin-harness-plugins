"""Stdio-to-TCP bridge for one guest connection, ported for ``guestfwd -cmd:``.

QEMU's ``guestfwd=tcp:<guest>:<port>-cmd:<command>`` spawns ``<command>`` once
per guest connection and wires the connection to the process's stdin and stdout.
That per-connection process is the only relay form that survives more than one
connection: the ``-tcp:`` target of QEMU 6.2 connects to the host once per
relay and then never again, so a page needing HTML, CSS and JS gets one response
and then silence.

This program is deliberately tiny. It moves bytes in both directions and adds no
policy: the policy lives in the gateway it connects to, which terminates HTTP and
TLS and decides what is allowed.

Short reads are the whole difficulty. ``BufferedReader.read(n)`` blocks until it
has ``n`` bytes or the stream ends, which would hang until the guest closed the
connection; ``os.read`` returns whatever is available, so the request reaches the
gateway as soon as the guest sends it.
"""
import os
import select
import socket
import sys

CHUNK = 65536


def pump(stdin_fd, stdout_fd, upstream, idle_seconds=300.0):
    """Relay until both directions close; return the number of bytes moved."""
    moved = 0
    stdin_open = True
    while True:
        watched = [upstream] + ([stdin_fd] if stdin_open else [])
        ready, _, _ = select.select(watched, [], [], idle_seconds)
        if not ready:
            return moved
        if upstream in ready:
            try:
                data = upstream.recv(CHUNK)
            except OSError:
                return moved
            if not data:
                return moved
            _write_all(stdout_fd, data)
            moved += len(data)
        if stdin_open and stdin_fd in ready:
            try:
                data = os.read(stdin_fd, CHUNK)
            except OSError:
                data = b""
            if not data:
                stdin_open = False                 # half close: still relay back
                try:
                    upstream.shutdown(socket.SHUT_WR)
                except OSError:
                    return moved
                continue
            try:
                upstream.sendall(data)
            except OSError:
                return moved


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def main(argv):
    if len(argv) != 2 or not argv[1].isdigit():
        raise SystemExit("usage: guest_relay.py <local-tcp-port>")
    try:
        upstream = socket.create_connection(("127.0.0.1", int(argv[1])), 10)
    except OSError as exc:
        raise SystemExit("guest_relay: upstream unavailable: " + str(exc))
    try:
        pump(0, 1, upstream)
    finally:
        upstream.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
