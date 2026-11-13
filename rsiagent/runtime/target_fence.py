"""Host-enforced public-egress boundary, ported from ``explore/target_isolation.py``.

The Actor is root inside the guest, so a guest-side firewall is not a security
boundary: anything the guest can rewrite, the Actor can rewrite. Upstream puts
the policy one layer out, in the OSWorld Docker container that owns QEMU's tap.
This plugin has no Docker container, so the same policy is installed one layer
out in **QEMU's own user-mode network stack**, which the guest cannot reach:

    -netdev user,id=n0,restrict=on
      ,guestfwd=tcp:10.0.2.100:80-cmd:<python> guest_relay.py P1
      ,guestfwd=tcp:10.0.2.100:443-cmd:<python> guest_relay.py P2

``restrict=on`` means the guest's only reachable destinations are the
``guestfwd`` relays; every other packet is dropped before it leaves QEMU. The
relays are this process's ``FilteringTcpGateway``, which terminates the
connection and admits exactly one framed HTTP request whose ``Host`` — and, for
HTTPS, whose TLS SNI *and then* inner ``Host`` — is the one allowed origin.

Why ``-cmd:`` and not the simpler ``-tcp:`` target: on QEMU 6.2 the ``-tcp:``
form connects to the host **once per relay for the lifetime of the VM**. Measured
here: three guest connections produced exactly one host connection, one request
and then silence, which is useless for a page that needs HTML, CSS and JS. The
``-cmd:`` form spawns the command once per guest connection (measured: three
guest connections, three processes, three requests served), so ``guest_relay.py``
bridges stdio to the gateway and the gateway stays a long-lived listener.

Target mode authorises exactly one hostname. Null-project mode authorises no
guest-initiated public traffic at all. Both modes are fail-closed: an
unparseable preface, a second ``Host`` header, a smuggled authority, a
non-ASCII or lookalike spelling, an unexpected port, a body past its declared
``Content-Length``, or a request that would re-use the connection is refused.
"""
import base64
import hashlib
import ipaddress
import json
import re
import socket
import ssl
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ALLOWED_HOST = "streamview.web.hku.icu"
POLICY_VERSION = "target-egress-seal-v2-qemu-user-netdev"
GATEWAY_GUEST_IP = "10.0.2.100"
MAX_PREFACE_BYTES = 65_536


class EgressError(RuntimeError):
    """The egress boundary could not be installed or verified."""


class PrefaceIncomplete(ValueError):
    """More bytes are needed before the connection preface can be judged."""


class PrefaceRejected(ValueError):
    """The preface is complete and is not allowed."""


def canonical_dns_name(value):
    """Return a strict ASCII DNS name without accepting lookalike spellings."""

    if not isinstance(value, str):
        raise PrefaceRejected("hostname is not text")
    if not value or value != value.strip() or value.endswith("."):
        raise PrefaceRejected("hostname is empty, padded, or has a trailing dot")
    try:
        encoded = value.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        raise PrefaceRejected("hostname is not ASCII") from exc
    lowered = encoded.decode("ascii").lower()
    labels = lowered.split(".")
    for label in labels:
        if (not label or len(label) > 63 or label[0] == "-" or label[-1] == "-"
                or any(not (character.isalnum() or character == "-")
                       for character in label)):
            raise PrefaceRejected("hostname is not a canonical DNS name")
    return lowered


def _parse_http_authority(value, expected_port):
    """Parse a Host/URI authority, accepting only an optional matching port."""

    if "\r" in value or "\n" in value:
        raise PrefaceRejected("HTTP authority contains a line break")
    value = value.strip(" \t")
    if not value or "@" in value or "[" in value:
        raise PrefaceRejected("HTTP authority is malformed")
    if ":" in value:
        host, separator, port_text = value.rpartition(":")
        if not separator or not port_text.isdigit() or int(port_text) != expected_port:
            raise PrefaceRejected("HTTP authority uses an unexpected port")
    else:
        host = value
    return canonical_dns_name(host)


