"""The RSIAgent broad-then-deep exploration loop, model-driven end to end.

Implements the paper's operating sequence over the protocol core
(`rsi.py`) and the model roles (`model_host.py`):

  1. BROAD phase — the Curriculum plans a wave of diverse training tasks;
     every Actor branch runs against one shared pre-wave memory snapshot;
     the Verifier judges each branch independently; only after the whole
     wave is verified does the Actor consolidate, in deterministic order.
     An unresolved verification blocks the wave from committing.
  2. DEEP phase — the Curriculum proposes focused practice built on the
     failures and fragile lessons; after practice the unchanged target is
     retried; an exhausted practice budget is reported, never faked.
  3. FREEZE — a versioned, digest-checked frozen causal memory.
  4. EVALUATE — held-out tasks read the frozen memory only; curriculum and
     consolidation are disabled; a digest change aborts the run.

All inter-role payloads cross as plain text; nothing here knows about any
benchmark or dataset (RFC-0003 decoupling). Deviations from the paper are
reported, not hidden: single shared environment, API-level role isolation
rather than independent VMs, and no QEMU rollback.
"""
import json
import re
import unicodedata
from pathlib import Path

import practice_journal
from practice_loop import PracticeInfrastructureError
from rsi import Protocol
from model_host import ChatClient, ModelHost, LLMError

CURRICULUM_PLANNER_SYSTEM = (
    "You are the Curriculum in the RSIAgent exploration protocol, planning "
    "a BROAD wave. Given the training domain description and the lessons so "
    "far, propose a wave of diverse, achievable training tasks that spreads "
    "coverage wide rather than deep. Reply with one JSON object: either "
    "{\"decision\":\"WAVE\",\"tasks\":[...]} or "
    "{\"decision\":\"SATURATED\"|\"STALLED\",\"report\":\"reason\"}. "
    "SATURATED means further broad exploration is not useful; STALLED means "
    "you cannot responsibly continue. No other text.")


def _parse_tasks(reply, limit=6):
    """Parse the Curriculum's wave plan; tolerate fenced JSON."""
    value = ModelHost._role_object("curriculum", reply)
    tasks = value.get("tasks") if isinstance(value, dict) else None
    if (not isinstance(tasks, list) or not tasks
            or not all(isinstance(t, str) and t.strip() for t in tasks)):
        raise LLMError("curriculum wave plan has no task list")
    if len(tasks) > limit:
        tasks = tasks[:limit]
    return [t.strip() for t in tasks]


class ExperimentRecord:
    """Append-only record of what the run did; the caller persists it."""

    def __init__(self):
        self.entries = []

    def add(self, kind, **fields):
        self.entries.append({"kind": kind, **fields})

    def dump(self):
        return json.dumps(self.entries, ensure_ascii=False, indent=1)


