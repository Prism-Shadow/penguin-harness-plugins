"""Target cycle state machine, ported from ``core/self_evolving_loop.py``.

The loop answers one question per cycle: given a live target environment, an
independent Verifier and the current memory, does the attempt pass, and if it
does not, what does the *same* Actor learn before the next attempt?

Invariants kept from upstream, because each one prevents a specific falsehood:

- Curriculum ``STALLED`` is a semantic stop, never a judgement about target
  correctness. The latest memory still gets one final fresh target test, and
  that live state is what leaves the loop.
- Infrastructure failures raise out of their hook. They are never mapped to a
  verdict, a stop, or a success.
- A grounded outcome (PASS/FAIL with a report) always goes through same-Actor
  learning before the Curriculum is consulted; a loop that reaches the
  Curriculum without it is a lifecycle bug, not a skip.
- ``UNVERIFIED`` terminates the cycle as a harness outcome; it does not silently
  become PASS and does not pretend to be FAIL.
- The target environment is released exactly once, on every path, and a release
  failure never masks the primary exception.
"""
import sys
from dataclasses import dataclass, field

EVENTS = (
    "TARGET_ORIENTATION_STARTED", "TARGET_ORIENTATION_COMPLETED",
    "TARGET_STARTED", "TARGET_VERIFIED", "TARGET_LEARNING_STARTED",
    "TARGET_LEARNING_COMPLETED", "TARGET_LOOP_TERMINATED", "EVOLUTION_STARTED",
    "PASS_NEXT_EXPERIENCE_REVIEW_STARTED", "PASS_NEXT_EXPERIENCE_REVIEW_COMPLETED",
    "READY_FOR_RETRY", "CURRICULUM_STALLED_FINAL_TEST_REQUIRED",
    "TARGET_RELEASE_FAILED",
)

VERDICTS = ("PASS", "FAIL", "UNVERIFIED")
STATUSES = ("READY_FOR_RETRY", "STALLED")


class EvolutionLoopError(RuntimeError):
    """The role lifecycle produced an invalid transport-level state."""


def _enum(value, allowed, label):
    text = str(value).strip().upper()
    if text not in allowed:
        raise EvolutionLoopError("unknown {}: {!r}".format(label, value))
    return text


@dataclass(frozen=True)
class TargetVerification:
    verdict: str
    report: str

    def __post_init__(self):
        object.__setattr__(self, "verdict", _enum(self.verdict, VERDICTS, "verdict"))
        if not isinstance(self.report, str) or not self.report.strip():
            raise EvolutionLoopError(
                "the Verifier report/reason must be nonempty")


@dataclass(frozen=True)
class ActorLearning:
    memory: object
    diagnosis: str

    def __post_init__(self):
        if not isinstance(self.diagnosis, str) or not self.diagnosis.strip():
            raise EvolutionLoopError("the Actor learning diagnosis must be nonempty")


@dataclass(frozen=True)
class EvolutionResult:
    status: str
    memory: object
    projects: int
    reason: str = ""

    def __post_init__(self):
        object.__setattr__(self, "status", _enum(self.status, STATUSES, "status"))
        if type(self.projects) is not int or self.projects < 0:
            raise EvolutionLoopError(
                "the project count must be a nonnegative integer")


@dataclass(frozen=True)
class LoopStart:
    """An admitted boundary: counters after completed practice, before a target."""

    target_cycles: int
    evolutions: int
    practice_projects: int
    final_after_stall: bool = False

    def __post_init__(self):
        if (any(type(value) is not int or value < 1 for value in (
                self.target_cycles, self.evolutions, self.practice_projects))
                or self.evolutions > self.target_cycles
                or type(self.final_after_stall) is not bool):
            raise EvolutionLoopError("invalid completed-practice boundary")


@dataclass(frozen=True)
class LoopResult:
    """Terminal live target state; ownership transfers to the caller."""

    output: object
    environment: object
    verifier_verdict: str
    verifier_report: str
    memory: object
    target_cycles: int
    evolutions: int
    practice_projects: int
    termination: str