def validate_http_preface(data, allowed_host=ALLOWED_HOST, expected_port=80,
                          expected_scheme="http"):
    """Validate one complete HTTP request header; return its canonical Host.

    The upstream destination is never taken from the request: callers connect
    the configured ``allowed_host``. Absolute-form targets are checked
    independently so a proxy-style request cannot smuggle another authority.
    """
    marker = data.find(b"\r\n\r\n")
    if marker < 0:
        if len(data) > MAX_PREFACE_BYTES:
            raise PrefaceRejected("HTTP header exceeds the egress-gateway limit")
        raise PrefaceIncomplete("HTTP header is incomplete")
    if marker + 4 > MAX_PREFACE_BYTES:
        raise PrefaceRejected("HTTP header exceeds the egress-gateway limit")
    header = data[:marker].decode("iso-8859-1", "strict")
    unfolded = header.replace("\r\n", "")
    if ("\x00" in header or "\r" in unfolded or "\n" in unfolded
            or any(ord(character) < 32 and character != "\t"
                   for character in unfolded)
            or "\x7f" in unfolded):
        raise PrefaceRejected("HTTP header contains invalid controls")
    lines = header.split("\r\n")
    if not lines or "\t" in lines[0] or len(lines[0].split(" ")) != 3:
        raise PrefaceRejected("HTTP request line is malformed")
    method, target, version = lines[0].split(" ")
    if (not method or not method.isascii() or not method.isalpha()
            or version not in {"HTTP/1.0", "HTTP/1.1"}
            or method.upper() == "CONNECT"):
        raise PrefaceRejected("HTTP method or version is not allowed")

    expected = canonical_dns_name(allowed_host)
    hosts = []
    for line in lines[1:]:
        if not line or line[0] in " \t" or ":" not in line:
            raise PrefaceRejected("HTTP header line is malformed or folded")
        name, value = line.split(":", 1)
        if (not name or not name.isascii()
                or any(not (character.isalnum() or character == "-")
                       for character in name)):
            raise PrefaceRejected("HTTP header name is malformed")
        if name.lower() == "host":
            hosts.append(_parse_http_authority(value, expected_port))
    if len(hosts) != 1 or hosts[0] != expected:
        raise PrefaceRejected("HTTP Host is absent, duplicated, or not allowed")

    if target.startswith(("http://", "https://")):
        parsed = urlsplit(target)
        if (parsed.scheme != expected_scheme or not parsed.netloc
                or parsed.username is not None or parsed.password is not None):
            raise PrefaceRejected("absolute HTTP target is malformed")
        try:
            target_port = parsed.port or expected_port
        except ValueError as exc:
            raise PrefaceRejected("absolute HTTP target port is malformed") from exc
        if (target_port != expected_port
                or canonical_dns_name(parsed.hostname or "") != expected):
            raise PrefaceRejected("absolute HTTP target is not allowed")
    elif target != "*" and not target.startswith("/"):
        raise PrefaceRejected("HTTP target form is not allowed")
    return expected


@dataclass(frozen=True)
class PreparedHttpRequest:
    """One framed request: normalized header, declared body, body seen so far."""

    header: bytes
    content_length: int
    initial_body: bytes


def prepare_single_http_request(data, allowed_host=ALLOWED_HOST, expected_port=80,
                                expected_scheme="http"):
    """Frame and normalize exactly one HTTP/1.x request.

    Transfer coding, protocol upgrades, ``Expect``, duplicated Content-Length
    and bytes beyond the declared body are rejected. The sole Host is rewritten
    to the canonical origin and ``Connection: close`` is forced, so the relay
    never becomes a reusable tunnel to another virtual host.
    """
    expected = validate_http_preface(data, allowed_host,
                                     expected_port=expected_port,
                                     expected_scheme=expected_scheme)
    marker = data.find(b"\r\n\r\n")
    header_end = marker + 4
    lines = data[:marker].decode("iso-8859-1").split("\r\n")
    method, target, version = lines[0].split(" ")
    if target.startswith(("http://", "https://")):
        parsed = urlsplit(target)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query

    content_lengths = []
    normalized = ["{} {} {}".format(method, target, version)]
    for line in lines[1:]:
        name, value = line.split(":", 1)
        lowered = name.lower()
        stripped = value.strip(" \t")
        if lowered == "host":
            continue
        if lowered == "content-length":
            if not stripped.isdigit():
                raise PrefaceRejected("Content-Length is not a decimal integer")
            content_lengths.append(int(stripped))
            continue
        if lowered in {"transfer-encoding", "expect", "upgrade", "te", "trailer"}:
            raise PrefaceRejected(
                "HTTP request uses forbidden framing header " + name)
        if lowered in {"connection", "proxy-connection", "keep-alive"}:
            continue
        if lowered in {"forwarded", "x-forwarded-host", "x-forwarded-server",
                       "x-original-host"}:
            continue
        normalized.append("{}: {}".format(name, stripped))
    if len(content_lengths) > 1:
        raise PrefaceRejected("HTTP request has ambiguous Content-Length")
    content_length = content_lengths[0] if content_lengths else 0
    initial_body = data[header_end:]
    if len(initial_body) > content_length:
        raise PrefaceRejected(
            "HTTP connection contains bytes beyond its sole declared request")
    host_value = (expected if expected_port in (80, 443)
                  else "{}:{}".format(expected, expected_port))
    normalized.append("Host: " + host_value)
    if content_lengths:
        normalized.append("Content-Length: {}".format(content_length))
    normalized.append("Connection: close")
    header = ("\r\n".join(normalized) + "\r\n\r\n").encode("iso-8859-1", "strict")
    return PreparedHttpRequest(header, content_length, initial_body)


