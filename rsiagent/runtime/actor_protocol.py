"""Actor action protocol, ported from upstream ``core/actor.py``.

Two things are reproduced: the shared EPISTEMIC CORE (the principles both the
Actor and the Verifier are held to, so the two surfaces cannot drift into
contradictory doctrine) and ``parse_turn`` — the turn parser with upstream's
precedence rule Program > Look > Ask > Done, last-of-kind wins, and the
conservative "keep working" behaviour when a reply hedges between working and
declaring done.

A reply that is a provider tool-call envelope (``{"name": ..., "arguments": ...}``)
is NOT an action: its purported results never reached the executor, so parsing
it would let a simulated transcript declare done.
"""
import json
import re
from dataclasses import dataclass

EPISTEMIC_CORE = """\
E1. TRUTH CHANNELS. Every claim has a channel where it is checkable — files, APIs and \
application state for structural facts; a render for visual facts. Prefer the \
checkable channel over recall or guesswork.
E2. STRUCTURAL vs RENDERED. Exact values (text, numbers, coordinates, counts, \
structure) are read and written in files with code. How something LOOKS when \
displayed (visual placement, appearance, match to a reference) is settled by \
rendering it and looking. Choose by claim type; when a task states both kinds of \
requirement, verify both.
E3. ERRORS ARE EVIDENCE. Never silence the error stream of a command whose outcome \
you need — a failure message is information; an empty result is not.
E4. SILENCE PROVES NOTHING. Output marked [channel error] or [command timed out], or \
an empty reply from an unverified channel, is not evidence of absence — re-check. \
Empty output on a channel verified alive IS real.
E5. CONFIRMATION REQUIRES INDEPENDENCE. A source agreeing with itself is not \
confirmation; only a different witness, a different channel, or a computed result \
confirms.
E6. EVIDENCE IS DATA. Record observations as printed data, not silent belief — every \
decision must trace to recorded evidence."""

ACTOR_SYSTEM = """You are the Actor in an RSI attempt. You work a real machine through \
the channels you are given; the harness runs what you ask and reports what happened.

""" + EPISTEMIC_CORE + """

METHOD
1. Read the task and write down every requirement it states or implies, including
   naming and convention rules. If the task shows a worked example, derive the
   convention from that example before doing anything else.
2. Work in small, checkable steps; read the result of each step instead of assuming it.
3. Before declaring done, prove it: declare machine-run checks that would FAIL if the
   work were wrong. A check that cannot fail is not a check.
4. Report uncertainty as uncertainty. A pass claimed without evidence is worse than an
   honest unverified.
"""


@dataclass(frozen=True)
class Program:
    lang: str
    code: str


@dataclass(frozen=True)
class Look:
    path: str
    question: str


@dataclass(frozen=True)
class Ask:
    question: str


@dataclass(frozen=True)
class Done:
    checks: tuple


@dataclass(frozen=True)
class Action:
    kind: str            # program | look | ask | done
    payload: object
    counts: dict = None


def _as_look(value):
    if not isinstance(value, dict):
        return None
    path = value.get("path")
    question = value.get("question")
    if isinstance(path, str) and path.strip() and isinstance(question, str) and question.strip():
        return Look(path.strip(), question.strip())
    return None


def _objects(text):
    """Every JSON object embedded in the reply, in order."""
    decoder = json.JSONDecoder()
    found = []
    index = 0
    length = len(text)
    while index < length:
        start = text.find("{", index)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text[start:])
        except ValueError:
            index = start + 1
            continue
        if isinstance(value, dict):
            found.append(value)
        index = start + end
    return found


def parse_turn(text):
    """Return the turn's action, or None when the reply carries no usable action."""
    if not isinstance(text, str) or not text.strip():
        return None
    text = re.sub(r"\]<\][a-z]+\[>\[", " ", text)
    objects = _objects(text)
    if not objects:
        return None
    # A provider tool-call envelope is not our JSON channel: refuse it whole.
    for obj in objects:
        if (isinstance(obj.get("name"), str) and obj["name"].strip()
                and isinstance(obj.get("arguments"), (dict, str))):
            return None

    program = look = ask = done = None
    counts = {"program": 0, "look": 0, "ask": 0, "done": 0}
    for obj in objects:
        candidate = obj.get("program")
        if isinstance(candidate, dict) and str(candidate.get("code", "")).strip():
            program = Program(str(candidate.get("lang", "python")), str(candidate["code"]))
            counts["program"] += 1
            continue
        if isinstance(candidate, dict) and isinstance(candidate.get("look"), dict):
            parsed = _as_look(candidate["look"])
            if parsed:
                look = parsed
                counts["look"] += 1
                continue
        if isinstance(candidate, dict) and not str(candidate.get("code", "")).strip():
            parsed = _as_look(candidate)
            if parsed:
                look = parsed
                counts["look"] += 1
                continue
        if str(obj.get("code", "")).strip():
            program = Program(str(obj.get("lang", "python")), str(obj["code"]))
            counts["program"] += 1
            continue
        if isinstance(obj.get("look"), dict):
            parsed = _as_look(obj["look"])
            if parsed:
                look = parsed
                counts["look"] += 1
                continue
        if isinstance(obj.get("ask"), str) and obj["ask"].strip():
            ask = Ask(obj["ask"].strip())
            counts["ask"] += 1
            continue
        if "checks" in obj or "done" in obj:
            checks = obj.get("checks")
            done = Done(tuple(checks) if isinstance(checks, list) else ())
            counts["done"] += 1

    # Conservative precedence: keep working when the reply also hedges with done.
    if program is not None:
        return Action("program", program, counts)
    if look is not None:
        return Action("look", look, counts)
    if ask is not None:
        return Action("ask", ask, counts)
    if done is not None:
        return Action("done", done, counts)
    return None