@dataclass
class LoopHooks:
    """Everything the loop may call. Nothing else is reachable from here.

    ``fresh_target_*`` build a throwaway environment and its roles; the Actor
    never carries state between cycles except through ``memory``.
    """

    fresh_target_environment: object
    fresh_target_verifier: object
    fresh_target_actor: object
    actor_work: object
    verifier_verify: object
    actor_learn: object
    evolve: object
    verifier_orient: object = None
    learn_on_pass: bool = False
    curriculum_after_pass: bool = False
    release_target_environment: object = staticmethod(lambda _environment: None)
    on_event: object = staticmethod(lambda _event, _payload: None)
    events: list = field(default_factory=list)

    def emit(self, event, payload):
        self.events.append((event, dict(payload)))
        self.on_event(event, payload)


def run_loop(target_direction, current_memory, hooks, start=None):
    """Run target tests and practice without consulting an external evaluator."""

    if not isinstance(target_direction, str) or not target_direction.strip():
        raise ValueError("target_direction must be nonempty")
    if hooks.curriculum_after_pass and not hooks.learn_on_pass:
        raise EvolutionLoopError(
            "a Curriculum review after PASS requires same-Actor PASS learning")

    memory = current_memory
    target_cycles = start.target_cycles if start else 0
    evolutions = start.evolutions if start else 0
    practice_projects = start.practice_projects if start else 0
    final_after_stall = start.final_after_stall if start else False

    while True:
        target_cycles += 1
        environment = hooks.fresh_target_environment(target_direction, target_cycles)
        if environment is None:
            raise EvolutionLoopError("fresh target environment was not created")
        transferred = False
        released = False
        try:
            verifier = hooks.fresh_target_verifier(
                environment, target_direction, target_cycles)
            if verifier is None:
                raise EvolutionLoopError("fresh target Verifier was not created")
            if hooks.verifier_orient is not None:
                hooks.emit("TARGET_ORIENTATION_STARTED",
                           {"target_cycle": target_cycles,
                            "evolutions": evolutions})
                # No Actor context or action exists at this boundary, and the
                # same Verifier context must continue into candidate verification.
                hooks.verifier_orient(verifier, environment, target_direction,
                                      target_cycles)
                hooks.emit("TARGET_ORIENTATION_COMPLETED",
                           {"target_cycle": target_cycles,
                            "evolutions": evolutions})

            actor = hooks.fresh_target_actor(memory, target_cycles)
            if actor is None:
                raise EvolutionLoopError("fresh target Actor was not created")
            hooks.emit("TARGET_STARTED",
                       {"target_cycle": target_cycles, "evolutions": evolutions,
                        "final_after_curriculum_stall": final_after_stall})
            output = hooks.actor_work(actor, verifier, environment, target_direction)
            verification = hooks.verifier_verify(
                verifier, environment, target_direction, output)
            if not isinstance(verification, TargetVerification):
                raise EvolutionLoopError(
                    "verifier_verify must return TargetVerification")
            hooks.emit("TARGET_VERIFIED",
                       {"target_cycle": target_cycles,
                        "verdict": verification.verdict})

            learning = None
            should_learn = (verification.verdict == "FAIL"
                            or (verification.verdict == "PASS" and hooks.learn_on_pass))
            if should_learn:
                hooks.emit("TARGET_LEARNING_STARTED",
                           {"target_cycle": target_cycles,
                            "verdict": verification.verdict,
                            "evolutions": evolutions})
                learning = hooks.actor_learn(
                    actor, environment, target_direction, output,
                    verification.verdict, verification.report, memory)
                if not isinstance(learning, ActorLearning):
                    raise EvolutionLoopError("actor_learn must return ActorLearning")
                memory = learning.memory
                hooks.emit("TARGET_LEARNING_COMPLETED",
                           {"target_cycle": target_cycles,
                            "verdict": verification.verdict,
                            "evolutions": evolutions})

            review_pass = (verification.verdict == "PASS"
                           and hooks.curriculum_after_pass
                           and not final_after_stall)
            if verification.verdict == "PASS" and not review_pass:
                termination = "verifier_pass"
            elif final_after_stall:
                termination = "curriculum_stalled_final_target_test"
            elif verification.verdict == "UNVERIFIED":
                termination = "target_harness_unverified"
            else:
                termination = ""

            if termination:
                hooks.emit("TARGET_LOOP_TERMINATED",
                           {"target_cycle": target_cycles,
                            "verdict": verification.verdict,
                            "termination": termination,
                            "evolutions": evolutions})
                transferred = True
                return LoopResult(
                    output=output, environment=environment,
                    verifier_verdict=verification.verdict,
                    verifier_report=verification.report, memory=memory,
                    target_cycles=target_cycles, evolutions=evolutions,
                    practice_projects=practice_projects, termination=termination)

            # Every grounded outcome has already been learned by the same Actor.
            if learning is None:
                raise EvolutionLoopError(
                    "a grounded outcome reached the Curriculum without Actor "
                    "learning")

            next_evolution = evolutions + 1
            if verification.verdict == "FAIL":
                hooks.emit("EVOLUTION_STARTED",
                           {"target_cycle": target_cycles,
                            "evolution": next_evolution})
                # The failed live state is no longer needed. A PASS state stays
                # live while the Curriculum decides.
                released = True
                hooks.release_target_environment(environment)
            else:
                hooks.emit("PASS_NEXT_EXPERIENCE_REVIEW_STARTED",
                           {"target_cycle": target_cycles,
                            "prospective_evolution": next_evolution})

            evolved = hooks.evolve(
                target_direction, verification.verdict, verification.report,
                learning.diagnosis, memory, next_evolution)
            if not isinstance(evolved, EvolutionResult):
                raise EvolutionLoopError("evolve must return EvolutionResult")
            memory = evolved.memory
            practice_projects += evolved.projects

            if verification.verdict == "PASS" and evolved.projects == 0:
                termination = ("verifier_pass_curriculum_ready"
                               if evolved.status == "READY_FOR_RETRY"
                               else "verifier_pass_curriculum_stalled")
                hooks.emit("PASS_NEXT_EXPERIENCE_REVIEW_COMPLETED",
                           {"target_cycle": target_cycles, "projects": 0,
                            "reason": evolved.reason, "termination": termination})
                hooks.emit("TARGET_LOOP_TERMINATED",
                           {"target_cycle": target_cycles,
                            "verdict": verification.verdict,
                            "termination": termination,
                            "evolutions": evolutions})
                transferred = True
                return LoopResult(
                    output=output, environment=environment,
                    verifier_verdict=verification.verdict,
                    verifier_report=verification.report, memory=memory,
                    target_cycles=target_cycles, evolutions=evolutions,
                    practice_projects=practice_projects, termination=termination)

            if verification.verdict == "PASS":
                hooks.emit("PASS_NEXT_EXPERIENCE_REVIEW_COMPLETED",
                           {"target_cycle": target_cycles,
                            "projects": evolved.projects,
                            "reason": evolved.reason,
                            "fresh_target_required": True})
                released = True
                hooks.release_target_environment(environment)
            evolutions = next_evolution
            final_after_stall = evolved.status == "STALLED"
            hooks.emit("READY_FOR_RETRY" if not final_after_stall
                       else "CURRICULUM_STALLED_FINAL_TEST_REQUIRED",
                       {"target_cycle": target_cycles, "evolution": evolutions,
                        "projects": evolved.projects, "reason": evolved.reason})
        finally:
            if not transferred and not released:
                primary_failure = sys.exc_info()[1]
                try:
                    hooks.release_target_environment(environment)
                except Exception as release_error:
                    if primary_failure is None:
                        raise
                    hooks.emit("TARGET_RELEASE_FAILED", {
                        "target_cycle": target_cycles,
                        "error_type": type(release_error).__name__,
                        "error": str(release_error),
                        "primary_error_type": type(primary_failure).__name__,
                    })
