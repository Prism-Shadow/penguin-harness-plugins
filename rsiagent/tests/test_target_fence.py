"""Target-fence tests: preface rules, TLS SNI, a live relay, the QEMU receipt."""
import base64
import hashlib
import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import target_fence as tf

HOST = "streamview.web.hku.icu"


# --- fixtures -------------------------------------------------------------


def request(target="/", host=HOST, extra=(), version="HTTP/1.1", method="GET",
            body=b""):
    lines = ["{} {} {}".format(method, target, version)]
    if host is not None:
        lines.append("Host: " + host)
    lines.extend(extra)
    if body:
        lines.append("Content-Length: {}".format(len(body)))
    lines.append("Connection: keep-alive")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1") + body


def client_hello(hostnames, *, include_extension=True, trailing=b""):
    """Build a real TLS record carrying a ClientHello with the given SNIs."""
    body = b"\x03\x03" + b"\x00" * 32 + b"\x00"
    ciphers = b"\x13\x01"
    body += len(ciphers).to_bytes(2, "big") + ciphers
    body += b"\x01\x00"
    if include_extension:
        names = b""
        for name in hostnames:
            encoded = name.encode("ascii")
            names += b"\x00" + len(encoded).to_bytes(2, "big") + encoded
        sni_data = len(names).to_bytes(2, "big") + names
        extension = b"\x00\x00" + len(sni_data).to_bytes(2, "big") + sni_data
        body += len(extension).to_bytes(2, "big") + extension
    else:
        body += b"\x00\x00"
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    record = b"\x16\x03\x01" + len(handshake).to_bytes(2, "big") + handshake
    return record + trailing


class HostnameTests(unittest.TestCase):
    def test_canonical_names(self):
        self.assertEqual(tf.canonical_dns_name("StreamView.Web.HKU.icu"), HOST)

    def test_lookalikes_are_refused(self):
        for bad in ("", " ", "a.", "exa mple.com", "exämple.com", "-a.com",
                    "a-.com", "a..b", "a" * 64 + ".com", "host:80", None):
            with self.assertRaises(tf.PrefaceRejected, msg=repr(bad)):
                tf.canonical_dns_name(bad)


class HttpPrefaceTests(unittest.TestCase):
    def test_a_plain_request_for_the_allowed_host_is_accepted(self):
        self.assertEqual(tf.validate_http_preface(request(), HOST), HOST)

    def test_host_with_the_matching_port_is_accepted(self):
        self.assertEqual(
            tf.validate_http_preface(request(host=HOST + ":80"), HOST), HOST)

    def test_another_host_is_refused(self):
        with self.assertRaises(tf.PrefaceRejected):
            tf.validate_http_preface(request(host="gitlab.web.hku.icu"), HOST)

    def test_lookalike_host_spellings_are_refused(self):
        for bad in (HOST + ".", HOST + ":8080", "user@" + HOST, HOST + "/x",
                    "[" + HOST + "]"):
            with self.assertRaises(tf.PrefaceRejected, msg=bad):
                tf.validate_http_preface(request(host=bad), HOST)

    def test_missing_and_duplicated_host_are_refused(self):
        with self.assertRaises(tf.PrefaceRejected):
            tf.validate_http_preface(request(host=None), HOST)
        with self.assertRaises(tf.PrefaceRejected):
            tf.validate_http_preface(request(extra=("Host: " + HOST,)), HOST)

    def test_connect_and_unknown_versions_are_refused(self):
        with self.assertRaises(tf.PrefaceRejected):
            tf.validate_http_preface(request(method="CONNECT", target="x:443"), HOST)
        with self.assertRaises(tf.PrefaceRejected):
            tf.validate_http_preface(request(version="HTTP/2.0"), HOST)
        with self.assertRaises(tf.PrefaceRejected):
            tf.validate_http_preface(request(method="GE T"), HOST)

    def test_absolute_form_targets_are_checked_independently(self):
        ok = request(target="http://" + HOST + "/a?b=1")
        self.assertEqual(tf.validate_http_preface(ok, HOST), HOST)
        for bad in ("http://gitlab.web.hku.icu/", "https://" + HOST + "/",
                    "http://" + HOST + ":8080/", "http://user:pw@" + HOST + "/"):
            with self.assertRaises(tf.PrefaceRejected, msg=bad):
                tf.validate_http_preface(request(target=bad), HOST)

    def test_malformed_header_lines_are_refused(self):
        raw = b"GET / HTTP/1.1\r\nHost: " + HOST.encode()
        for bad in (raw + b"\r\n X-Folded: 1\r\n\r\n",
                    raw + b"\r\nBadHeader\r\n\r\n",
                    raw + b"\r\n\x00evil: 1\r\n\r\n",
                    raw + b"\r\nX: a\x01b\r\n\r\n",
                    raw + b"\r\nX-Name!: 1\r\n\r\n"):
            with self.assertRaises(tf.PrefaceRejected, msg=repr(bad[-24:])):
                tf.validate_http_preface(bad, HOST)

    def test_incomplete_and_oversized_headers(self):
        with self.assertRaises(tf.PrefaceIncomplete):
            tf.validate_http_preface(b"GET / HTTP/1.1\r\nHost: " + HOST.encode(), HOST)
        with self.assertRaises(tf.PrefaceRejected):
            tf.validate_http_preface(b"GET / HTTP/1.1\r\nX: " + b"a" * 70000, HOST)


