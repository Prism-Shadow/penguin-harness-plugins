"""Live attestation: the egress fence really holds inside a booted guest.

Two boots, no public-internet dependency:

1. null mode — the guest has a NIC but the only reachable destinations are none:
   public TCP and DNS both fail.
2. target mode — the guest reaches exactly one hostname through the host
   gateway, and nothing else. A wrong ``Host`` is still refused inside the
   gateway, and a direct public IP is still dead.
"""
import json
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from vm_host import VMBranch
import target_fence as tf

IMAGE = "/home/yichen/vm-prep.qcow2"
HOST = "streamview.web.hku.icu"
report = {"image": IMAGE, "host": HOST}

BLOCKED_PUBLIC = (
    "set +e\n"
    "if curl --noproxy '*' -fsS --max-time 3 http://1.1.1.1/ >/dev/null 2>&1; "
    "then echo PUBLIC=OPEN; else echo PUBLIC=BLOCKED; fi\n"
    "if getent hosts example.com >/dev/null 2>&1; "
    "then echo DNS=RESOLVED; else echo DNS=BLOCKED; fi\n"
)


class Origin:
    """A tiny local HTTP origin standing in for the allowed website."""

    def __init__(self, body=b"FENCE-OK"):
        self.body = body
        self.requests = []
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self._listener.settimeout(0.5)
        self.port = self._listener.getsockname()[1]
        self._stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._one, args=(connection,),
                             daemon=True).start()

    def _one(self, connection):
        with connection:
            connection.settimeout(10)
            received = b""
            while b"\r\n\r\n" not in received:
                chunk = connection.recv(65536)
                if not chunk:
                    return
                received += chunk
            self.requests.append(received.decode("iso-8859-1", "replace"))
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: "
                               + str(len(self.body)).encode() + b"\r\n\r\n"
                               + self.body)

    def close(self):
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass


def boot(net_args=None):
    return VMBranch(IMAGE, boot_timeout=240, net_args=net_args)


# --- 1. null mode ---------------------------------------------------------
fence = tf.TargetFence(HOST)
null_installation = fence.install("null")
with boot(null_installation.netdev_args) as vm:
    code, output = vm.run(BLOCKED_PUBLIC, timeout=60)
    report["null_mode"] = {"exit": code, "output": output.strip(),
                           "netdev": null_installation.netdev_args[1],
                           "policy_sha256": null_installation.policy_sha256}
    report["null_mode"]["holds"] = (
        "PUBLIC=BLOCKED" in output and "DNS=BLOCKED" in output)
fence.close()

# --- 2. target mode ------------------------------------------------------
origin = Origin()
gateways = []


def factory(bind_ip, allowed_host, **kwargs):
    gateway = tf.FilteringTcpGateway(
        bind_ip, allowed_host, upstream_ips=("127.0.0.1",),
        connect=lambda address, timeout=None: socket.create_connection(
            ("127.0.0.1", origin.port), timeout))
    gateways.append(gateway)
    return gateway


fence = tf.TargetFence(HOST, gateway_factory=factory)
try:
    installation = fence.install("target")
    with boot(installation.netdev_args) as vm:
        code, output = vm.run(fence.guest_setup_command(installation), timeout=180)
        report["guest_identity"] = {"exit": code, "output": output.strip()[-500:]}
        code, output = vm.run(
            "set +e\n"
            "printf 'ALLOWED='; curl --noproxy '*' -fsS --max-time 6 "
            "'http://{host}/page' 2>/dev/null; echo\n"
            "printf 'WRONG_HOST='; curl --noproxy '*' -s --max-time 6 "
            "-H 'Host: evil.example' -o /dev/null -w '%{{http_code}}' "
            "'http://{ip}:80/' 2>/dev/null; echo\n"
            "printf 'PUBLIC='; if curl --noproxy '*' -fsS --max-time 3 "
            "http://1.1.1.1/ >/dev/null 2>&1; then echo OPEN; "
            "else echo BLOCKED; fi\n"
            .format(host=HOST, ip=tf.GATEWAY_GUEST_IP), timeout=90)
        text = output or ""
        report["target_mode"] = {
            "exit": code, "output": text.strip()[-800:],
            "netdev": installation.netdev_args[1],
            "policy": installation.as_dict(),
            "upstream_saw_host": [],
            "holds": ("ALLOWED=FENCE-OK" in text and "WRONG_HOST=421" in text
                      and "PUBLIC=BLOCKED" in text),
        }
        report["target_mode"]["upstream_saw_host"] = [
            line for request in origin.requests
            for line in request.splitlines()
            if line.lower().startswith("host:")]
finally:
    fence.close()
    origin.close()

out = Path(__file__).resolve().parents[1] / "target-fence-report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
print(json.dumps(report, ensure_ascii=False)[:2500])
