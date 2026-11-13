"""Independent verifier — a done-time inspection with its own context.

Ported from upstream ``core/verifier.py`` (doctrine, three verdicts, findings
as data, probe economy, second opinion), adapted to this plugin: probes run in
a throwaway clone of the branch disk through ``verifier_runtime``, and the
inspector is a plain chat call that never sees the Actor's history, checks,
reasoning or memory.

Why independence: an Actor's own checks inherit the Actor's assumptions, so a
false "done" passes them. A zero-history inspector re-derives the requirements
from the task and the machine and judges on evidence alone.
"""
import json
import re
import threading

import checks as checks_module
import verifier_runtime

_VERDICTS = ("PASS", "WRONG", "UNVERIFIED")


class Findings(str):
    """Findings text plus side channels: ``doubts`` (what a pass accepted without
    affirmative evidence) and ``items`` ((requirement, status, evidence) rows)."""

    doubts = ""
    items = ()

    def __new__(cls, text="", doubts="", items=()):
        value = super().__new__(cls, text)
        value.doubts = str(doubts or "")[:800]
        value.items = _clean_items(items)
        return value


def _clean_items(raw):
    """Normalise the inspector's items into (req, status, evidence); malformed rows drop."""
    rows = []
    for item in (raw or [])[:24]:
        if isinstance(item, (tuple, list)) and len(item) == 3:
            # Already-normalised rows (carry_findings round trip).
            req, status, evidence = item
            status = str(status).strip().lower()
            if req and status in ("met", "violated", "could-not-confirm"):
                rows.append((str(req)[:200], status, str(evidence or "")[:300]))
            continue
        if not isinstance(item, dict):
            continue
        req = str(item.get("req") or item.get("requirement") or "").strip()[:200]
        status = str(item.get("status") or "").strip().lower().replace("_", "-")
        if status in ("could not confirm", "couldnot-confirm", "unconfirmed"):
            status = "could-not-confirm"
        if status in ("partially-met", "partial"):
            status = "could-not-confirm"
        if not req or status not in ("met", "violated", "could-not-confirm"):
            continue
        rows.append((req, status, str(item.get("evidence") or "").strip()[:300]))
    return tuple(rows)


def carry_findings(text, source):
    """Rebuild Findings around new text without losing doubts/items."""
    return Findings(text, getattr(source, "doubts", ""), getattr(source, "items", ()))


def unsettled_items(findings):
    """Violated first, then could-not-confirm: the rows a bounce must name."""
    rows = tuple(getattr(findings, "items", ()) or ())
    return (tuple(r for r in rows if r[1] == "violated")
            + tuple(r for r in rows if r[1] == "could-not-confirm"))


def items_punchlist(findings, cap=6):
    """Render unsettled items as a numbered punch list for the Actor."""
    rows = unsettled_items(findings)[:cap]
    if not rows:
        return ""
    lines = ["", "THE UNSETTLED ITEMS (address these specifically):"]
    for number, (req, status, evidence) in enumerate(rows, 1):
        label = "VIOLATED" if status == "violated" else "COULD NOT CONFIRM"
        lines.append("  {}. [{}] {}".format(number, label, req)
                     + (" — " + evidence if evidence else ""))
    settled = sum(1 for row in (getattr(findings, "items", ()) or ()) if row[1] == "met")
    lines.append("  ({} other requirement(s) it judged met — leave those alone.)"
                 .format(settled))
    return "\n".join(lines)


def probe_ok(probe):
    """Read-only guard: the same mutation lint the check gate uses."""
    if not isinstance(probe, str) or not probe.strip():
        return False
    return not checks_module._nonscratch_mutations(probe)


def probe_informative(output):
    """Did a probe return usable evidence? Timeouts and empty output prove nothing."""
    text = (output or "").strip()
    if not text:
        return False
    head = text[:60]
    if "timed out" in head or text.startswith("[channel error"):
        return False
    lowered = text.lower()
    if lowered.endswith("not found") or "command not found" in lowered:
        return False
    if "no such file" in lowered and len(text) < 80:
        return False
    return True