class FramingTests(unittest.TestCase):
    def test_host_is_rewritten_and_close_is_forced(self):
        prepared = tf.prepare_single_http_request(
            request(target="http://" + HOST + "/a?b=1",
                    extra=("X-Test: 1", "Forwarded: for=x")), HOST)
        header = prepared.header.decode()
        self.assertIn("GET /a?b=1 HTTP/1.1\r\n", header)
        self.assertEqual(header.count("Host: "), 1)
        self.assertIn("Host: " + HOST + "\r\n", header)
        self.assertIn("Connection: close\r\n", header)
        self.assertNotIn("Forwarded", header)
        self.assertIn("X-Test: 1", header)

    def test_body_is_carried_and_counted(self):
        prepared = tf.prepare_single_http_request(
            request(method="POST", body=b"hello"), HOST)
        self.assertEqual(prepared.content_length, 5)
        self.assertEqual(prepared.initial_body, b"hello")

    def test_forbidden_framing_is_refused(self):
        for extra in (("Transfer-Encoding: chunked",), ("Expect: 100-continue",),
                      ("Upgrade: websocket",), ("TE: trailers",),
                      ("Content-Length: 5", "Content-Length: 6")):
            with self.assertRaises(tf.PrefaceRejected, msg=str(extra)):
                tf.prepare_single_http_request(request(extra=extra), HOST)

    def test_bytes_beyond_the_declared_body_are_refused(self):
        smuggled = request(method="POST", body=b"hello") + b"GET /evil HTTP/1.1\r\n"
        with self.assertRaises(tf.PrefaceRejected):
            tf.prepare_single_http_request(smuggled, HOST)


class TlsTests(unittest.TestCase):
    def test_sni_is_extracted(self):
        self.assertEqual(tf.parse_tls_client_hello_sni(client_hello([HOST])), HOST)

    def test_validate_rejects_a_co_tenant(self):
        data = client_hello(["gitlab.web.hku.icu"])
        with self.assertRaises(tf.PrefaceRejected):
            tf.validate_tls_preface(data, HOST)
        self.assertEqual(tf.validate_tls_preface(client_hello([HOST]), HOST), HOST)

    def test_absent_or_multiple_sni_is_refused(self):
        with self.assertRaises(tf.PrefaceRejected):
            tf.parse_tls_client_hello_sni(client_hello([], include_extension=False))
        with self.assertRaises(tf.PrefaceRejected):
            tf.parse_tls_client_hello_sni(client_hello([HOST, "other.example"]))
        with self.assertRaises(tf.PrefaceRejected):
            tf.parse_tls_client_hello_sni(client_hello([HOST + "."]))

    def test_truncated_records_are_incomplete(self):
        data = client_hello([HOST])
        for cut in (2, 5, 12, len(data) - 1):
            with self.assertRaises(tf.PrefaceIncomplete, msg=cut):
                tf.parse_tls_client_hello_sni(data[:cut])

    def test_non_handshake_bytes_are_refused(self):
        with self.assertRaises(tf.PrefaceRejected):
            tf.parse_tls_client_hello_sni(b"GET / HTTP/1.1\r\n\r\n")
        with self.assertRaises(tf.PrefaceRejected):
            tf.parse_tls_client_hello_sni(b"\x16\x02\x01\x00\x02\x01\x00")


# --- a live relay ---------------------------------------------------------


