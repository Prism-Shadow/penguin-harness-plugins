"""VMBranch tests. The boot test is opt-in (RSI_VM_TEST=1) because it starts QEMU."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from vm_host import VMBranch, VMError

BASE = os.environ.get("RSI_VM_BASE", "/home/yichen/vm-prep.qcow2")


class VMBranchUnitTests(unittest.TestCase):
    def test_missing_base_image_is_refused_before_qemu(self):
        with self.assertRaises(VMError):
            VMBranch("/nonexistent/base.qcow2").start()

    def test_overlay_path_is_unique_per_branch(self):
        a = VMBranch(BASE)
        b = VMBranch(BASE)
        self.assertNotEqual(a, b)
        # No process and no disk is created until start() is called.
        self.assertIsNone(a.dir)
        self.assertIsNone(a.overlay)
        self.assertEqual(a.overlay_bytes, 0)


@unittest.skipUnless(os.environ.get("RSI_VM_TEST") == "1",
                     "set RSI_VM_TEST=1 to boot a branch VM")
class VMBranchLiveTests(unittest.TestCase):
    def test_branch_runs_commands_and_discards_its_disk(self):
        with VMBranch(BASE, boot_timeout=240) as vm:
            code, out = vm.run("echo hello-from-vm")
            self.assertEqual(code, 0)
            self.assertIn("hello-from-vm", out)
            overlay = vm.overlay
            self.assertGreater(vm.overlay_bytes, 0)
            work_dir = vm.dir
        self.assertFalse(os.path.exists(work_dir), "overlay directory must be removed")
        self.assertFalse(os.path.exists(overlay))

    def test_second_branch_does_not_see_first_branch_writes(self):
        marker = "/root/rsi-test-marker"
        with VMBranch(BASE, boot_timeout=240) as first:
            first.run("rm -f " + marker + "; echo leaked > " + marker)
        with VMBranch(BASE, boot_timeout=240) as second:
            code, _ = second.run("test -f " + marker)
            self.assertNotEqual(code, 0, "a fresh overlay must not see the earlier branch")


if __name__ == "__main__":
    unittest.main()
