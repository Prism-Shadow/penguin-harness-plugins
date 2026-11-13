"""Host-driven target routing for the RSIAgent unified lifecycle.

The host supplies role sessions and environment isolation; this state machine
cannot supply those security boundaries. Verifier reports are mandatory and no
external benchmark score is accepted. The target is never changed by practice.
"""
from copy import deepcopy
from dataclasses import dataclass


@dataclass(frozen=True)
class Verification:
    verdict: str
    report: str

    def __post_init__(self):
        if self.verdict not in ("PASS", "FAIL", "UNVERIFIED"):
            raise ValueError("invalid verifier verdict")
        if not isinstance(self.report, str) or not self.report.strip():
            raise ValueError("verifier report is required")


@dataclass(frozen=True)
class UnifiedResult:
    status: str
    memory: object
    candidate: object
    verification: Verification
    events: tuple


def run_unified(host, target, memory, *, max_target_actions=8,
                max_verifications=12, max_practices=8):
    """Drive routing with one Curriculum session and bounded work.

    Required host methods: new_curriculum, new_attempt, actor_work,
    verify, route, learn, practice_decision and practice. An attempt owns
    (environment, actor, verifier), all retained through REVISE and
    VERIFY_MORE. An EVOLVE releases it and creates a fresh attempt only
    after Curriculum requests READY_FOR_RETRY. host.release is always called.
    Practice must return verified, actor-consolidated memory; the host enforces
    isolation and rejects held-out tasks. A budget stop is not success.
    """
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target must be nonempty")
    if any(type(n) is not int or n < 1 for n in
           (max_target_actions, max_verifications)) or (
            type(max_practices) is not int or max_practices < 0):
        raise ValueError("invalid work budget")
    curriculum = host.new_curriculum(target)
    if curriculum is None:
        raise RuntimeError("Curriculum session missing")
    state = deepcopy(memory)
    events = []
    actions = checks = practices = 0
    candidate = None
    verification = Verification("UNVERIFIED", "No target verification yet")
    feedback = None

    def result(status):
        return UnifiedResult(status, deepcopy(state), candidate, verification,
                             tuple(events))

    while True:
        if actions >= max_target_actions or checks >= max_verifications:
            return result("BUDGET_EXHAUSTED")
        attempt = host.new_attempt(target, deepcopy(state))
        if attempt is None:
            raise RuntimeError("target attempt missing")
        evolve = False
        try:
            while True:
                if actions >= max_target_actions:
                    return result("BUDGET_EXHAUSTED")
                candidate = host.actor_work(attempt, target, deepcopy(state), feedback)
                feedback = None
                actions += 1
                events.append("ACTOR")
                while True:
                    if checks >= max_verifications:
                        return result("BUDGET_EXHAUSTED")
                    verification = host.verify(attempt, target, candidate, feedback)
                    checks += 1
                    feedback = None
                    if not isinstance(verification, Verification):
                        raise RuntimeError("host must return Verification")
                    events.append("VERIFY_" + verification.verdict)
                    if verification.verdict == "UNVERIFIED":
                        return result("UNVERIFIED")
                    decision = host.route(curriculum, target, verification)
                    allowed = (("HANDOFF", "VERIFY_MORE") if
                               verification.verdict == "PASS" else ("REVISE", "EVOLVE"))
                    if (not isinstance(decision, tuple) or len(decision) != 2 or
                            decision[0] not in allowed or
                            not isinstance(decision[1], str) or not decision[1].strip()):
                        raise RuntimeError("invalid Curriculum route or report")
                    route, routing_report = decision
                    events.append("ROUTE_" + route)
                    if route == "HANDOFF":
                        return result("PASS")
                    if route == "VERIFY_MORE":
                        feedback = routing_report
                        continue
                    if route == "REVISE":
                        feedback = verification.report
                        break
                    state = deepcopy(host.learn(attempt, target, candidate,
                                                verification, deepcopy(state)))
                    events.append("LEARN")
                    evolve = True
                    break
                if evolve:
                    break
        finally:
            host.release(attempt)
        while True:
            decision = host.practice_decision(curriculum, target, verification,
                                              deepcopy(state))
            if not isinstance(decision, tuple) or len(decision) != 2:
                raise RuntimeError("invalid practice decision")
            kind, value = decision
            if kind == "READY_FOR_RETRY":
                if not isinstance(value, str) or not value.strip():
                    raise RuntimeError("readiness report is required")
                events.append("READY_FOR_RETRY")
                break
            if kind == "STALLED":
                if not isinstance(value, str) or not value.strip():
                    raise RuntimeError("stall report is required")
                events.append("STALLED")
                return result("STALLED")
            if kind != "PROJECT" or not isinstance(value, str) or not value.strip():
                raise RuntimeError("practice project must be nonempty")
            if practices >= max_practices:
                return result("BUDGET_EXHAUSTED")
            state = deepcopy(host.practice(curriculum, value, deepcopy(state)))
            practices += 1
            events.append("PRACTICE")
