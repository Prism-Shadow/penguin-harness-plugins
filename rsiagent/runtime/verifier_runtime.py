"""Verifier programs run against a *clone* of the branch disk.

Upstream ``core/verifier_runtime.py`` gives the Verifier its own execution
boundary: it may observe and test the candidate, but its effects can never
enter the scored state (namespaces in one mode, a QEMU checkpoint and restore
in the other). This module provides the same property with overlay chains:

    base image -> branch overlay (the Actor's disk)
               -> verifier overlay (a throwaway clone)

The Verifier boots the clone, runs read-only probes, and the clone is deleted
afterwards. Anything the Verifier writes lands in the clone's overlay and dies
with it; the Actor's disk is untouched, so a verifier can never help a
candidate by accident. Verified by ``tests/test_verifier_runtime.py``.
"""
import re

from vm_host import VMBranch


class VerifierRuntimeError(RuntimeError):
    """The verifier clone could not be created or driven."""


def clone_overlay(branch, *, boot_timeout=240, **kwargs):
    """Boot a disposable clone of ``branch``'s disk and return it.

    The caller owns the returned branch and must close it. The clone's base is
    the Actor branch's overlay, so it sees exactly the candidate state and
    nothing the Actor wrote after the clone was made.
    """
    overlay = getattr(branch, "overlay", None)
    if not overlay:
        raise VerifierRuntimeError("the branch has no overlay disk to clone")
    # The Actor's VM must not write while the clone reads the same file. Flush
    # the guest cache, freeze the vCPUs, then boot the clone; ``resume`` is the
    # caller's job so the Actor can continue if verification bounces.
    try:
        branch.sync()
        branch.pause()
    except AttributeError:
        pass                      # a branch without a monitor channel
    clone = VMBranch(overlay, boot_timeout=boot_timeout, **kwargs)
    clone.start()
    return clone


def run_probe(branch, probe, *, timeout=60, boot_timeout=240, **kwargs):
    """Run one verifier probe in a clone of the branch; return (code, output).

    PASS/FAIL semantics stay with the caller: this function only reports what
    the machine did.
    """
    clone = clone_overlay(branch, boot_timeout=boot_timeout, **kwargs)
    try:
        return clone.run(probe, timeout=timeout)
    finally:
        clone.close()


def verdict_from(output):
    """Conservative verdict: PASS only when the probe says PASS and never FAIL."""
    text = output or ""
    if re.search(r"\bFAIL\b", text):
        return "FAIL"
    if re.search(r"\bPASS\b", text):
        return "PASS"
    return "UNVERIFIED"


def verify_candidate(branch, probes, *, timeout=60, boot_timeout=240):
    """Run several probes in ONE clone; report per-probe results and a verdict.

    One clone per verification batch keeps the cost at a single boot while
    still isolating every effect from the Actor's disk.
    """
    clone = clone_overlay(branch, boot_timeout=boot_timeout)
    results = []
    try:
        for probe in probes:
            code, output = clone.run(probe, timeout=timeout)
            results.append({"probe": probe, "exit_code": code,
                            "output": (output or "")[-400:],
                            "verdict": verdict_from(output)})
    finally:
        clone.close()
    if any(result["verdict"] == "FAIL" for result in results):
        overall = "FAIL"
    elif results and all(result["verdict"] == "PASS" for result in results):
        overall = "PASS"
    else:
        overall = "UNVERIFIED"
    return {"verdict": overall, "results": results}
