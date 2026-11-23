"""Parallel-wave tests: real concurrency, deterministic commit, per-attempt state."""
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import explore
from rsi import Protocol
from vm_client import VMClient


class BarrierHost:
    """A host whose attempts must all be in flight at once to get past a barrier."""

    def __init__(self, party=2, timeout=10.0):
        self.barrier = threading.Barrier(party, timeout=timeout)
        self.threads = []
        self.verifications = []
        self.order = []

    def run_actor(self, task, memory):
        self.threads.append(threading.get_ident())
        self.barrier.wait()               # raises BrokenBarrierError if serialised
        time.sleep(0.05)
        return SimpleNamespace(public={"task": task})

    def verify(self, task, public):
        self.verifications.append((task, threading.get_ident()))
        return "PASS"

    def consolidate(self, memory, task, candidate, verdict):
        self.order.append(task)
        return memory + [task]

    def curriculum_review(self, task, verdict, memory):
        return None


class ParallelWaveTests(unittest.TestCase):
    def test_workers_actually_run_in_parallel(self):
        host = BarrierHost(party=3, timeout=10.0)
        protocol = Protocol(host, ["seed"])
        self.assertEqual(protocol.broad_wave(["a", "b", "c"], workers=3),
                         ["PASS", "PASS", "PASS"])
        self.assertEqual(len(set(host.threads)), 3, "attempts shared one thread")

    def test_commit_order_stays_the_task_order_regardless_of_finish_order(self):
        host = BarrierHost(party=3, timeout=10.0)
        protocol = Protocol(host, ["seed"])
        protocol.broad_wave(["slow", "quick", "middle"], workers=3)
        self.assertEqual(host.order, ["slow", "quick", "middle"])
        self.assertEqual(protocol.memory, ["seed", "slow", "quick", "middle"])

    def test_verification_happens_in_the_attempt_thread(self):
        host = BarrierHost(party=2, timeout=10.0)
        protocol = Protocol(host, [])
        protocol.broad_wave(["a", "b"], workers=2)
        self.assertEqual({thread for _, thread in host.verifications},
                         set(host.threads))


class AttemptStateTests(unittest.TestCase):
    """Each thread must see only its own branch (the parallel verifier bug)."""

    def _client(self):
        return VMClient(ScriptedClient(), "/base.qcow2",
                        branch_factory=FakeBranch)

    def test_each_thread_only_sees_its_own_branch(self):
        client = self._client()
        seen = {}
        ready = threading.Barrier(2, timeout=10)

        def work(label):
            history = [{"role": "user", "content": label}]
            client.last_branch = client._branch_for(history)
            client.last_check_report = {"label": label}
            ready.wait()                    # both threads are mid-attempt
            time.sleep(0.05)
            seen[label] = (client.last_branch, client.last_check_report)

        threads = [threading.Thread(target=work, args=(label,))
                   for label in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertIsNot(seen["a"][0], seen["b"][0])
        self.assertEqual(seen["a"][0].base_image, "/base.qcow2")
        self.assertEqual(seen["a"][1], {"label": "a"})
        self.assertEqual(seen["b"][1], {"label": "b"})

    def test_a_thread_without_an_attempt_sees_nothing(self):
        self.assertIsNone(self._client().last_branch)
        self.assertIsNone(self._client().last_check_report)


class WorkerCountTests(unittest.TestCase):
    def test_a_wave_worker_count_must_be_positive(self):
        for bad in (0, -1, "2", None, 1.5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                explore.ExploreRun(None, wave_workers=bad)

    def test_the_worker_count_reaches_the_wave(self):
        calls = {}

        class Protocol2:
            last_wave_answers = []

            def broad_wave(self, tasks, workers=1):
                calls["workers"] = workers
                calls["tasks"] = list(tasks)
                return ["PASS" for _ in tasks]

        run = explore.ExploreRun(None, wave_workers=3)
        run.protocol = Protocol2()
        run.curriculum_history = []
        verdicts = run.protocol.broad_wave(["a", "b"], workers=run.wave_workers)
        self.assertEqual(calls["workers"], 3)
        self.assertEqual(verdicts, ["PASS", "PASS"])


class FakeBranch:
    instances = []

    def __init__(self, base_image, **kwargs):
        self.base_image = base_image
        self.closed = False
        self.overlay_bytes = 1024
        FakeBranch.instances.append(self)

    def start(self):
        return self

    def run(self, command, timeout=None):
        return 0, "PASS"

    def close(self):
        self.closed = True


class ScriptedClient:
    def __init__(self):
        self.calls = {}
        self.prompts = []

    def complete(self, role, system, user, **kwargs):
        self.calls[role] = self.calls.get(role, 0) + 1
        self.prompts.append((role, user))
        return "{}"


if __name__ == "__main__":
    unittest.main()
