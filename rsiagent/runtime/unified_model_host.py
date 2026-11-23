"""Text-only chat host for the unified routing prototype.

Separate role histories are API-level separation, not an OS isolation boundary.
This adapter cannot operate benchmark services or certify factual correctness.
"""
import json
from copy import deepcopy
from types import SimpleNamespace
from environment_client import EnvironmentBudgetExceeded
from model_host import ModelHost, LLMError, MEMORY_SYSTEM
from unified import Verification

ACTOR = ("You are the RSIAgent Actor. Solve the task with the given prior lessons. "
         "Use feedback for revision. Reply JSON with answer and evidence.")
VERIFY = ("You are the independent Verifier. Inspect the candidate only, never "
          "request Actor memory or private reasoning. Reply with exactly one JSON "
          "object, for example {\"verdict\":\"PASS\",\"report\":"
          "\"the candidate satisfies the stated requirement\"}. The verdict must "
          "be PASS, FAIL, or UNVERIFIED and report must be a nonempty string. "
          "Do not claim observations you cannot support.")
DIAGNOSIS = ("You are the same Actor preparing a learning diagnosis for the "
             "Curriculum after reconciling memory. Summarize attempted approaches, "
             "verified limitations, plausible causes, uncertainty, and useful "
             "contrasts for future practice. Ground claims in the trajectory and "
             "verifier report; do not invent evidence, reveal private reasoning "
             "or provide a turn-by-turn transcript. Return a concise handoff.")

CURRICULUM = ("You are the RSIAgent Curriculum. Given the target and verifier "
              "report, choose REVISE or EVOLVE after FAIL, HANDOFF or "
              "VERIFY_MORE after PASS. For practice, choose PROJECT with a "
              "task, READY_FOR_RETRY, or STALLED. Reply JSON with decision "
              "and nonempty report; PROJECT also needs task. A PASS route "
              "audits the report and cannot override the verifier.")