# --- TLS ------------------------------------------------------------------


def _take(data, offset, size):
    end = offset + size
    if size < 0 or end > len(data):
        raise PrefaceRejected("TLS ClientHello is truncated internally")
    return data[offset:end], end


def _parse_client_hello_body(body):
    """Extract the sole host_name entry from a complete ClientHello body."""

    offset = 0
    _, offset = _take(body, offset, 2 + 32)          # legacy_version + random
    session_size_b, offset = _take(body, offset, 1)
    _, offset = _take(body, offset, session_size_b[0])
    cipher_size_b, offset = _take(body, offset, 2)
    cipher_size = int.from_bytes(cipher_size_b, "big")
    if cipher_size == 0 or cipher_size % 2:
        raise PrefaceRejected("TLS cipher-suite vector is malformed")
    _, offset = _take(body, offset, cipher_size)
    compression_size_b, offset = _take(body, offset, 1)
    if compression_size_b[0] == 0:
        raise PrefaceRejected("TLS compression vector is empty")
    _, offset = _take(body, offset, compression_size_b[0])
    extensions_size_b, offset = _take(body, offset, 2)
    extensions, offset = _take(body, offset, int.from_bytes(extensions_size_b, "big"))
    if offset != len(body):
        raise PrefaceRejected("TLS ClientHello has trailing bytes")

    sni_extensions = 0
    host_names = []
    ext_offset = 0
    while ext_offset < len(extensions):
        ext_type_b, ext_offset = _take(extensions, ext_offset, 2)
        ext_size_b, ext_offset = _take(extensions, ext_offset, 2)
        ext_data, ext_offset = _take(
            extensions, ext_offset, int.from_bytes(ext_size_b, "big"))
        if int.from_bytes(ext_type_b, "big") != 0:
            continue
        sni_extensions += 1
        if len(ext_data) < 2:
            raise PrefaceRejected("TLS SNI extension is malformed")
        names_size = int.from_bytes(ext_data[:2], "big")
        if names_size != len(ext_data) - 2:
            raise PrefaceRejected("TLS SNI list length is malformed")
        name_offset = 2
        while name_offset < len(ext_data):
            name_type_b, name_offset = _take(ext_data, name_offset, 1)
            name_size_b, name_offset = _take(ext_data, name_offset, 2)
            name_b, name_offset = _take(
                ext_data, name_offset, int.from_bytes(name_size_b, "big"))
            if name_type_b[0] == 0:
                try:
                    host_names.append(name_b.decode("ascii", "strict"))
                except UnicodeDecodeError as exc:
                    raise PrefaceRejected("TLS SNI host is not ASCII") from exc
    if ext_offset != len(extensions):
        raise PrefaceRejected("TLS extension vector is malformed")
    if sni_extensions != 1 or len(host_names) != 1:
        raise PrefaceRejected("TLS requires exactly one SNI host_name")
    return canonical_dns_name(host_names[0])


