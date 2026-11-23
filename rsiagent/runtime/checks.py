"""Verification checks — model-authored, machine-executed, instruction-derived.

Ported from the upstream RSIAgent ``core/checks.py`` (same gate, same PASS
semantics, same tier-based eviction), adapted to this plugin's transport: a
probe runs through a branch handle that exposes ``run(command) -> (code, out)``
(a VMBranch, a container probe, or a test double).

The model declares done with a list of ``{desc, probe}`` checks; the harness's
only job is (a) a falsifiability gate that rejects lazy or mutating probes, and
(b) running the accepted probes read-only in the environment. PASS/FAIL
semantics are conservative: a probe passes iff its output contains PASS and no
FAIL.
"""
import re
from dataclasses import dataclass


@dataclass
class Check:
    desc: str
    probe: str


@dataclass
class CheckResult:
    desc: str
    probe: str
    passed: bool
    output: str


# Persistent-state mutations that are destructive/installing regardless of target.
# Matched as WHOLE COMMAND WORDS (start-of-string or after a shell separator), not
# substrings — "confirm file" must not trip 'rm ', "grep -w add" must not trip 'dd '.
_HARD_MUTATING_RE = re.compile(
    r"(?:^|[;&|`$(\s])"
    r"(rm|mv|shred|dd|chmod|chown|wget|rmdir|unlink)\s"
    r"|sed\s+-i|pip3?\s+install|apt(?:-get)?\s|curl\s+(?:-\S*\s+)*-[oO]\b")
# File-CREATING commands whose target is checked: writing under /tmp scratch is
# read-only with respect to graded state, so a check may stage a render/copy there.
_TARGETED_MUTATING = ("cp", "tee", "mkdir", "touch", "truncate")
_SCRATCH = "/tmp/"
# Redirections that are harmless in a read-only probe.
_HARMLESS_REDIR = ("2>&1", "2>/dev/null", "&>/dev/null", "1>&2", "2> /dev/null",
                   ">/dev/null", "> /dev/null", "1>/dev/null", "1> /dev/null",
                   ">> /dev/null", ">>/dev/null")

_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_REDIR = re.compile(r">>?\s*([^\s|;&()]+)")
_MKFILE = re.compile(r"\b(cp|tee|mkdir|touch|truncate)\b\s+((?:-\S+\s+)*)([^\s|;&()]+)")

# Interpreter one-liner bodies — python -c "...", perl -e '...'. Quote masking hides
# these from the shell-token scan (a python '>' inside a string is not a redirect),
# but it also blinds the gate to real mutations inside the body.
_INTERP_BODY = re.compile(
    r"(?:python3?|perl|ruby|node|php|bash|sh)\s+-[ce]\s+(['\"])(.*?)\1", re.DOTALL)
_BODY_DELETE = re.compile(
    r"shutil\.(?:rmtree|move)\b"
    r"|os\.(?:remove|unlink|rmdir|removedirs|rename|renames|replace|truncate)\b"
    r"|\.unlink\s*\(|\brmtree\s*\(|\bunlink\b"
    r"|\.write_(?:text|bytes)\s*\(")
_BODY_WRITE_OPEN = re.compile(
    r"open\s*\(\s*[^,)]+,\s*(['\"])[^'\"]*[wax][^'\"]*\1")


def _nonscratch_mutations(probe):
    """Shell-level mutations of non-/tmp state. Empty list means read-only."""
    sk = probe
    for harmless in _HARMLESS_REDIR:
        sk = sk.replace(harmless, " ")
    sk = _QUOTED.sub("  ", sk)          # mask quoted interpreter bodies
    bad = []
    for match in _HARD_MUTATING_RE.finditer(sk):
        bad.append((match.group(1) or match.group(0)).strip())
    for match in _REDIR.finditer(sk):
        if not match.group(1).startswith(_SCRATCH):
            bad.append("redirect to " + match.group(1)[:40])
    for match in _MKFILE.finditer(sk):
        cmd = match.group(1)
        if cmd == "cp":
            tail = sk[match.start():].split("|")[0].split(";")[0].split("&&")[0]
            args = [a for a in tail.split()[1:] if not a.startswith("-")]
            target = args[-1] if len(args) >= 2 else match.group(3)
        else:
            target = match.group(3)
        if not target.startswith(_SCRATCH):
            bad.append(cmd + " " + target[:30])
    for _quote, body in _INTERP_BODY.findall(probe):   # scan UNMASKED bodies
        if _BODY_DELETE.search(body):
            bad.append("interpreter-body delete/move/rename")
        elif _BODY_WRITE_OPEN.search(body) and _SCRATCH not in body:
            bad.append("interpreter-body write to non-scratch")
    return bad