DOCTRINE = """You are an INDEPENDENT INSPECTOR. Someone else claims to have completed the \
task below on this machine. You have deliberately NOT been shown their work, their \
reasoning, their checks or their assumptions — your value is an independent reading. \
Decide whether the machine's CURRENT state satisfies the task.

METHOD:
1. Derive the FULL requirement inventory yourself from the task text: every property it
   states or implies, including naming and convention rules. If the task contains a
   worked instance, derive the convention FROM that instance before judging anything.
2. Probe the machine READ-ONLY to gather evidence for each requirement.
3. Judge each requirement on the evidence alone. Plausible is not verified.
4. Pick the channel by claim type: structural facts are settled by reading values; a
   rendered result is settled by looking at the artifact. When a render is impossible,
   measure instead — derive checkable quantities with code and judge those.
5. Before you emit pass, try to falsify your own conclusion: name the strongest
   plausible way the complete result could still violate the task, including an
   interaction between parts that each look correct alone. If that is unresolved,
   investigate it or answer wrong/unverified; emit pass only when evidence settles it.

Reply with ONLY ONE JSON object per turn:
  {"probes": ["<read-only shell command>", "..."]}      // up to 4 per turn
or your final verdict:
  {"verdict": "pass" | "wrong" | "unverified",
   "findings": "<per requirement: met / violated / could-not-confirm + evidence>",
   "doubts": "<unresolved uncertainty about a MATERIAL requirement; empty for pass>",
   "items": [{"req": "...", "status": "met" | "violated" | "could-not-confirm",
              "evidence": "..."}]}

The verdicts are NOT interchangeable:
- pass: EVERY material requirement is affirmatively evidenced on the machine.
- wrong: concrete evidence that some requirement is VIOLATED; cite it. A probe that ran
  successfully and shows a required artifact ABSENT is wrong, not unverified.
- unverified: you could not gather enough evidence either way. Never wrong on suspicion,
  never pass on hope.

Probe discipline: probes must be read-only (no writes, moves, deletions, installs).
Start from the exact paths the task names; use cheap existence and attribute probes
first and read contents only where a requirement demands it; never scan a whole home
tree. If a probe times out, split it into narrower probes instead of repeating it.
"""


class Inspection:
    """Outcome of one inspection."""

    def __init__(self, verdict, findings, rounds=0, probes=(), accept=False):
        self.verdict = verdict
        self.findings = findings
        self.rounds = rounds
        self.probes = tuple(probes)
        self.accept = accept

    def as_dict(self):
        return {"verdict": self.verdict, "findings": str(self.findings),
                "doubts": getattr(self.findings, "doubts", ""),
                "items": list(getattr(self.findings, "items", ()) or ()),
                "rounds": self.rounds, "probes": list(self.probes),
                "accept": self.accept}


def _parse(reply):
    text = reply.strip()
    if text.startswith("```"):
        blocks = re.findall(r"```(?:[A-Za-z0-9_.+-]+)?[ \t]*\n(.*?)```", text, re.S)
        if len(blocks) == 1:
            text = blocks[0].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        first, last = text.find("{"), text.rfind("}")
        if first < 0 or last <= first:
            raise ValueError("inspector reply is not JSON: " + reply[:200])
        value = json.loads(text[first:last + 1])
    if not isinstance(value, dict):
        raise ValueError("inspector reply must be a JSON object")
    return value


