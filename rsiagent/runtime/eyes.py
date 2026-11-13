"""Screen capture from a branch VM, ported from upstream ``core/eyes.py``.

Upstream gives the Actor a way to *look*: the harness captures the screen and a
vision model answers a targeted question about it. This module captures through
the QEMU monitor (``screendump``), converts the PPM QEMU writes into a PNG with
the standard library only, and leaves the questioning to ``imagery.py``.

The capture path is deliberately separate from the shell channel: structural
facts come from commands, rendered facts come from looking.
"""
import struct
import time
import zlib


class ScreenshotError(RuntimeError):
    """The screen could not be captured."""


def parse_ppm(data):
    """Parse a binary PPM (P6) into (width, height, bytes) with comments allowed."""
    if not data.startswith(b"P6"):
        raise ScreenshotError("not a binary PPM (P6) image")
    index, fields = 2, []
    length = len(data)
    while len(fields) < 3:
        while index < length and data[index:index + 1].isspace():
            index += 1
        if index < length and data[index:index + 1] == b"#":
            while index < length and data[index:index + 1] != b"\n":
                index += 1
            continue
        start = index
        while index < length and not data[index:index + 1].isspace():
            index += 1
        if start == index:
            raise ScreenshotError("truncated PPM header")
        fields.append(int(data[start:index]))
    index += 1                      # single whitespace after the maxval
    width, height, maxval = fields
    if maxval != 255:
        raise ScreenshotError("only 8-bit PPM is supported (maxval {})".format(maxval))
    pixels = data[index:index + width * height * 3]
    if len(pixels) != width * height * 3:
        raise ScreenshotError("truncated PPM pixel data")
    return width, height, pixels


def _chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def ppm_to_png(width, height, pixels):
    """Encode RGB bytes as a PNG (filter type 0 per scanline)."""
    raw = bytearray()
    stride = width * 3
    for row in range(height):
        raw.append(0)
        raw.extend(pixels[row * stride:(row + 1) * stride])
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + _chunk(b"IEND", b""))


def capture(branch, *, path=None, timeout=20, settle=0.2):
    """Capture the branch's screen and return PNG bytes.

    ``branch`` must expose ``screendump(path)``, which asks QEMU's monitor for a
    PPM file on the host.
    """
    if not hasattr(branch, "screendump"):
        raise ScreenshotError("this branch has no screen channel")
    path = path or "/tmp/rsi-screen.ppm"
    branch.screendump(path)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            data = b""
        if data.startswith(b"P6"):
            width, height, pixels = parse_ppm(data)
            return ppm_to_png(width, height, pixels)
        time.sleep(settle)
    raise ScreenshotError("no screen dump appeared at " + path)


def describe_question(path, question):
    """The vision request shape: one image plus one targeted question."""
    return {"path": path, "question": question}