def parse_tls_client_hello_sni(data):
    """Parse TLS records through a complete ClientHello and return exact SNI."""

    if len(data) > MAX_PREFACE_BYTES:
        raise PrefaceRejected("TLS ClientHello exceeds the gateway limit")
    records_offset = 0
    handshake = bytearray()
    expected_handshake_size = None
    while True:
        if len(data) - records_offset < 5:
            raise PrefaceIncomplete("TLS record header is incomplete")
        content_type = data[records_offset]
        legacy_major = data[records_offset + 1]
        record_size = int.from_bytes(data[records_offset + 3:records_offset + 5], "big")
        if content_type != 22 or legacy_major != 3 or record_size > 18432:
            raise PrefaceRejected(
                "connection does not begin with TLS handshake records")
        record_end = records_offset + 5 + record_size
        if record_end > len(data):
            raise PrefaceIncomplete("TLS record body is incomplete")
        handshake.extend(data[records_offset + 5:record_end])
        records_offset = record_end
        if len(handshake) >= 4 and expected_handshake_size is None:
            if handshake[0] != 1:
                raise PrefaceRejected("first TLS handshake is not ClientHello")
            expected_handshake_size = 4 + int.from_bytes(handshake[1:4], "big")
            if expected_handshake_size > MAX_PREFACE_BYTES:
                raise PrefaceRejected("TLS ClientHello is too large")
        if (expected_handshake_size is not None
                and len(handshake) >= expected_handshake_size):
            return _parse_client_hello_body(bytes(handshake[4:expected_handshake_size]))
        if records_offset == len(data):
            raise PrefaceIncomplete("TLS ClientHello spans another record")


def validate_tls_preface(data, allowed_host=ALLOWED_HOST):
    """The SNI must name exactly the allowed host, spelled canonically."""

    host = parse_tls_client_hello_sni(data)
    if host != canonical_dns_name(allowed_host):
        raise PrefaceRejected("TLS SNI is not allowed")
    return host


# --- gateway --------------------------------------------------------------


@dataclass(frozen=True)
class GatewayPorts:
    http: int
    https: int


@dataclass(frozen=True)
class GatewayIdentity:
    """Ephemeral CA/leaf identity retained only by the trusted host process."""

    ca_pem: bytes
    leaf_pem: bytes
    private_key_pem: bytes
    leaf_spki_sha256: str
    leaf_spki_sha256_b64: str


def new_gateway_identity(hostname):
    """Create a short-lived CA and an exact-host leaf for TLS interception."""

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    canonical = canonical_dns_name(hostname)
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                                            "RSI TARGET ephemeral egress CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            key_cert_sign=True, crl_sign=True, encipher_only=False,
            decipher_only=False), True)
        .sign(ca_key, hashes.SHA256()))
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, canonical)]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(canonical)]), False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False,
            key_encipherment=False, data_encipherment=False, key_agreement=True,
            key_cert_sign=False, crl_sign=False, encipher_only=False,
            decipher_only=False), True)
        .sign(ca_key, hashes.SHA256()))
    spki = leaf_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    return GatewayIdentity(
        ca_pem=ca_cert.public_bytes(serialization.Encoding.PEM),
        leaf_pem=leaf_cert.public_bytes(serialization.Encoding.PEM),
        private_key_pem=leaf_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()),
        leaf_spki_sha256=hashlib.sha256(spki).hexdigest(),
        leaf_spki_sha256_b64=base64.b64encode(hashlib.sha256(spki).digest())
                                   .decode("ascii"))


def _server_ssl_context(identity, allowed_host):
    """Load the ephemeral key without leaving it on disk after setup."""

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(["http/1.1"])
    with tempfile.TemporaryDirectory(prefix="target-egress-cert-") as temp:
        cert_path = Path(temp) / "leaf.pem"
        key_path = Path(temp) / "leaf.key"
        cert_path.write_bytes(identity.leaf_pem)
        key_path.write_bytes(identity.private_key_pem)
        key_path.chmod(0o600)
        context.load_cert_chain(str(cert_path), str(key_path))

    expected = canonical_dns_name(allowed_host)

    def require_exact_sni(_socket, server_name, _context):
        try:
            observed = canonical_dns_name(server_name or "")
        except PrefaceRejected:
            return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
        if observed != expected:
            return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
        return None

    context.set_servername_callback(require_exact_sni)
    return context