def verify_candidate(task, branch, client, *, max_rounds=6, timeout=60,
                     boot_timeout=240, accept_unverified=False):
    """Inspect a candidate from a zero-history context; return an Inspection.

    Accepted iff the verdict is pass with no material doubts. ``wrong`` always
    bounces with its findings. ``unverified`` bounces unless the caller allows a
    late accept (upstream: could-not-confirm never vetoes forever).
    """
    clone = verifier_runtime.clone_overlay(branch, boot_timeout=boot_timeout)
    history = []
    probes_run = []
    findings = Findings("")
    try:
        prompt = "Task:\n" + task
        for round_no in range(1, max_rounds + 1):
            reply = client.complete("verifier", DOCTRINE, prompt,
                                    history=history, max_tokens=2048)
            value = _parse(reply)
            requested = value.get("probes")
            if isinstance(requested, list) and requested:
                outputs = []
                for probe in requested[:4]:
                    if not isinstance(probe, str) or not probe.strip():
                        outputs.append("probe rejected: not a command")
                        continue
                    if not probe_ok(probe):
                        outputs.append("probe rejected: must be READ-ONLY")
                        continue
                    code, output = clone.run(probe, timeout=timeout)
                    probes_run.append(probe)
                    evidence = (output or "").strip()
                    if not probe_informative(evidence):
                        evidence = "(no usable output; exit {})".format(code)
                    outputs.append("probe: {}\nexit: {}\noutput:\n{}".format(
                        probe, code, evidence[-1200:]))
                prompt = "Task:\n{}\n\nEvidence so far:\n{}".format(
                    task, "\n\n".join(outputs))
                continue
            verdict = str(value.get("verdict", "")).strip().upper()
            if verdict not in _VERDICTS:
                raise ValueError("inspector verdict must be pass/wrong/unverified")
            findings = Findings(str(value.get("findings", ""))[:2000],
                                value.get("doubts", ""), value.get("items") or ())
            accept = (verdict == "PASS" and not findings.doubts)
            if verdict == "UNVERIFIED" and accept_unverified:
                accept = True
            return Inspection(verdict, findings, round_no, probes_run, accept)
        return Inspection("UNVERIFIED", Findings("inspection budget exhausted"),
                          max_rounds, probes_run, accept=False)
    finally:
        clone.close()
        try:
            branch.resume()
        except AttributeError:
            pass


class BranchVerifierHost:
    """Host adapter: the Protocol's verify() becomes an independent inspection.

    Wraps a ModelHost so the Actor role, consolidation and curriculum stay as
    they are, while verification is done by ``verify_candidate`` against the
    branch the Actor worked in. Acceptance is the conjunction upstream uses:
    the Actor's own checks already passed (VMClient enforces that), and the
    inspector does not find a violation.
    """

    def __init__(self, model_host, vm_client, chat_client, *, max_rounds=6,
                 accept_unverified=False, boot_timeout=240):
        self.model_host = model_host
        self.vm_client = vm_client
        self.chat_client = chat_client
        self.max_rounds = max_rounds
        self.accept_unverified = accept_unverified
        self.boot_timeout = boot_timeout
        # Per-thread, like the VM client's attempt state: a wave runs several
        # inspections at once and a shared slot would report another attempt's
        # result.
        self._state = threading.local()
        self.inspections = []
        self._inspections_lock = threading.Lock()

    @property
    def last_inspection(self):
        """This thread's most recent inspection, or None."""
        return getattr(self._state, "inspection", None)

    @last_inspection.setter
    def last_inspection(self, value):
        self._state.inspection = value

    def run_actor(self, task, memory):
        return self.model_host.run_actor(task, memory)

    def release_candidate(self, candidate):
        release = getattr(self.model_host, "release_candidate", None)
        if release is not None:
            release(candidate)

    def consolidate(self, memory, task, candidate, verdict):
        return self.model_host.consolidate(memory, task, candidate, verdict)

    def curriculum_review(self, task, verdict, memory):
        return self.model_host.curriculum_review(task, verdict, memory)

    def verify(self, task, public):
        branch = getattr(self.vm_client, "last_branch", None)
        if branch is None:
            # No branch to inspect (non-VM run): fall back to the model judge.
            return self.model_host.verify(task, public)
        inspection = verify_candidate(task, branch, self.chat_client,
                                      max_rounds=self.max_rounds,
                                      accept_unverified=self.accept_unverified,
                                      boot_timeout=self.boot_timeout)
        self.last_inspection = inspection
        with self._inspections_lock:
            self.inspections.append(inspection)
        report = str(inspection.findings)
        punchlist = items_punchlist(inspection.findings)
        if punchlist:
            report = report + punchlist
        verdict = "PASS" if inspection.accept else "FAIL"
        from model_host import Verdict
        return Verdict(verdict, report or inspection.verdict)
