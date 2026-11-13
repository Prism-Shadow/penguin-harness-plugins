"""Target-cycle tests: pass, retry, stall, unverified, release, infra failures."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import evolution_loop as el


def verification(verdict, report="report"):
    return el.TargetVerification(verdict, report)


def evolution(status="READY_FOR_RETRY", projects=1):
    return el.EvolutionResult(status, memory={"evolved": True}, projects=projects)


class Harness:
    """Scripted hook results in two queues, plus the log the loop produced."""

    def __init__(self, verifications, evolutions=(),
                 learn_on_pass=False, curriculum_after_pass=False):
        self.verifications = list(verifications)
        self.evolutions = list(evolutions)
        self.created = []
        self.released = []
        self.learned = []
        self.evolved = []
        self.events = []
        self.hooks = el.LoopHooks(
            fresh_target_environment=self.fresh_environment,
            fresh_target_verifier=self.fresh_verifier,
            fresh_target_actor=self.fresh_actor,
            actor_work=self.actor_work,
            verifier_verify=self.verifier_verify,
            actor_learn=self.actor_learn,
            evolve=self.evolve,
            learn_on_pass=learn_on_pass,
            curriculum_after_pass=curriculum_after_pass,
            release_target_environment=self.release,
            on_event=lambda event, payload: self.events.append(event))

    def fresh_environment(self, direction, cycle):
        environment = type("Env", (), {"cycle": cycle})()
        self.created.append(environment)
        return environment

    @staticmethod
    def fresh_verifier(environment, direction, cycle):
        return object()

    @staticmethod
    def fresh_actor(memory, cycle):
        return object()

    @staticmethod
    def actor_work(actor, verifier, environment, direction):
        return {"cycle": environment.cycle, "output": "done"}

    def verifier_verify(self, verifier, environment, direction, output):
        if not self.verifications:
            raise AssertionError("unexpected extra verification")
        return self.verifications.pop(0)

    def actor_learn(self, actor, environment, direction, output, verdict, report,
                    memory):
        self.learned.append(verdict)
        return el.ActorLearning(memory={"round": len(self.learned)}, diagnosis="x")

    def evolve(self, direction, verdict, report, diagnosis, memory, number):
        if not self.evolutions:
            raise AssertionError("unexpected extra evolution")
        self.evolved.append((verdict, number))
        return self.evolutions.pop(0)

    def release(self, environment):
        self.released.append(environment)


class PassTests(unittest.TestCase):
    def test_pass_terminates_and_keeps_the_live_state(self):
        harness = Harness([verification("PASS")])
        result = el.run_loop("learn x", {"m": 0}, harness.hooks)
        self.assertEqual(result.termination, "verifier_pass")
        self.assertEqual(result.target_cycles, 1)
        self.assertEqual(result.evolutions, 0)
        self.assertEqual(harness.released, [])          # ownership transferred
        self.assertEqual(harness.learned, [])           # no learning on a plain pass
        self.assertIs(result.environment, harness.created[0])
        self.assertEqual(harness.events[-1], "TARGET_LOOP_TERMINATED")

    def test_verification_report_must_be_nonempty(self):
        with self.assertRaises(el.EvolutionLoopError):
            el.TargetVerification("PASS", "  ")
        with self.assertRaises(el.EvolutionLoopError):
            el.TargetVerification("MAYBE", "x")

    def test_evolution_result_validates_its_fields(self):
        with self.assertRaises(el.EvolutionLoopError):
            el.EvolutionResult("LATER", memory={}, projects=1)
        with self.assertRaises(el.EvolutionLoopError):
            el.EvolutionResult("READY_FOR_RETRY", memory={}, projects=-1)
        with self.assertRaises(el.EvolutionLoopError):
            el.ActorLearning(memory={}, diagnosis="")


class RetryTests(unittest.TestCase):
    def test_fail_then_pass_takes_two_cycles_and_releases_the_failed_state(self):
        harness = Harness(
            [verification("FAIL", "missing file"), verification("PASS")],
            [evolution("READY_FOR_RETRY", projects=2)])
        result = el.run_loop("learn x", {"m": 0}, harness.hooks)
        self.assertEqual(result.termination, "verifier_pass")
        self.assertEqual(result.target_cycles, 2)
        self.assertEqual(result.evolutions, 1)
        self.assertEqual(result.practice_projects, 2)
        self.assertEqual(len(harness.released), 1)      # the failed cycle
        self.assertEqual(harness.learned, ["FAIL"])
        self.assertIn("EVOLUTION_STARTED", harness.events)
        self.assertIn("READY_FOR_RETRY", harness.events)
        self.assertIs(result.environment, harness.created[1])

    def test_stall_runs_one_final_fresh_target_test(self):
        harness = Harness(
            [verification("FAIL"), verification("FAIL")],
            [evolution("STALLED", projects=1)])
        result = el.run_loop("learn x", {}, harness.hooks)
        self.assertEqual(result.termination, "curriculum_stalled_final_target_test")
        self.assertEqual(result.target_cycles, 2)
        self.assertIn("CURRICULUM_STALLED_FINAL_TEST_REQUIRED", harness.events)
        # The final test is a real attempt with its own environment.
        self.assertEqual(len(harness.created), 2)

    def test_unverified_terminates_as_a_harness_outcome(self):
        harness = Harness([verification("UNVERIFIED", "no evidence")])
        result = el.run_loop("learn x", {}, harness.hooks)
        self.assertEqual(result.termination, "target_harness_unverified")
        self.assertEqual(result.verifier_verdict, "UNVERIFIED")
        self.assertEqual(harness.evolved, [])


class PassReviewTests(unittest.TestCase):
    def test_zero_project_review_keeps_the_pass_state_live(self):
        harness = Harness([verification("PASS")],
                          [evolution("READY_FOR_RETRY", projects=0)],
                          learn_on_pass=True, curriculum_after_pass=True)
        result = el.run_loop("learn x", {}, harness.hooks)
        self.assertEqual(result.termination, "verifier_pass_curriculum_ready")
        self.assertEqual(harness.released, [])
        self.assertEqual(harness.learned, ["PASS"])

    def test_review_that_orders_practice_requires_a_fresh_target(self):
        harness = Harness(
            [verification("PASS"), verification("PASS")],
            [evolution("READY_FOR_RETRY", projects=3),
             evolution("READY_FOR_RETRY", projects=0)],
            learn_on_pass=True, curriculum_after_pass=True)
        result = el.run_loop("learn x", {}, harness.hooks)
        self.assertEqual(result.target_cycles, 2)
        self.assertEqual(result.practice_projects, 3)
        self.assertEqual(len(harness.released), 1)      # the first PASS state
        self.assertEqual(result.termination, "verifier_pass_curriculum_ready")
        self.assertIn("PASS_NEXT_EXPERIENCE_REVIEW_COMPLETED", harness.events)

    def test_review_that_stalls_after_a_pass_ends_the_loop(self):
        harness = Harness([verification("PASS")],
                          [evolution("STALLED", projects=0)],
                          learn_on_pass=True, curriculum_after_pass=True)
        result = el.run_loop("learn x", {}, harness.hooks)
        self.assertEqual(result.termination, "verifier_pass_curriculum_stalled")

    def test_review_without_pass_learning_is_refused_up_front(self):
        harness = Harness([], learn_on_pass=False, curriculum_after_pass=True)
        with self.assertRaises(el.EvolutionLoopError) as caught:
            el.run_loop("learn x", {}, harness.hooks)
        self.assertIn("requires same-Actor PASS learning", str(caught.exception))


class FailureTests(unittest.TestCase):
    def test_infrastructure_failure_propagates_and_releases_once(self):
        class Boom(RuntimeError):
            pass

        harness = Harness([])

        def explode(*_args):
            raise Boom("VM died")

        harness.hooks.actor_work = explode
        with self.assertRaises(Boom):
            el.run_loop("learn x", {}, harness.hooks)
        self.assertEqual(len(harness.released), 1)
        self.assertEqual(harness.events, ["TARGET_STARTED"])

    def test_a_release_failure_does_not_mask_the_primary_error(self):
        class Boom(RuntimeError):
            pass

        harness = Harness([])

        def explode(*_args):
            raise Boom("VM died")

        def bad_release(_environment):
            raise OSError("qemu gone")

        harness.hooks.actor_work = explode
        harness.hooks.release_target_environment = bad_release
        with self.assertRaises(Boom):
            el.run_loop("learn x", {}, harness.hooks)
        self.assertEqual(harness.events[-1], "TARGET_RELEASE_FAILED")

    def test_a_missing_environment_is_a_lifecycle_error(self):
        harness = Harness([])
        harness.hooks.fresh_target_environment = lambda *_: None
        with self.assertRaises(el.EvolutionLoopError):
            el.run_loop("learn x", {}, harness.hooks)

    def test_hooks_must_return_the_declared_types(self):
        harness = Harness([])
        harness.hooks.verifier_verify = lambda *_: "PASS"
        with self.assertRaises(el.EvolutionLoopError):
            el.run_loop("learn x", {}, harness.hooks)

    def test_empty_direction_is_refused(self):
        with self.assertRaises(ValueError):
            el.run_loop("  ", {}, Harness([]).hooks)


class StartTests(unittest.TestCase):
    def test_start_boundary_validates_its_counters(self):
        el.LoopStart(2, 1, 3)
        for bad in ((0, 0, 1), (1, 2, 1), (1, 1, 0), (1, 1, -1)):
            with self.assertRaises(el.EvolutionLoopError, msg=str(bad)):
                el.LoopStart(*bad)
        with self.assertRaises(el.EvolutionLoopError):
            el.LoopStart(1, 1, 1, final_after_stall="yes")

    def test_resuming_from_a_boundary_continues_the_counters(self):
        harness = Harness([verification("PASS")])
        result = el.run_loop("learn x", {}, harness.hooks,
                             start=el.LoopStart(4, 3, 7))
        self.assertEqual(result.target_cycles, 5)
        self.assertEqual(result.evolutions, 3)
        self.assertEqual(result.practice_projects, 7)


if __name__ == "__main__":
    unittest.main()