class FilteringTcpGateway:
    """Host-side HTTP/TLS terminator enforcing the exact origin at both layers."""

    def __init__(self, bind_ip, allowed_host=ALLOWED_HOST, connect=None,
                 max_connections=64, handshake_timeout=10.0, identity=None,
                 upstream_ips=None, preface_timeout=180.0):
        ipaddress.ip_address(bind_ip)
        self.bind_ip = bind_ip
        self.allowed_host = canonical_dns_name(allowed_host)
        self._connect = connect or socket.create_connection
        self._handshake_timeout = handshake_timeout
        # QEMU's slirp opens the host side of a ``guestfwd`` relay as soon as
        # the guest's connection is established, which happens at boot — tens of
        # seconds before the first request. Measured on QEMU 6.2: the relay
        # socket is connected 39 s before the guest's payload arrives. A short
        # idle deadline therefore closes the socket before the guest ever
        # speaks, and every later request on that relay is lost. This timeout is
        # the idle allowance for the *guest*, not for the upstream.
        self._preface_timeout = preface_timeout
        self.upstream_ips = tuple(upstream_ips or self._resolve_upstream_ips())
        self.identity = identity or new_gateway_identity(self.allowed_host)
        self._server_context = _server_ssl_context(self.identity, self.allowed_host)
        self._upstream_context = ssl.create_default_context()
        self._upstream_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._upstream_context.set_alpn_protocols(["http/1.1"])
        self._slots = threading.BoundedSemaphore(max_connections)
        self._stop = threading.Event()
        self._listeners = []
        self._threads = []
        self._ports = None

    def _resolve_upstream_ips(self):
        """Resolve once in trusted host context; the Actor never chooses an IP."""

        try:
            infos = socket.getaddrinfo(self.allowed_host, 443,
                                       socket.AF_INET, socket.SOCK_STREAM)
        except OSError as exc:
            raise EgressError(
                "could not resolve the allowed origin " + self.allowed_host) from exc
        addresses = tuple(sorted({str(info[4][0]) for info in infos}))
        if not addresses:
            raise EgressError("the allowed origin has no IPv4 address")
        for text_ip in addresses:
            address = ipaddress.ip_address(text_ip)
            if address.version != 4 or not address.is_global:
                raise EgressError("the allowed origin resolved outside public IPv4")
        return addresses

    @property
    def ports(self):
        if self._ports is None:
            raise EgressError("filtering gateway has not started")
        return self._ports

    def start(self):
        if self._ports is not None:
            return self._ports
        listeners = []
        try:
            for _protocol in ("http", "https"):
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((self.bind_ip, 0))
                listener.listen(128)
                listener.settimeout(0.5)
                listeners.append(listener)
        except Exception:
            for listener in listeners:
                listener.close()
            raise
        self._listeners = listeners
        self._ports = GatewayPorts(http=listeners[0].getsockname()[1],
                                   https=listeners[1].getsockname()[1])
        for listener, protocol in zip(listeners, ("http", "https")):
            thread = threading.Thread(target=self._accept_loop,
                                      args=(listener, protocol), daemon=True,
                                      name="target-egress-" + protocol)
            thread.start()
            self._threads.append(thread)
        return self._ports

    def close(self):
        self._stop.set()
        for listener in self._listeners:
            try:
                listener.close()
            except OSError:
                pass
        for thread in self._threads:
            thread.join(timeout=2)
        self._listeners = []
        self._threads = []
        self._ports = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_exc):
        self.close()

    def _accept_loop(self, listener, protocol):
        while not self._stop.is_set():
            try:
                client, _address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if not self._slots.acquire(blocking=False):
                client.close()
                continue
            threading.Thread(target=self._handle_guarded, args=(client, protocol),
                             daemon=True).start()

    def _handle_guarded(self, client, protocol):
        try:
            self._handle(client, protocol)
        except (OSError, PrefaceIncomplete, PrefaceRejected):
            if protocol == "http":
                try:
                    client.sendall(b"HTTP/1.1 421 Misdirected Request\r\n"
                                   b"Connection: close\r\nContent-Length: 0\r\n\r\n")
                except OSError:
                    pass
        finally:
            try:
                client.close()
            finally:
                self._slots.release()

    def _read_request_head(self, client, protocol):
        payload = bytearray()
        while len(payload) <= MAX_PREFACE_BYTES:
            chunk = client.recv(min(8192, MAX_PREFACE_BYTES + 1 - len(payload)))
            if not chunk:
                raise PrefaceRejected("connection closed before an allowed preface")
            payload.extend(chunk)
            try:
                return prepare_single_http_request(
                    bytes(payload), self.allowed_host,
                    expected_port=(80 if protocol == "http" else 443),
                    expected_scheme=protocol)
            except PrefaceIncomplete:
                continue
        raise PrefaceRejected("connection preface exceeds the gateway limit")

    def _handle(self, client, protocol):
        client.settimeout(self._preface_timeout)
        downstream = client
        if protocol == "https":
            # Termination is required: SNI-only forwarding would let an
            # encrypted request carry a different Host to a co-tenant.
            downstream = self._server_context.wrap_socket(client, server_side=True)
            downstream.settimeout(self._preface_timeout)
        request = self._read_request_head(downstream, protocol)
        upstream_port = 80 if protocol == "http" else 443
        raw_upstream = last_error = None
        for upstream_ip in self.upstream_ips:
            try:
                raw_upstream = self._connect((upstream_ip, upstream_port),
                                             self._handshake_timeout)
                break
            except OSError as exc:
                last_error = exc
        if raw_upstream is None:
            raise last_error or OSError("no pinned upstream for the allowed origin")
        upstream = raw_upstream
        if protocol == "https":
            try:
                upstream = self._upstream_context.wrap_socket(
                    raw_upstream, server_hostname=self.allowed_host)
            except Exception:
                raw_upstream.close()
                raise
        try:
            upstream.sendall(request.header)
            if request.initial_body:
                upstream.sendall(request.initial_body)
            remaining = request.content_length - len(request.initial_body)
            while remaining:
                chunk = downstream.recv(min(65536, remaining))
                if not chunk:
                    raise PrefaceRejected(
                        "HTTP request body ended before Content-Length")
                upstream.sendall(chunk)
                remaining -= len(chunk)
            # Never read from the downstream again: a later keep-alive or
            # pipelined request cannot cross this one-request proxy.
            downstream.settimeout(None)
            upstream.settimeout(120.0)
            self._relay_one_response(downstream, upstream)
        finally:
            upstream.close()
            if downstream is not client:
                downstream.close()

    def _relay_one_response(self, client, upstream):
        while not self._stop.is_set():
            data = upstream.recv(65536)
            if not data:
                return
            client.sendall(data)