class Origin:
    """A one-shot HTTP origin that records exactly what reached it."""

    def __init__(self, status=b"200 OK", body=b"ok"):
        self.received = b""
        self.status = status
        self.body = body
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(4)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            connection, _ = self._listener.accept()
        except OSError:
            return
        with connection:
            connection.settimeout(5)
            while b"\r\n\r\n" not in self.received:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                self.received += chunk
            connection.sendall(b"HTTP/1.1 " + self.status + b"\r\n"
                               b"Content-Length: " + str(len(self.body)).encode()
                               + b"\r\n\r\n" + self.body)

    def close(self):
        try:
            self._listener.close()
        except OSError:
            pass


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.origin = Origin()

    def tearDown(self):
        self.origin.close()

    def _gateway(self):
        return tf.FilteringTcpGateway(
            "127.0.0.1", HOST, upstream_ips=("127.0.0.1",),
            connect=lambda address, timeout=None: socket.create_connection(
                ("127.0.0.1", self.origin.port), timeout))

    def _exchange(self, payload, timeout=6):
        with self._gateway() as gateway:
            client = socket.create_connection(("127.0.0.1", gateway.ports.http),
                                              timeout)
            try:
                client.sendall(payload)
                reply = b""
                while True:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    reply += chunk
                return reply
            finally:
                client.close()

    def test_an_allowed_request_is_relayed_with_a_canonical_host(self):
        reply = self._exchange(request(target="/index.html"))
        self.assertIn(b"200 OK", reply)
        self.assertIn(b"ok", reply)
        self.assertIn(b"Host: " + HOST.encode(), self.origin.received)
        self.assertIn(b"Connection: close", self.origin.received)

    def test_a_co_tenant_request_is_misdirected_and_never_reaches_upstream(self):
        reply = self._exchange(request(host="gitlab.web.hku.icu"))
        self.assertIn(b"421", reply)
        self.assertEqual(self.origin.received, b"")

    def test_an_idle_relay_is_not_closed_before_the_guest_speaks(self):
        """QEMU pre-connects the relay at boot; the request arrives much later."""
        with self._gateway() as gateway:
            gateway._preface_timeout = 5.0
            client = socket.create_connection(("127.0.0.1", gateway.ports.http), 6)
            try:
                time.sleep(1.5)                      # a long pre-request pause
                client.sendall(request(target="/late"))
                reply = b""
                while True:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    reply += chunk
            finally:
                client.close()
        self.assertIn(b"200 OK", reply)
        self.assertIn(b"Host: " + HOST.encode(), self.origin.received)

    def test_a_silent_connection_is_dropped_after_its_idle_allowance(self):
        with self._gateway() as gateway:
            gateway._preface_timeout = 0.3
            client = socket.create_connection(("127.0.0.1", gateway.ports.http), 5)
            try:
                time.sleep(0.8)
                reply = client.recv(65536)
            finally:
                client.close()
        self.assertEqual(self.origin.received, b"")
        self.assertIn(reply[:3], (b"", b"HTT"))

    def test_a_smuggled_second_request_is_never_relayed(self):
        payload = (request(method="POST", body=b"hello")
                   + b"GET /evil HTTP/1.1\r\nHost: " + HOST.encode() + b"\r\n\r\n")
        reply = self._exchange(payload)
        self.assertIn(b"421", reply)
        self.assertNotIn(b"GET /evil", self.origin.received)


# --- policy receipt and QEMU wiring ---------------------------------------


class ManifestTests(unittest.TestCase):
    def test_digest_is_stable_and_host_specific(self):
        first = tf.egress_policy_sha256()
        self.assertEqual(first, tf.egress_policy_sha256())
        self.assertNotEqual(first, tf.egress_policy_sha256("other.example"))
        self.assertEqual(len(first), 64)

    def test_manifest_declares_the_boundary_and_blocks(self):
        manifest = tf.egress_policy_manifest()
        self.assertEqual(manifest["enforcement_boundary"],
                         "qemu-user-netdev-restrict-plus-host-gateway")
        self.assertEqual(manifest["target_public_egress"]["host"], HOST)
        self.assertIn("direct-ip-bypass", manifest["blocked"])
        self.assertEqual(manifest["null_project_public_egress"], [])
        # Ports are ephemeral, so the receipt must not contain them.
        self.assertNotIn("ports", json.dumps(manifest["blocked"]))


