"""Give each Actor attempt its own disposable VM behind a chat client.

The model requests shell work by replying with a single JSON object
``{"vm_run": "<command>"}``; the command executes inside the branch's VM and
the output is fed back. A done declaration may carry model-authored checks
(``{"answer": ..., "checks": [{"desc": ..., "probe": ...}]}``); they pass the
falsifiability gate in ``checks.py`` and then run read-only in the same VM, so
"done" means the model proved the work on the machine rather than asserting it.

Every attempt (keyed by the Actor's history list) gets a fresh overlay on the
prepared base image, so attempts cannot see each other's files and the disk is
thrown away at release — the paper's per-branch isolation and rollback.
"""
import json
import threading

import actor_protocol
import checks as checks_module
from model_host import (LLMError, parse_json_sequence, parse_leading_object,
                        parse_repeated_json)


class VMClient:
    """Actor-in-a-VM wrapper; other roles pass straight through."""

    def __init__(self, client, base_image, *, max_commands=12, require_checks=True,
                 max_checks=8, branch_factory=None, **branch_kwargs):
        self.client = client
        self.base_image = base_image
        self.max_commands = max_commands
        self.require_checks = require_checks
        self.max_checks = max_checks
        self.branch_factory = branch_factory
        self.branch_kwargs = branch_kwargs
        self.branches = {}          # id(history) -> (history, branch)
        self._branches_lock = threading.Lock()
        self.calls = client.calls
        self.completed_branches = []
        self.check_reports = []        # every attempt's gate report, in order
        # Per-thread attempt state. ``broad_wave`` runs one whole attempt
        # (actor + verification) per worker thread, so a single shared
        # attribute would let one attempt's verifier inspect another attempt's
        # disk and report a confident PASS about work it never saw.
        self._state = threading.local()

    # --- the attempt this thread is working on ---------------------------
    @property
    def last_branch(self):
        """The branch this thread's attempt worked in, or None."""
        return getattr(self._state, "branch", None)

    @last_branch.setter
    def last_branch(self, value):
        self._state.branch = value

    @property
    def last_check_report(self):
        """The check gate's report for this thread's attempt, or None."""
        return getattr(self._state, "check_report", None)

    @last_check_report.setter
    def last_check_report(self, value):
        self._state.check_report = value
        if value is not None:
            with self._branches_lock:
                self.check_reports.append(value)

    # --- branch lifecycle -------------------------------------------------
    def _factory(self):
        if self.branch_factory is not None:
            return self.branch_factory(self.base_image, **self.branch_kwargs)
        from vm_host import VMBranch
        return VMBranch(self.base_image, **self.branch_kwargs)

    def _branch_for(self, history):
        key = id(history)
        with self._branches_lock:
            entry = self.branches.get(key)
        if entry is not None and entry[0] is history:
            return entry[1]
        # Boot outside the lock: a VM takes seconds and other attempts must not
        # wait behind it.
        branch = self._factory().start()
        with self._branches_lock:
            self.branches[key] = (history, branch)
        return branch

    def release_history(self, history):
        with self._branches_lock:
            entry = self.branches.pop(id(history), None)
        if entry is not None and entry[0] is history:
            branch = entry[1]
            # Read the size before close(): the overlay is deleted on close.
            size = getattr(branch, "overlay_bytes", 0)
            try:
                branch.close()
            except Exception:  # a released branch must never mask a real error
                pass
            self.completed_branches.append(size)
        release = getattr(self.client, "release_history", None)
        if release is not None:
            release(history)

    def close_all(self):
        with self._branches_lock:
            live = [history for history, _ in self.branches.values()]
        for history in live:
            self.release_history(history)

    # --- completion -------------------------------------------------------
    def complete(self, role, system, user, max_tokens=512, temperature=0.0,
                 history=None, **kwargs):
        if role != "actor":
            return self.client.complete(role, system, user, max_tokens=max_tokens,
                                        temperature=temperature, history=history,
                                        **kwargs)
        if history is None:
            raise LLMError("the Actor needs a history list to own a branch VM")
        branch = self._branch_for(history)
        self.last_branch = branch
        task = user.split("\n\nPrior lessons")[0]
        instructions = (
            system + "\nYou work inside a private disposable Linux VM. Request a shell "
            "command by replying with only JSON {\"vm_run\":\"<command>\"}. You will "
            "receive its exit code and output. To declare the task done, reply with "
            '{"answer": <answer>, "checks": [{"desc": "...", "probe": "<read-only '
            'shell command that prints PASS or FAIL>"}]}. Checks run on the machine, so '
            "they must inspect the work itself and must not modify anything outside "
            "/tmp. A failed check sends you back to work.")
        prompt = user
        turns = 0
        commands_used = 0
        while turns <= self.max_commands:
            turns += 1
            remaining = self.max_commands - commands_used
            note = ("No shell commands remain; declare done with your answer and "
                    "checks now." if remaining == 0 else
                    "Shell commands remaining: {}.".format(remaining))
            reply = self.client.complete(role, instructions, prompt + "\n\n" + note,
                                         max_tokens=max_tokens,
                                         temperature=temperature, history=history,
                                         **kwargs)
            commands = self._commands(reply)
            if commands is not None:
                if remaining <= 0:
                    raise LLMError("the Actor kept requesting shell commands after the budget")
                chunks = []
                for command in commands:
                    if commands_used >= self.max_commands:
                        chunks.append("Command: {}\n[not run: command budget exhausted]"
                                      .format(command))
                        continue
                    code, output = branch.run(command)
                    commands_used += 1
                    if len(output) > 2000:
                        output = output[:2000] + "\n[output truncated]"
                    chunks.append("Command: {}\nExit code: {}\nOutput:\n{}".format(
                        command, code, output))
                prompt = "\n\n".join(chunks)
                continue
            program = self._program(reply)
            if program is not None:
                if commands_used >= self.max_commands:
                    raise LLMError("the Actor kept submitting programs after the budget")
                code, output = self._run_program(branch, program)
                commands_used += 1
                if len(output) > 2000:
                    output = output[:2000] + "\n[output truncated]"
                prompt = "Program ({}):\n{}\nExit code: {}\nOutput:\n{}".format(
                    program.lang, program.code[:600], code, output)
                continue
            decision = self._declaration(reply)
            if decision is None:
                if not self.require_checks:
                    return reply                 # plain prose/text answer
                # A reply that is neither an action nor a declaration is not a
                # done. Accepting it used to turn a model's garbled echo of its
                # own output into an "answer" that skipped the check gate.
                prompt = ("That reply was neither a shell request nor a done "
                          'declaration. Reply with {"vm_run": "<command>"} to work, '
                          'or declare done with {"answer": ..., "checks": '
                          '[{"desc": ..., "probe": ...}]}.')
                continue
            if decision.get("checks") is None:
                if not self.require_checks:
                    return reply
                prompt = ("You declared done without checks. Reply again with "
                          '{"answer": ..., "checks": [{"desc": ..., "probe": ...}]}; each '
                          "probe is a read-only command that prints PASS or FAIL.")
                continue
            accepted, rejections, dropped = checks_module.validate(
                decision.get("checks"), max_checks=self.max_checks, instruction=task)
            if rejections:
                self.last_check_report = {"rejected": rejections, "dropped": dropped}
                prompt = ("Your checks were rejected:\n- " + "\n- ".join(rejections) +
                          "\nRewrite them: each probe must be read-only, inspect the "
                          "actual work, and print PASS or FAIL.")
                continue
            results = checks_module.run_checks(accepted, branch)
            self.last_check_report = {
                "rejected": [], "dropped": dropped,
                "results": [{"desc": r.desc, "passed": r.passed,
                             "output": r.output[-200:]} for r in results]}
            if results and all(result.passed for result in results):
                if dropped:
                    # Capacity overflow is recorded but does not veto a declaration.
                    self.last_check_report["dropped"] = dropped
                return reply
            prompt = ("Machine checks failed, so the task is not done:\n" +
                      checks_module.summarize(results) +
                      "\nFix the work, then declare done again.")
        raise LLMError("the Actor exhausted its turns without passing its own checks")

    @staticmethod
    def _commands(reply):
        """Return the batch of vm_run commands, or None when this is not a request."""
        values = parse_json_sequence(reply)
        if values is None:
            value = parse_leading_object(reply)
            values = [value] if value is not None else []
        if not values or not all(isinstance(v, dict) and set(v) == {"vm_run"}
                                for v in values):
            return None
        commands = []
        for value in values:
            command = value["vm_run"]
            if not isinstance(command, str) or not command.strip():
                raise LLMError("vm_run must be a nonempty string")
            commands.append(command)
        return commands

    @staticmethod
    def _program(reply):
        """A whole-program submission (upstream's primary action channel)."""
        action = actor_protocol.parse_turn(reply)
        if action is not None and action.kind == "program":
            return action.payload
        return None

    @staticmethod
    def _run_program(branch, program):
        """Stage the program in the guest and run it with its interpreter."""
        import shlex
        if program.lang in ("python", "python3"):
            path, runner = "/tmp/rsi_program.py", "python3"
        elif program.lang in ("bash", "sh", "shell"):
            path, runner = "/tmp/rsi_program.sh", "bash"
        else:
            raise LLMError("unsupported program language: " + str(program.lang))
        staged = branch.run("cat > {} <<'RSI_PROGRAM_EOF'\n{}\nRSI_PROGRAM_EOF"
                            .format(path, program.code))
        if staged[0] != 0:
            return staged
        return branch.run("{} {}".format(runner, shlex.quote(path)))

    @staticmethod
    def _declaration(reply):
        """Return the done-declaration object, if the reply is one."""
        value = parse_repeated_json(reply)
        if value is None:
            value = parse_leading_object(reply)
        if isinstance(value, dict) and "answer" in value:
            return value
        return None
