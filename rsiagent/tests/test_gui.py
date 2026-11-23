"""GUI input and checkpoint tests: monitor command shapes (no QEMU boot)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import vm_host


class Recorder(vm_host.VMBranch):
    """A branch whose monitor only records commands."""

    def __init__(self, base):
        super().__init__(base)
        self.sent = []
        self._paused = False

    def _monitor(self, command, timeout=15):
        self.sent.append(command)
        return "ok"


class GuiTests(unittest.TestCase):
    def setUp(self):
        handle, self._base = tempfile.mkstemp(suffix=".qcow2")
        os.close(handle)

    def tearDown(self):
        os.unlink(self._base)

    def _branch(self):
        return Recorder(self._base)

    def test_absent_base_image_is_still_refused(self):
        with self.assertRaises(vm_host.VMError):
            vm_host.VMBranch("/nope.qcow2").start()

    def test_key_chord(self):
        branch = self._branch()
        branch.key("ctrl-alt-t")
        self.assertEqual(branch.sent, ["sendkey ctrl-alt-t"])
        with self.assertRaises(vm_host.VMError):
            branch.key("")

    def test_pointer_moves_use_absolute_scaling(self):
        branch = self._branch()
        branch.move_pointer(960, 540, 1920, 1080)
        self.assertEqual(len(branch.sent), 2)
        self.assertIn('"axis":"x","value":16392', branch.sent[0])
        self.assertIn('"axis":"y","value":16398', branch.sent[1])

    def test_pointer_is_clamped_to_the_viewport(self):
        branch = self._branch()
        branch.move_pointer(99999, -5, 100, 100)
        self.assertIn('"value":{}'.format(vm_host.VMBranch._ABS_SCALE), branch.sent[0])
        self.assertIn('"value":0', branch.sent[1])

    def test_click_sends_down_then_up(self):
        branch = self._branch()
        branch.click(10, 10, 100, 100)
        self.assertEqual(len(branch.sent), 4)          # x, y, down, up
        self.assertIn('"down":true', branch.sent[2])
        self.assertIn('"down":false', branch.sent[3])
        with self.assertRaises(vm_host.VMError):
            branch.click(0, 0, 10, 10, button="thumb")

    def test_type_text_maps_characters(self):
        branch = self._branch()
        count = branch.type_text("Hi 5-")
        self.assertEqual(count, 5)
        self.assertEqual(branch.sent, ["sendkey shift-h", "sendkey i", "sendkey spc",
                                       "sendkey 5", "sendkey minus"])

    def test_unmapped_character_is_refused(self):
        branch = self._branch()
        with self.assertRaises(vm_host.VMError):
            branch.type_text("汉")

    def test_pause_and_resume_are_idempotent(self):
        branch = self._branch()
        branch.pause()
        branch.pause()
        branch.resume()
        branch.resume()
        self.assertEqual(branch.sent, ["stop", "cont"])


class WrapTests(unittest.TestCase):
    """The serial console runs one bash group per command; keep it well formed."""

    def test_single_line_command_is_grouped(self):
        wrapped = vm_host.wrap_command("ls /root", "M")
        self.assertTrue(wrapped.startswith("{ ls /root\n} 2>&1;"))
        self.assertIn('echo "M"', wrapped)
        self.assertTrue(wrapped.endswith("\n"))

    def test_multi_line_command_has_no_stray_semicolon(self):
        wrapped = vm_host.wrap_command("set +e\nif true; then echo A; fi\n", "M")
        self.assertNotIn("\n;", wrapped)
        self.assertIn("fi\n} 2>&1;", wrapped)

    def test_exit_code_is_captured_even_when_the_group_fails(self):
        wrapped = vm_host.wrap_command("false", "M")
        self.assertIn("__rsi_rc=$?", wrapped)
        self.assertIn("__RSI_RC__", wrapped)


if __name__ == "__main__":
    unittest.main()