class FenceTests(unittest.TestCase):
    def test_null_mode_has_no_network_path_at_all(self):
        fence = tf.TargetFence()
        installation = fence.install("null")
        netdev = installation.netdev_args[1]
        self.assertIn("restrict=on", netdev)
        self.assertNotIn("guestfwd", netdev)
        self.assertEqual(installation.pinned_upstream_ips, ())
        self.assertTrue(tf.TargetFence.verify_netdev(installation))
        self.assertEqual(fence.guest_setup_command(installation), "true")
        fence.close()

    def test_target_mode_relays_exactly_two_ports_to_the_gateway(self):
        fence = tf.TargetFence(gateway_factory=None)
        origin = Origin()
        try:
            gateway = tf.FilteringTcpGateway("127.0.0.1", HOST,
                                             upstream_ips=("127.0.0.1",))
            with gateway:
                ports = gateway.ports
                fence._gateway = gateway
                installation = fence.install("target")
                netdev = installation.netdev_args[1]
                self.assertEqual(netdev.count("guestfwd="), 2)
                # Each relay must be the per-connection -cmd: form; the
                # -tcp: form silently stops after one connection on QEMU 6.2.
                self.assertEqual(netdev.count("-cmd:"), 2)
                self.assertNotIn("-tcp:127.0.0.1", netdev)
                self.assertIn("tcp:{}:80-cmd:".format(tf.GATEWAY_GUEST_IP),
                              netdev)
                self.assertIn("tcp:{}:443-cmd:".format(tf.GATEWAY_GUEST_IP),
                              netdev)
                self.assertIn("guest_relay.py {}".format(ports.http), netdev)
                self.assertIn("guest_relay.py {}".format(ports.https), netdev)
                self.assertEqual(installation.gateway_ports.http, ports.http)
                self.assertEqual(len(installation.gateway_ca_sha256), 64)
                self.assertEqual(len(installation.gateway_leaf_spki_sha256), 64)
        finally:
            origin.close()
            fence.close()

    def test_a_tampered_netdev_is_refused(self):
        fence = tf.TargetFence()
        installation = fence.install("null")
        broken = tf.FenceInstallation(
            mode="null", policy_sha256=installation.policy_sha256,
            gateway_guest_ip=tf.GATEWAY_GUEST_IP, gateway_ports=None,
            gateway_ca_sha256=None, gateway_leaf_spki_sha256=None,
            pinned_upstream_ips=(), netdev_args=("-netdev", "user,id=n0"))
        with self.assertRaises(tf.EgressError):
            tf.TargetFence.verify_netdev(broken)
        relaying = tf.FenceInstallation(
            mode="null", policy_sha256=installation.policy_sha256,
            gateway_guest_ip=tf.GATEWAY_GUEST_IP, gateway_ports=None,
            gateway_ca_sha256=None, gateway_leaf_spki_sha256=None,
            pinned_upstream_ips=(),
            netdev_args=("-netdev", "user,id=n0,restrict=on,"
                                   "guestfwd=tcp:{}:80-tcp:127.0.0.1:1"
                         .format(tf.GATEWAY_GUEST_IP)))
        with self.assertRaises(tf.EgressError):
            tf.TargetFence.verify_netdev(relaying)
        fence.close()

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(tf.EgressError):
            tf.TargetFence().install("promiscuous")

    def test_guest_setup_pins_the_host_and_trusts_the_ephemeral_ca(self):
        gateways = []

        def factory(bind_ip, allowed_host, **kwargs):
            gateway = tf.FilteringTcpGateway(bind_ip, allowed_host,
                                             upstream_ips=("127.0.0.1",))
            gateways.append(gateway)
            return gateway

        fence = tf.TargetFence(gateway_factory=factory)
        try:
            installation = fence.install("target")
            command = fence.guest_setup_command(installation)
            self.assertIn("/usr/local/share/ca-certificates/target-egress.crt", command)
            self.assertIn("update-ca-certificates", command)
            self.assertIn("# RSI_TARGET_EGRESS", command)
            self.assertIn(HOST, command)
            self.assertIn(tf.GATEWAY_GUEST_IP, command)
        finally:
            fence.close()

    def test_probe_command_expects_the_boundary_to_hold(self):
        fence = tf.TargetFence()
        installation = fence.install("null")
        command = fence.probe_command(installation)
        self.assertIn("RSI_PROBE_EXACT=BLOCKED", command)
        self.assertIn("RSI_PROBE_WRONG_SNI=OPEN", command)   # expected==OPEN fails
        self.assertIn("RSI_PROBE_DONE=1", command)
        fence.close()

    def test_probe_fails_closed_when_a_marker_is_missing(self):
        fence = tf.TargetFence()
        installation = fence.install("null")

        class Branch:
            def run(self, command, timeout=None):
                return 0, "RSI_PROBE_DONE=1\nRSI_PROBE_EXACT=BLOCKED\n"

        with self.assertRaises(tf.EgressError) as caught:
            fence.probe(Branch(), installation)
        self.assertIn("WRONG_SNI", str(caught.exception))
        fence.close()


class IdentityTests(unittest.TestCase):
    def test_leaf_names_the_exact_host_and_digest_is_reusable(self):
        identity = tf.new_gateway_identity(HOST)
        self.assertIn(b"BEGIN CERTIFICATE", identity.leaf_pem)
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        leaf = x509.load_pem_x509_certificate(identity.leaf_pem)
        san = leaf.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
        self.assertEqual(san.get_values_for_type(x509.DNSName), [HOST])
        spki = leaf.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        self.assertEqual(identity.leaf_spki_sha256, hashlib.sha256(spki).hexdigest())
        self.assertEqual(identity.leaf_spki_sha256_b64,
                         base64.b64encode(hashlib.sha256(spki).digest()).decode())


if __name__ == "__main__":
    unittest.main()