# --- policy receipt -------------------------------------------------------


def egress_policy_manifest(allowed_host=ALLOWED_HOST):
    """Stable, port-independent provenance for the enforced network policy."""

    return {
        "schema_version": 1,
        "policy_version": POLICY_VERSION,
        "enforcement_boundary": "qemu-user-netdev-restrict-plus-host-gateway",
        "guest_network": "slirp-restricted",
        "target_public_egress": {
            "host": canonical_dns_name(allowed_host),
            "protocols": [
                "http-host-exact",
                "https-terminated-sni-exact-and-inner-host-exact",
                "one-framed-request-per-connection-canonical-host-close",
            ],
            "ports": [80, 443],
        },
        "null_project_public_egress": [],
        "blocked": [
            "guest-dns", "direct-ip-bypass", "non-http-tcp", "udp", "ipv6",
        ],
        "guest_relay_address": GATEWAY_GUEST_IP,
    }


def egress_policy_sha256(allowed_host=ALLOWED_HOST):
    payload = json.dumps(egress_policy_manifest(allowed_host), sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class FenceInstallation:
    """What was installed, and the digest a report can quote as a receipt."""

    mode: str
    policy_sha256: str
    gateway_guest_ip: str
    gateway_ports: object
    gateway_ca_sha256: object
    gateway_leaf_spki_sha256: object
    pinned_upstream_ips: tuple
    netdev_args: tuple

    def as_dict(self):
        return {"mode": self.mode, "policy_sha256": self.policy_sha256,
                "gateway_guest_ip": self.gateway_guest_ip,
                "gateway_ports": (None if self.gateway_ports is None
                                  else [self.gateway_ports.http,
                                        self.gateway_ports.https]),
                "gateway_ca_sha256": self.gateway_ca_sha256,
                "gateway_leaf_spki_sha256": self.gateway_leaf_spki_sha256,
                "pinned_upstream_ips": list(self.pinned_upstream_ips)}


class TargetFence:
    """Own the trusted gateway and hand QEMU the network arguments it implies.

    There is no firewall script to re-install here: the policy lives in the
    ``-netdev`` line, so a branch either boots with the boundary or has no
    network at all. ``install`` is therefore idempotent and cheap, and
    ``verify_netdev`` re-reads the produced arguments instead of trusting them.
    """

    def __init__(self, allowed_host=ALLOWED_HOST, gateway_factory=None,
                 relay_script=None, python=sys.executable):
        self.allowed_host = canonical_dns_name(allowed_host)
        self._factory = gateway_factory or FilteringTcpGateway
        self.relay_script = str(relay_script
                                or Path(__file__).with_name("guest_relay.py"))
        self.python = python
        self._gateway = None
        self._lock = threading.Lock()

    def close(self):
        with self._lock:
            if self._gateway is not None:
                self._gateway.close()
            self._gateway = None

    def install(self, mode, upstream_ips=None):
        """Build the boundary for ``mode``; fail closed on an unknown mode."""

        if mode not in ("target", "null"):
            raise EgressError("egress mode must be target or null")
        with self._lock:
            if mode == "null":
                return FenceInstallation(
                    mode="null", policy_sha256=egress_policy_sha256(self.allowed_host),
                    gateway_guest_ip=GATEWAY_GUEST_IP, gateway_ports=None,
                    gateway_ca_sha256=None, gateway_leaf_spki_sha256=None,
                    pinned_upstream_ips=(), netdev_args=self._null_netdev_args())
            if self._gateway is None:
                self._gateway = self._factory("127.0.0.1", self.allowed_host,
                                              upstream_ips=upstream_ips)
            ports = self._gateway.start()
            installation = FenceInstallation(
                mode="target", policy_sha256=egress_policy_sha256(self.allowed_host),
                gateway_guest_ip=GATEWAY_GUEST_IP, gateway_ports=ports,
                gateway_ca_sha256=hashlib.sha256(
                    self._gateway.identity.ca_pem).hexdigest(),
                gateway_leaf_spki_sha256=self._gateway.identity.leaf_spki_sha256,
                pinned_upstream_ips=tuple(self._gateway.upstream_ips),
                netdev_args=self._target_netdev_args(ports))
        self.verify_netdev(installation)
        return installation

    @staticmethod
    def _null_netdev_args():
        """No NIC at all: the strongest form of "no public egress"."""

        return ("-netdev", "user,id=n0,restrict=on", "-device", "virtio-net-pci,netdev=n0")

    def _target_netdev_args(self, ports):
        """Two relays, each spawning a fresh stdio bridge per guest connection."""

        def relay(guest_port, host_port):
            return "guestfwd=tcp:{ip}:{guest}-cmd:{python} {script} {host}".format(
                ip=GATEWAY_GUEST_IP, guest=guest_port, python=self.python,
                script=self.relay_script, host=host_port)

        forward = ("user,id=n0,restrict=on,"
                   + relay(80, ports.http) + "," + relay(443, ports.https))
        return ("-netdev", forward, "-device", "virtio-net-pci,netdev=n0")

    @staticmethod
    def verify_netdev(installation):
        """Re-read the produced arguments; raise unless the boundary is intact."""

        args = list(installation.netdev_args)
        if "-netdev" not in args:
            raise EgressError("netdev arguments are missing")
        netdev = args[args.index("-netdev") + 1]
        if "restrict=on" not in netdev:
            raise EgressError("guest network is not restricted")
        if installation.mode == "target":
            if netdev.count("guestfwd=") != 2:
                raise EgressError("target mode needs exactly two guest relays")
            if netdev.count("-cmd:") != 2:
                raise EgressError(
                    "each relay must use the per-connection -cmd: form")
            if "guest_relay.py" not in netdev:
                raise EgressError("the relay bridge is not named in the boundary")
            for port in (installation.gateway_ports.http,
                         installation.gateway_ports.https):
                if not re.search(r"-cmd:\S+ \S+ {}(?:,|$)".format(port),
                                 netdev):
                    raise EgressError("a relay target port is missing")
        else:
            if "guestfwd" in netdev:
                raise EgressError("null mode must not relay anything")
        return True

    def guest_setup_command(self, installation):
        """Trust this run's ephemeral CA and pin the one allowed hostname."""

        if installation.mode != "target":
            return "true"
        ca_b64 = base64.b64encode(self._gateway.identity.ca_pem).decode("ascii")
        return (
            "set -eu\n"
            "printf %s {ca} | base64 -d > /usr/local/share/ca-certificates/"
            "target-egress.crt\n"
            "chmod 0644 /usr/local/share/ca-certificates/target-egress.crt\n"
            "update-ca-certificates >/dev/null\n"
            "sed -i '/[[:space:]]# RSI_TARGET_EGRESS$/d' /etc/hosts\n"
            "printf '%s\\t%s\\t# RSI_TARGET_EGRESS\\n' {ip} {host} >> /etc/hosts\n"
            "test \"$(getent ahostsv4 {host} | awk 'NR==1 {{print $1}}')\" = {ip}\n"
            "echo RSI_GUEST_RELAY_IDENTITY_OK=1\n"
        ).format(ca=ca_b64, ip=installation.gateway_guest_ip, host=self.allowed_host)

    PROBE_SCRIPT = '''
import socket

def blocked(name, family, address, port, udp=False):
    sock = socket.socket(family, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM)
    sock.settimeout(2.0)
    try:
        if udp:
            query = bytes.fromhex(
                "e15001000001000000000000076578616d706c6503636f6d0000010001")
            sock.sendto(query, (address, port))
            sock.recvfrom(512)
        else:
            sock.connect((address, port))
    except OSError:
        print("RSI_PROBE_%s=BLOCKED" % name)
    else:
        print("RSI_PROBE_%s=OPEN" % name)
    finally:
        sock.close()

blocked("DNS", socket.AF_INET, "10.0.2.3", 53, udp=True)
blocked("NON_HTTP", socket.AF_INET, "1.1.1.1", 53)
blocked("IPV6", socket.AF_INET6, "2606:4700:4700::1111", 443)
'''

    def probe_command(self, installation):
        """The guest-side attestation transcript, as shell text."""

        expected_exact = "PASS" if installation.mode == "target" else "BLOCKED"
        pinned_ip = (installation.pinned_upstream_ips[0]
                     if installation.pinned_upstream_ips else "1.1.1.1")
        encoded = base64.b64encode(self.PROBE_SCRIPT.encode("utf-8")).decode("ascii")
        return (
            "set +e\n"
            "if curl --noproxy '*' -fsS --connect-timeout 8 --max-time 25 "
            "'https://{host}/' >/dev/null 2>&1; then echo RSI_PROBE_EXACT=PASS; "
            "else echo RSI_PROBE_EXACT=BLOCKED; fi\n"
            "if curl --noproxy '*' -kfsS --connect-timeout 5 --max-time 10 "
            "--resolve 'gitlab.web.hku.icu:443:{ip}' 'https://gitlab.web.hku.icu/' "
            ">/dev/null 2>&1; then echo RSI_PROBE_WRONG_SNI=OPEN; "
            "else echo RSI_PROBE_WRONG_SNI=BLOCKED; fi\n"
            "if curl --noproxy '*' -kfsS --connect-timeout 5 --max-time 10 "
            "'https://{ip}/' >/dev/null 2>&1; then echo RSI_PROBE_DIRECT_IP=OPEN; "
            "else echo RSI_PROBE_DIRECT_IP=BLOCKED; fi\n"
            "printf %s {encoded} | base64 -d | python3 -\n"
            "echo RSI_PROBE_DONE=1\n"
        ).format(host=self.allowed_host, ip=pinned_ip, encoded=encoded,
                 expected=expected_exact)

    def probe(self, branch, installation):
        """Run the attestation from inside the guest and check every marker."""

        if installation.mode not in ("target", "null"):
            raise EgressError("cannot probe an unknown egress mode")
        code, output = branch.run(self.probe_command(installation), timeout=90)
        output = output or ""
        expected = {"EXACT": ("PASS" if installation.mode == "target" else "BLOCKED"),
                    "WRONG_SNI": "BLOCKED", "DIRECT_IP": "BLOCKED",
                    "DNS": "BLOCKED", "NON_HTTP": "BLOCKED", "IPV6": "BLOCKED"}
        observed = {}
        for line in output.splitlines():
            if not line.startswith("RSI_PROBE_") or "=" not in line:
                continue
            name, value = line[len("RSI_PROBE_"):].split("=", 1)
            if name in observed:
                raise EgressError("egress probe emitted a duplicate marker")
            observed[name] = value
        if observed.get("DONE") != "1":
            raise EgressError("egress probe did not finish: " + output[-1500:])
        for name, value in expected.items():
            if observed.get(name) != value:
                raise EgressError("egress probe {} was {}, expected {}: {}".format(
                    name, observed.get(name), value, output[-1500:]))
        return {"mode": installation.mode, "markers": observed,
                "transcript_sha256": hashlib.sha256(
                    output.encode("utf-8")).hexdigest()}
