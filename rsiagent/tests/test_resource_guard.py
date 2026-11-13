"""Resource guard tests: ceilings, terminate, infrastructure classification."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import resource_guard as rg


class GuardTests(unittest.TestCase):
    def test_inside_budget_passes(self):
        guard = rg.ResourceGuard(memory_limit_bytes=1024, wall_limit_seconds=10,
                                 cpu_limit_seconds=10)
        self.assertIsNone(guard.check(rg.Sample(1, 512, 1.0, 1.0)))

    def test_memory_over_limit(self):
        guard = rg.ResourceGuard(memory_limit_bytes=1024)
        reason = guard.check(rg.Sample(1, 4096, 0.0, 0.0))
        self.assertIn("memory", reason)

    def test_wall_and_cpu_limits(self):
        guard = rg.ResourceGuard(wall_limit_seconds=5, cpu_limit_seconds=3)
        self.assertIn("wall clock", guard.check(rg.Sample(1, 0, 0.0, 99.0)))
        self.assertIn("cpu", guard.check(rg.Sample(1, 0, 99.0, 1.0)))

    def test_enforce_terminates_and_records(self):
        killed = []
        guard = rg.ResourceGuard(memory_limit_bytes=10)
        with self.assertRaises(rg.ResourceStop):
            guard.enforce(rg.Sample(42, 999, 0.0, 0.0), killed.append)
        self.assertEqual(killed, [42])
        self.assertEqual(guard.events[0]["kind"], "resource_stop")
        self.assertTrue(guard.events[0]["infrastructure"])

    def test_enforce_survives_a_failing_terminator(self):
        def boom(pid):
            raise OSError("no such process")
        guard = rg.ResourceGuard(memory_limit_bytes=10)
        with self.assertRaises(rg.ResourceStop) as caught:
            guard.enforce(rg.Sample(42, 999, 0.0, 0.0), boom)
        self.assertIn("terminate raised", str(caught.exception))

    def test_guard_stop_is_infrastructure(self):
        self.assertTrue(rg.is_infrastructure_stop(rg.ResourceStop("x")))
        self.assertFalse(rg.is_infrastructure_stop(ValueError("x")))

    def test_sample_of_a_real_process(self):
        import os
        sample = rg.sample_process(os.getpid())
        self.assertEqual(sample.pid, os.getpid())
        self.assertGreater(sample.rss_bytes, 0)


if __name__ == "__main__":
    unittest.main()