class ExploreRun:
    """One full broad-then-deep exploration over a training domain."""

    def __init__(self, client, max_practices=2, log=None, host=None, held_out=(),
                 wave_workers=1, journal_dir=None):
        if type(wave_workers) is not int or wave_workers < 1:
            raise ValueError("wave_workers must be a positive integer")
        self.wave_workers = wave_workers
        self.journal_dir = Path(journal_dir) if journal_dir is not None else None
        self._held_out = {self._task_key(task) for task in held_out}
        self.client = client
        self.curriculum_history = []
        self.previous_wave_outcomes = []
        self.broad_status = "NOT_STARTED"
        self.waves_completed = 0
        self.host = host if host is not None else ModelHost(
            client, curriculum_history=self.curriculum_history, reconcile_memory=True)
        self.record = ExperimentRecord()
        if log is not None:
            self.client.log = log
        self.max_practices = max_practices
        self.protocol = Protocol(self.host, training_validator=self._reject_held_out)

    @staticmethod
    def _task_key(task):
        if not isinstance(task, str) or not task.strip():
            raise ValueError("tasks must be nonempty strings")
        return " ".join(unicodedata.normalize("NFKC", task).casefold().split())

    def _reject_held_out(self, task):
        # Exact normalized matches only. This cannot detect paraphrases or
        # semantic overlap; callers must independently design disjoint splits.
        if self._task_key(task) in self._held_out:
            raise ValueError("held-out task cannot be used during exploration")

    # --- 1. broad ---------------------------------------------------------
    def broad(self, domain, wave_size=2, training_tasks=None):
        prompt = ("Training domain: {}\nLessons so far: {}\n"
                  "Previous completed wave outcomes: {}\n\n"
                  "Plan the next broad wave of training tasks:".format(
                      domain, json.dumps(self.protocol.memory, ensure_ascii=False),
                      json.dumps(self.previous_wave_outcomes, ensure_ascii=False)))
        system = CURRICULUM_PLANNER_SYSTEM
        if training_tasks is not None:
            if (not isinstance(training_tasks, dict) or not training_tasks or
                    not all(isinstance(k, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", k)
                            and isinstance(v, str) and v.strip()
                            for k, v in training_tasks.items())):
                raise ValueError("training_tasks must map nonempty IDs to nonempty tasks")
            for task in training_tasks.values():
                self._reject_held_out(task)
            system = ("You are the RSIAgent Curriculum planning a broad wave. "
                      "Choose diverse tasks only from the provided training catalog. "
                      "Return JSON with decision WAVE and tasks as distinct catalog IDs, "
                      "or decision SATURATED or STALLED and a nonempty report. "
                      "Do not invent IDs.")
            prompt += "\nTraining catalog (ID to task): " + json.dumps(training_tasks, ensure_ascii=False)
        reply = self.client.complete("curriculum", system, prompt,
                                     history=self.curriculum_history)
        plan = ModelHost._role_object("curriculum", reply)
        decision = plan.get("decision", "WAVE")
        if decision in ("SATURATED", "STALLED"):
            report = plan.get("report")
            if not isinstance(report, str) or not report.strip() or plan.get("tasks"):
                raise LLMError("terminal broad decision requires a report and no tasks")
            self.broad_status = decision
            self.record.add("broad_terminal", decision=decision, report=report.strip())
            return [], []
        if decision != "WAVE":
            raise LLMError("invalid broad curriculum decision")
        selected = _parse_tasks(reply, limit=wave_size)
        if training_tasks is None:
            tasks = selected
        else:
            if len(selected) != len(set(selected)) or any(key not in training_tasks for key in selected):
                raise LLMError("curriculum must select distinct IDs from the training catalog")
            # Preserve the operator's ID so a training-only verifier can request
            # feedback for the same task; task text itself remains unchanged.
            tasks = ["Training task ID: {}\n{}".format(key, training_tasks[key])
                     for key in selected]
        for task in tasks:
            self._reject_held_out(task)
        self.record.add("wave_plan", tasks=tasks, workers=self.wave_workers)
        verdicts = self.protocol.broad_wave(tasks, workers=self.wave_workers)
        # Only completed and committed outcomes become Curriculum context.
        self.broad_status = "WAVE_COMPLETED"
        self.waves_completed += 1
        self.previous_wave_outcomes = [
            {"task": task, "verdict": str(verdict),
             "verifier_report": getattr(verdict, "report", "No detailed report supplied.")}
            for task, verdict in zip(tasks, verdicts)]
        self.record.add("wave_verdicts", tasks=tasks, verdicts=verdicts,
                        answers=self.protocol.last_wave_answers)
        return tasks, verdicts

    # --- 2. deep ----------------------------------------------------------
    def _journal_for(self, target):
        """The durable practice record for a target, or None when disabled."""

        if self.journal_dir is None:
            return None
        name = "deep-{}.json".format(
            practice_journal.sha256_bytes(target.encode("utf-8"))[:16])
        return practice_journal.Journal(self.journal_dir / name, target)

    def _admit(self, target, journal):
        """Read the durable record before any new work is allowed to start."""

        if journal is None or not journal.path.exists():
            return None
        return practice_journal.admit(journal.path, target=target)

    def deep(self, target):
        self._reject_held_out(target)
        journal = self._journal_for(target)
        admission = self._admit(target, journal)
        resume_from = 0

        if admission is not None:
            if admission.kind == practice_journal.INFRASTRUCTURE:
                raise PracticeInfrastructureError(
                    "the previous attempt stopped on an infrastructure event: "
                    + admission.reason)
            if admission.kind in (practice_journal.ALREADY_COMPLETE,
                                  practice_journal.STOPPED,
                                  practice_journal.REDO_LEARNING):
                recovered = self.protocol.recover(target, journal)
                self.record.add("deep_recovered", target=target,
                                recovery=recovered["kind"],
                                verdict=recovered["verdict"],
                                steps_committed=admission.steps_committed)
                return recovered["verdict"]
            resume_from = admission.steps_committed
            self.record.add("deep_resumed", target=target,
                            recovery=admission.kind,
                            steps_committed=resume_from)

        if journal is not None and not journal.path.exists():
            journal.start()

        verdict = self.protocol.deep(target, self.max_practices, journal=journal,
                                     resume_from=resume_from)
        self.record.add("deep", target=target, verdict=verdict,
                        answer=self.protocol.last_target_answer,
                        steps_committed=(journal.steps_committed()
                                         if journal is not None else 0))
        return verdict

    def deep_unified(self, target):
        """Run the unified routing prototype using separate model role histories."""
        from unified import run_unified
        from unified_model_host import UnifiedModelHost
        self._reject_held_out(target)
        host = UnifiedModelHost(self.client, training_validator=self._reject_held_out,
                                curriculum_history=self.curriculum_history,
                                reconcile_memory=True,
                                previous_wave_outcomes=self.previous_wave_outcomes)
        result = run_unified(host, target, self.protocol.memory,
                             max_practices=self.max_practices)
        self.protocol.memory = result.memory
        public = getattr(result.candidate, "public", None)
        answer = public.get("answer") if isinstance(public, dict) else None
        self.record.add("deep_unified", target=target, status=result.status,
                        answer=answer, events=list(result.events))
        return result.status

    # --- 3. freeze ----------------------------------------------------------
    def freeze(self):
        frozen = self.protocol.freeze()
        self.record.add("freeze", digest=frozen.sha256,
                        lessons=len(frozen.read()))
        return frozen

    # --- 4. evaluate ---------------------------------------------------------
    def evaluate(self, held_out, frozen):
        results = []
        for task in held_out:
            candidate, verdict = self.protocol.evaluate(task, frozen)
            # Export only the public answer; the Actor's private transcript is
            # not a benchmark artifact and must not be included in the report.
            results.append({"task": task, "answer": candidate.public["answer"],
                            "verdict": verdict})
            self.record.add("eval", task=task, verdict=verdict)
        return results

    def report(self):
        return {
            "role_calls": dict(self.client.calls),
            "lessons": len(self.protocol.memory),
            "events": self.record.dump(),
            "deviations": [
                "branch concurrency is configured per wave; isolation depends on the configured client",
                "verifier mode: " + getattr(self.host, "verifier_mode", "caller-supplied"),
                "no claim of independent VM isolation or QEMU rollback",
            ],
        }


def run(domain, target, held_out, client, max_practices=2):
    """Drive one full experiment: broad wave -> deep target -> freeze -> evaluate."""
    held_out = list(held_out)
    run_state = ExploreRun(client, max_practices=max_practices, held_out=held_out)
    run_state._reject_held_out(target)
    run_state.broad(domain)
    deep_verdict = ("BROAD_STALLED" if run_state.broad_status == "STALLED"
                    else run_state.deep(target))
    frozen = run_state.freeze()
    evals = run_state.evaluate(held_out, frozen)
    report = run_state.report()
    report["target_verdict"] = deep_verdict
    report["evals"] = evals
    report["frozen_digest"] = frozen.sha256
    return report