_EXIST = re.compile(r"\btest\s+-[efdrsL]|\[\s*-[efdrsL]\b|\bls\b")
_META = re.compile(r"\bffprobe\b|\bstat\b|\bfile\b|\bwc\b|\bdu\b|identify\b|"
                   r"zipinfo|unzip\s+-l|pdfinfo|\bhead\b|\btail\b")
_CONTENT = re.compile(r"diff|cmp|md5|sha[0-9]|psnr|ssim|compare|==|!=|<=|>=|"
                      r"grep|python|awk|jq|pdftotext|json")

_PRIORITY = {"content": 0, "metadata": 1, "existence": 2}


def _category(probe):
    """Value tier of a check, for capacity eviction: content > metadata > existence."""
    lowered = probe.lower()
    if _CONTENT.search(lowered):
        return "content"
    if _META.search(lowered):
        return "metadata"
    if _EXIST.search(lowered):
        return "existence"
    return "content"


def validate(raw_checks, max_checks=16, instruction="", witness_gates=True):
    """Split model-proposed checks into (accepted, rejections, dropped).

    Rejections are soundness failures (malformed / mutating / vacuous) and veto a
    done declaration. Dropped is capacity overflow beyond max_checks and does not
    veto. Over capacity, the least valuable checks are evicted first.
    """
    instruction_l = (instruction or "").lower()
    accepted_ok, rejections, dropped = [], [], []
    for check in (raw_checks or []):
        if not isinstance(check, dict) or not str(check.get("probe", "")).strip():
            rejections.append("malformed check (need desc + probe): " + str(check)[:120])
            continue
        desc = str(check.get("desc", "")).strip() or "(no desc)"
        probe = str(check["probe"]).strip()
        mutations = _nonscratch_mutations(probe)
        if mutations:
            rejections.append("probe must be READ-ONLY (mutates: "
                              + "; ".join(mutations)[:90] + "): " + probe[:100])
            continue
        if not any(ch in probe for ch in "/~$|`"):
            rejections.append("probe does not inspect the machine (no path/var/pipe): "
                              + probe[:120])
            continue
        if probe.startswith("echo") and not any(ch in probe for ch in "|;&$`"):
            rejections.append("probe is a constant echo — not falsifiable: " + probe[:120])
            continue
        # A probe that reads the agent's own self-report certifies the reporter.
        self_report = None if not witness_gates else re.search(
            r"[\w~/.-]*(?:evidence|verification|_proof|self_?report)[\w.-]*",
            probe, re.IGNORECASE)
        if self_report and self_report.group(0).split("/")[-1].lower() not in instruction_l:
            rejections.append("probe reads a self-authored report file — verify the WORK "
                              "through an independent channel instead: " + probe[:100])
            continue
        accepted_ok.append(Check(desc=desc, probe=probe))

    if len(accepted_ok) > max_checks:
        order = sorted(range(len(accepted_ok)),
                       key=lambda i: (_PRIORITY[_category(accepted_ok[i].probe)], i))
        keep = set(order[:max_checks])
        accepted = [accepted_ok[i] for i in range(len(accepted_ok)) if i in keep]
        dropped = ["over capacity (max {}, kept higher-value checks) — not run: {}".format(
            max_checks, accepted_ok[i].desc[:70])
            for i in range(len(accepted_ok)) if i not in keep]
    else:
        accepted = accepted_ok
    if not accepted and not rejections:
        rejections.append("no checks provided — derive at least one falsifiable check "
                          "from the task's own requirements")
    return accepted, rejections, dropped


def run_checks(accepted, branch, timeout=30):
    """Run accepted probes through the branch handle; conservative PASS semantics."""
    results = []
    for check in accepted:
        result = branch.run(check.probe, timeout=timeout)
        output = result[1] if isinstance(result, tuple) else str(result)
        output = output or ""
        passed = bool(re.search(r"\bPASS\b", output)) and not re.search(r"\bFAIL\b", output)
        results.append(CheckResult(desc=check.desc, probe=check.probe,
                                   passed=passed, output=output[-400:]))
    return results


def summarize(results):
    """One-line feedback for the Actor: which probes passed and why not."""
    if not results:
        return "no checks ran"
    lines = []
    for result in results:
        lines.append("{}: {} — {}".format(
            "PASS" if result.passed else "FAIL", result.desc, result.output[-200:]))
    return "\n".join(lines)