class UnifiedModelHost:
    def __init__(self, client, training_validator=None, curriculum_history=None,
                 reconcile_memory=False, previous_wave_outcomes=()):
        self.client = client
        self.model = ModelHost(client, reconcile_memory=reconcile_memory)
        self.training_validator = training_validator
        self.curriculum_history = curriculum_history if curriculum_history is not None else []
        self.learning_diagnosis = None
        self.previous_wave_outcomes = deepcopy(list(previous_wave_outcomes))

    def new_curriculum(self, target):
        return self.curriculum_history

    def new_attempt(self, target, memory):
        return SimpleNamespace(actor=[], verifier=[])

    def _complete(self, role, system, user, history, max_tokens=1024):
        return self.client.complete(role, system, user, history=history,
                                    max_tokens=max_tokens)

    def actor_work(self, attempt, target, memory, feedback):
        try:
            response = self._complete("actor", ACTOR,
                "Task: {}\nMemory: {}\nFeedback: {}".format(
                    target, json.dumps(memory, ensure_ascii=False), feedback or "none"),
                attempt.actor, max_tokens=4096)
        except EnvironmentBudgetExceeded:
            # The observation budget ran out before the Actor answered. Record
            # the attempt as an answerless failure; the run continues and the
            # Verifier still judges it.
            return SimpleNamespace(
                public={"answer": "",
                        "evidence": "observation budget exhausted before an answer"},
                private={"actor_reply": "", "budget_exhausted": True})
        value = ModelHost._role_object("actor", response)
        answer = value.get("answer")
        if answer is None and set(value) and not (
                {"environment_get", "training_judge"} & set(value)):
            # The task defined its own answer schema; the object is the answer.
            answer = value
        if answer is None or (isinstance(answer, str) and not answer.strip()):
            raise LLMError("actor answer missing")
        return SimpleNamespace(public={"answer": answer, "evidence": value.get("evidence", "")},
                               private={"actor_reply": response})

    def verify(self, attempt, target, candidate, feedback):
        answer = deepcopy(candidate.public["answer"])
        user = "Task: {}\nCandidate answer: {}\nAdditional review: {}".format(
            target, json.dumps(answer, ensure_ascii=False), feedback or "none")
        if hasattr(self.client, "complete_verifier"):
            response = self.client.complete_verifier(VERIFY, user, task=target,
                answer=answer, history=attempt.verifier, max_tokens=1024)
        else:
            response = self._complete("verifier", VERIFY, user, attempt.verifier)
        value = ModelHost._role_object("verifier", response)
        try:
            return Verification(value.get("verdict"), value.get("report"))
        except ValueError as exc:
            raise LLMError(str(exc)) from exc

    def route(self, curriculum, target, verification):
        allowed = ("HANDOFF or VERIFY_MORE" if verification.verdict == "PASS"
                   else "REVISE or EVOLVE")
        response = self._complete("curriculum", CURRICULUM,
            "Target: {}\nVerifier verdict: {}\nVerifier report: {}\n"
            "Previous completed wave outcomes: {}\n"
            "Allowed decisions for this verdict: {}. Return decision and report as JSON."
            .format(target, verification.verdict, verification.report,
                    json.dumps(self.previous_wave_outcomes, ensure_ascii=False), allowed), curriculum)
        value = ModelHost._role_object("curriculum", response)
        return value.get("decision"), value.get("report")

    def learn(self, attempt, target, candidate, verification, memory):
        # The same Actor transcript receives the verifier report for distillation.
        response = self._complete("actor",
            MEMORY_SYSTEM,
            "Task: {}\nVerifier report: {}\nVerdict: {}\nDistill one lesson.".format(
                target, verification.report, verification.verdict), attempt.actor)
        value = ModelHost._role_object("memory", response)
        fields = ("condition", "action", "outcome", "support")
        if not all(isinstance(value.get(k), str) and value[k].strip() for k in fields):
            raise LLMError("memory lesson lacks a required field")
        lesson = {k: value[k].strip() for k in fields}
        lesson.update(verdict=verification.verdict, source_task=target)
        if self.model.reconcile_memory:
            reconciled = self.model.reconcile(memory, lesson, attempt.actor)
            diagnosis = self._complete("actor", DIAGNOSIS,
                "Task: {}\nVerifier verdict: {}\nVerifier report: {}\n"
                "Provide the learning diagnosis for Curriculum.".format(
                    target, verification.verdict, verification.report), attempt.actor, max_tokens=2048)
            if not isinstance(diagnosis, str) or not diagnosis.strip() or len(diagnosis) > 16000:
                raise LLMError("actor learning diagnosis is missing or too long")
            self.learning_diagnosis = diagnosis.strip()
            return reconciled
        return list(memory) + [lesson]

    def practice_decision(self, curriculum, target, verification, memory):
        response = self._complete("curriculum", CURRICULUM,
            "Target: {}\nFailure report: {}\nActor learning diagnosis: {}\n"
            "Practice lessons: {}\nChoose PROJECT, READY_FOR_RETRY, or STALLED.".format(
                target, verification.report, self.learning_diagnosis or "not provided",
                json.dumps(memory, ensure_ascii=False)), curriculum)
        value = ModelHost._role_object("curriculum", response)
        decision = value.get("decision")
        return decision, value.get("task") if decision == "PROJECT" else value.get("report")

    def practice(self, curriculum, project, memory):
        if self.training_validator is not None:
            self.training_validator(project)
        attempt = self.new_attempt(project, memory)
        try:
            candidate = self.actor_work(attempt, project, memory, None)
            verification = self.verify(attempt, project, candidate, None)
            if verification.verdict == "UNVERIFIED":
                raise LLMError("practice verification incomplete; memory unchanged")
            return self.learn(attempt, project, candidate, verification, memory)
        finally:
            self.release(attempt)

    def release(self, attempt):
        # Only forget coordinator-side references. Host session storage and
        # traces are governed separately by the configured sandbox policy.
        release = getattr(self.client, "release_history", None)
        for history in (attempt.actor, attempt.verifier):
            if release is not None:
                release(history)
            history.clear()
