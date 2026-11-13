"""Eyes/imagery/render tests: PPM parsing, PNG encoding, capture, comparison."""
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import eyes
import imagery
import render


def ppm(width, height, colour=(10, 20, 30)):
    header = "P6\n{} {}\n255\n".format(width, height).encode()
    return header + bytes(colour) * (width * height)


class EyesTests(unittest.TestCase):
    def test_parse_ppm_and_reject_other_formats(self):
        width, height, pixels = eyes.parse_ppm(ppm(2, 1))
        self.assertEqual((width, height, len(pixels)), (2, 1, 6))
        with self.assertRaises(eyes.ScreenshotError):
            eyes.parse_ppm(b"P3\n1 1\n255\n0 0 0\n")

    def test_ppm_with_comment(self):
        data = b"P6\n# made by qemu\n2 1\n255\n" + bytes(6)
        self.assertEqual(eyes.parse_ppm(data)[:2], (2, 1))

    def test_png_is_well_formed(self):
        width, height, pixels = eyes.parse_ppm(ppm(4, 3))
        png = eyes.ppm_to_png(width, height, pixels)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IHDR", png[:32])
        self.assertTrue(png.endswith(b"IEND\xae\x42\x60\x82"))
        length = struct.unpack(">I", png[8:12])[0]
        self.assertEqual(png[12:16], b"IHDR")
        self.assertEqual(length, 13)

    def test_capture_reads_the_dump(self):
        class Branch:
            def __init__(self, path):
                self.path = path

            def screendump(self, target):
                with open(target, "wb") as handle:
                    handle.write(ppm(2, 2))

        with tempfile.TemporaryDirectory() as temp:
            target = os.path.join(temp, "screen.ppm")
            png = eyes.capture(Branch(target), path=target, timeout=5)
            self.assertTrue(png.startswith(b"\x89PNG"))

    def test_capture_without_channel_is_refused(self):
        with self.assertRaises(eyes.ScreenshotError):
            eyes.capture(object())


class ImageryTests(unittest.TestCase):
    def test_vision_request_shape(self):
        captured = {}

        class Client:
            model = "vision-model"
            base_url = "http://127.0.0.1:1/v1"
            api_key = "k"
            calls = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "left panel"}}]}).encode()

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode())
            return FakeResponse()

        original = imagery.urllib.request.urlopen
        imagery.urllib.request.urlopen = fake_urlopen
        try:
            text = imagery.ask_about_image(Client(), b"png-bytes", "where is the panel?")
        finally:
            imagery.urllib.request.urlopen = original
        self.assertEqual(text, "left panel")
        content = captured["body"]["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("where is the panel?", content[0]["text"])
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))


class RenderTests(unittest.TestCase):
    def _write(self, directory, name, data):
        path = os.path.join(directory, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_identical_images_are_same(self):
        with tempfile.TemporaryDirectory() as temp:
            a = self._write(temp, "a.ppm", ppm(8, 8))
            b = self._write(temp, "b.ppm", ppm(8, 8))
            ok, result = render.same(a, b)
            self.assertTrue(ok)
            self.assertEqual(result["changed_ratio"], 0.0)

    def test_changed_image_is_not_same(self):
        with tempfile.TemporaryDirectory() as temp:
            a = self._write(temp, "a.ppm", ppm(8, 8, (0, 0, 0)))
            b = self._write(temp, "b.ppm", ppm(8, 8, (255, 255, 255)))
            ok, result = render.same(a, b)
            self.assertFalse(ok)
            self.assertEqual(result["changed_ratio"], 1.0)

    def test_size_mismatch_is_not_comparable(self):
        with tempfile.TemporaryDirectory() as temp:
            a = self._write(temp, "a.ppm", ppm(4, 4))
            b = self._write(temp, "b.ppm", ppm(8, 8))
            ok, result = render.same(a, b)
            self.assertFalse(ok)
            self.assertFalse(result["comparable"])

    def test_changed_regions_locate_the_difference(self):
        with tempfile.TemporaryDirectory() as temp:
            a = self._write(temp, "a.ppm", ppm(128, 128, (0, 0, 0)))
            data = bytearray(ppm(128, 128, (0, 0, 0)))
            # Repaint the top-left 64x64 tile white.
            header_len = len(data) - 128 * 128 * 3
            for y in range(64):
                for x in range(64):
                    offset = header_len + (y * 128 + x) * 3
                    data[offset:offset + 3] = b"\xff\xff\xff"
            b = self._write(temp, "b.ppm", bytes(data))
            regions = render.changed_regions(a, b, tile=64)
            self.assertEqual(len(regions), 1)
            self.assertEqual((regions[0]["x"], regions[0]["y"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
