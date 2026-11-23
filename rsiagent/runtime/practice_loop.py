"""Practice-loop resilience, ported from upstream ``explore/practice_loop.py``.

The upstream practice loop distinguishes three things that must never be
confused, because conflating them invents verdicts the machine never produced:

- a **product** segment (the Actor did something: a program, a probe, a look),
- an **actionless** segment (the model replied without acting),
- an **infrastructure** failure (transport, VM, timeout).

Rules reproduced here:

- repeated actionless segments are a *liveness* problem, never a task verdict
  and never Curriculum convergence; past the allowance the loop must recover
  (re-prompt with a narrower request) instead of concluding;
- infrastructure failures are not verdicts either: they are retried within an
  allowance, and only an exhausted allowance is reported, as infrastructure;
- a productive segment RESETS the actionless counter (a long investigation is
  many productive segments, not a stall);
- running out of budget is reported as a budget stop, never as success.
"""
from dataclasses import dataclass, field

MAX_ACTIONLESS_SEGMENTS = 3
MAX_INFRA_RETRIES = 2


class PracticeError(RuntimeError):
    """Base class for practice-loop failures."""


class PracticeBoundaryError(PracticeError):
    """The attempt broke a protocol boundary (bad payload, disallowed action)."""


class PracticeInfrastructureError(PracticeError):
    """Transport, VM or timeout failure: not evidence about the task."""


@dataclass
class PracticeState:
    """Liveness bookkeeping for one practice session."""

    actionless: int = 0
    infra_retries: int = 0
    productive_segments: int = 0
    actionless_recoveries: int = 0
    events: list = field(default_factory=list)

    def note_product(self, what="segment"):
        """A segment that did something on the machine."""
        self.productive_segments += 1
        self.actionless = 0          # productive work resets the stall counter
        self.infra_retries = 0       # and the infra allowance
        self.events.append(("product", what))

    def note_actionless(self):
        """A reply with no action. Returns the recovery directive, if any."""
        self.actionless += 1
        self.events.append(("actionless", self.actionless))
        if self.actionless > MAX_ACTIONLESS_SEGMENTS:
            self.actionless_recoveries += 1
            self.actionless = 0
            return ("recover: the last reply performed no action. Ask for one "
                    "narrow, concrete step and say so explicitly.")
        return None

    def note_infrastructure(self, exc):
        """An infrastructure failure. Raises when the allowance is exhausted."""
        self.infra_retries += 1
        self.events.append(("infrastructure", repr(exc)[:200]))
        if self.infra_retries > MAX_INFRA_RETRIES:
            raise PracticeInfrastructureError(
                "infrastructure failed {} times without a productive segment: {}"
                .format(self.infra_retries, repr(exc)[:300]))
        return "retry"

    def classify(self, exc):
        """Map an exception to the loop's three-way distinction."""
        if isinstance(exc, PracticeInfrastructureError):
            return "infrastructure"
        if isinstance(exc, (PracticeBoundaryError, PracticeError)):
            return "boundary"
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            return "infrastructure"
        return "boundary"


@dataclass
class PracticeBudget:
    """Work budget for one practice target."""

    max_actions: int = 8
    max_verifications: int = 12
    actions: int = 0
    verifications: int = 0

    def spend_action(self):
        if self.actions >= self.max_actions:
            return False
        self.actions += 1
        return True

    def spend_verification(self):
        if self.verifications >= self.max_verifications:
            return False
        self.verifications += 1
        return True

    @property
    def exhausted(self):
        return self.actions >= self.max_actions or self.verifications >= self.max_verifications


def stop_reason(state, budget):
    """Why a loop stopped, in the vocabulary the report must use.

    A budget stop is never reported as success; an infrastructure stop is never
    reported as a task verdict.
    """
    if state.productive_segments == 0 and state.infra_retries:
        return "INFRASTRUCTURE"
    if budget.exhausted:
        return "BUDGET_EXHAUSTED"
    return None
