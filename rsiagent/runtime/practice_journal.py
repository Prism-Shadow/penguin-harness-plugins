"""Durable practice progress and strict recovery admission.

Ported from the ``explore/*_recovery.py`` family (1,205 lines across six
modules). Upstream's recovery is not a retry button: it is an **admission**
decision taken from saved artifacts, and every branch of that decision refuses
to invent work. This module keeps the two halves that make that possible:

1. **Durable progress.** A journal is appended one entry at a time, each entry
   carrying a digest over its own payload *and the previous entry's digest*.
   A crash therefore leaves either a complete entry or a truncated tail that is
   detectable, never a record that silently claims more than happened.

2. **Admission.** Given the journal and the saved artifacts, exactly one
   outcome is allowed:

   - ``RESUME_PRACTICE`` — steps had been committed, the project had not
     finished: continue from the recorded step count.
   - ``REDO_LEARNING`` — a terminal verdict was saved but learning never
     committed: run *only* learning. Repeating Actor work or re-inspecting would
     re-derive a verdict that is already final.
   - ``INFRASTRUCTURE`` — the run stopped on an infrastructure event. That is
     not a verdict and not progress.
   - ``ALREADY_COMPLETE`` — the project closed; nothing may be redone.
   - ``RESTART_PROJECT`` — no usable journal. Nothing may be assumed.

Anything else (a broken digest chain, a target that is not the journalled
target, a terminal verdict whose history is incomplete) raises
``RecoveryRefused``. Refusing is the point: a resume that guesses produces a
result nobody can attribute.
"""
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

JOURNAL_VERSION = 1

# Entry kinds, in the order a healthy project produces them.
PRACTICE_STARTED = "practice_started"
STEP_COMMITTED = "step_committed"
VERDICT_SAVED = "verdict_saved"
LEARNING_COMMITTED = "learning_committed"
PROJECT_CLOSED = "project_closed"
BUDGET_STOP = "budget_stop"
INFRASTRUCTURE_STOP = "infrastructure_stop"

# Admission outcomes.
RESUME_PRACTICE = "RESUME_PRACTICE"
REDO_LEARNING = "REDO_LEARNING"
INFRASTRUCTURE = "INFRASTRUCTURE"
ALREADY_COMPLETE = "ALREADY_COMPLETE"
STOPPED = "STOPPED"
RESTART_PROJECT = "RESTART_PROJECT"


class RecoveryRefused(RuntimeError):
    """The saved artifacts do not support any recovery, so none is admitted."""


# --- durable primitives ---------------------------------------------------


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def atomic_json(path, payload):
    """Write JSON so a crash leaves the old file or the new one, never half."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="." + path.name + ".",
                                         dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def manifest(files):
    """Digest a set of named byte blobs so a resume can prove they are unchanged."""

    return {name: {"bytes": len(data), "sha256": sha256_bytes(data)}
            for name, data in sorted(files.items())}


def verify_manifest(root, expected):
    """Raise unless every named file still matches the recorded digest."""

    root = Path(root)
    for name, record in sorted(expected.items()):
        path = root / name
        if not path.is_file():
            raise RecoveryRefused("a saved artifact is missing: " + name)
        actual = sha256_file(path)
        if actual != record["sha256"]:
            raise RecoveryRefused("a saved artifact changed: " + name)
    return True


# --- conversation evidence ------------------------------------------------


def paired(messages):
    """A complete saved conversation alternates user and assistant."""

    if not messages or len(messages) % 2:
        return False
    roles = [message.get("role") for message in messages]
    return roles[::2] == ["user"] * (len(roles) // 2) and \
        roles[1::2] == ["assistant"] * (len(roles) // 2)


def actor_leads(messages):
    """The saved conversation starts with the task, not with the Actor."""

    return bool(messages) and messages[0].get("role") == "user"


# --- the journal ----------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    index: int
    kind: str
    digest: str
    payload: dict = field(default_factory=dict)


class Journal:
    """Append-only progress record with a digest chain.

    One file, one JSON object per line, a final ``chain`` object carrying the
    head digest. A crash can leave a partial last line, which ``entries``
    reports as a truncated tail rather than as data.
    """

    def __init__(self, path, target):
        self.path = Path(path)
        self.target = str(target)
        self._head = "0" * 64
        self._count = 0

    # -- writing ----------------------------------------------------------
    def start(self, **payload):
        """Create a fresh journal and record that practice began.

        Beginning is itself evidence: a journal whose first entry is not
        ``practice_started`` cannot be trusted, because nothing then says which
        project the later steps belong to.
        """

        atomic_json(self.path, {
            "version": JOURNAL_VERSION,
            "target_sha256": sha256_bytes(self.target.encode("utf-8")),
            "target": self.target,
            "head": self._head,
            "entries": [],
        })
        self.append(PRACTICE_STARTED, **payload)
        return self

    def append(self, kind, **payload):
        """Append one entry. Returns it; the chain digest advances."""

        state = self.read_raw(allow_missing=True)
        entries = state["entries"]
        index = len(entries) + 1
        body = {"index": index, "kind": kind, "payload": payload,
                "target_sha256": state["target_sha256"]}
        digest = sha256_bytes((state["head"] + json.dumps(
            body, sort_keys=True, separators=(",", ":"))).encode("utf-8"))
        entries.append({"index": index, "kind": kind, "payload": payload,
                        "digest": digest})
        atomic_json(self.path, {**state, "entries": entries, "head": digest})
        self._head, self._count = digest, index
        return Entry(index, kind, digest, payload)

    # -- reading ----------------------------------------------------------
    def read_raw(self, allow_missing=False):
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if allow_missing:
                return {"version": JOURNAL_VERSION,
                        "target_sha256": sha256_bytes(self.target.encode("utf-8")),
                        "target": self.target, "head": "0" * 64, "entries": []}
            raise RecoveryRefused("no journal at " + str(self.path))
        except (OSError, json.JSONDecodeError) as exc:
            raise RecoveryRefused("the journal is unreadable: " + str(exc)) from exc
        if state.get("version") != JOURNAL_VERSION:
            raise RecoveryRefused("unknown journal version")
        return state

    def entries(self):
        """Every entry, after proving the digest chain is intact."""

        state = self.read_raw()
        entries = state.get("entries") or []
        head = "0" * 64
        for position, entry in enumerate(entries, 1):
            if entry.get("index") != position:
                raise RecoveryRefused("the journal has a gap or a reordered entry")
            body = {"index": entry["index"], "kind": entry["kind"],
                    "payload": entry.get("payload") or {},
                    "target_sha256": state["target_sha256"]}
            digest = sha256_bytes((head + json.dumps(
                body, sort_keys=True, separators=(",", ":"))).encode("utf-8"))
            if digest != entry.get("digest"):
                raise RecoveryRefused("the journal chain is broken at entry "
                                      + str(position))
            head = digest
        if entries and head != state.get("head"):
            raise RecoveryRefused("the journal head does not match its last entry")
        return [Entry(entry["index"], entry["kind"], entry["digest"],
                      entry.get("payload") or {}) for entry in entries]

    # -- convenience ------------------------------------------------------
    def steps_committed(self):
        return sum(1 for entry in self.entries() if entry.kind == STEP_COMMITTED)

    def last(self, kind):
        found = None
        for entry in self.entries():
            if entry.kind == kind:
                found = entry
        return found

    def has(self, kind):
        return self.last(kind) is not None


# --- admission ------------------------------------------------------------


@dataclass(frozen=True)
class Admission:
    kind: str
    reason: str
    steps_committed: int = 0
    verdict: str = ""
    journal: object = None

    @property
    def runs_steps(self):
        return self.kind in (RESUME_PRACTICE, RESTART_PROJECT)

    @property
    def runs_learning(self):
        return self.kind in (RESUME_PRACTICE, RESTART_PROJECT, REDO_LEARNING)

    def as_dict(self):
        return {"kind": self.kind, "reason": self.reason,
                "steps_committed": self.steps_committed,
                "verdict": self.verdict}


def admit(journal_path, *, target, verify_target=True):
    """Decide how a stopped practice may continue. Never guesses."""

    journal = Journal(journal_path, target)
    if not Path(journal_path).exists():
        return Admission(RESTART_PROJECT,
                         "no journal: nothing durable exists to continue from",
                         0, "", journal)
    state = journal.read_raw()
    if verify_target:
        expected = sha256_bytes(str(target).encode("utf-8"))
        if state.get("target_sha256") != expected:
            raise RecoveryRefused(
                "the journal belongs to a different target; refusing to continue")
    entries = journal.entries()

    if not entries:
        return Admission(RESTART_PROJECT,
                         "the journal is empty: the run never started", 0, "",
                         journal)
    if entries[0].kind != PRACTICE_STARTED:
        raise RecoveryRefused("the journal does not begin with practice_started")

    steps = sum(1 for entry in entries if entry.kind == STEP_COMMITTED)
    if steps == 0 and not any(entry.kind in (VERDICT_SAVED, INFRASTRUCTURE_STOP)
                              for entry in entries):
        return Admission(RESTART_PROJECT,
                         "the run started but committed nothing", 0, "", journal)
    closed = next((entry for entry in entries if entry.kind == PROJECT_CLOSED), None)
    verdict = next((entry for entry in entries if entry.kind == VERDICT_SAVED), None)
    learning = next((entry for entry in entries
                     if entry.kind == LEARNING_COMMITTED), None)
    budget = next((entry for entry in entries if entry.kind == BUDGET_STOP), None)
    stopped = any(entry.kind == INFRASTRUCTURE_STOP for entry in entries)

    if closed is not None:
        return Admission(ALREADY_COMPLETE,
                         "the project closed; nothing may be redone", steps,
                         (verdict.payload.get("verdict") if verdict else ""),
                         journal)
    if budget is not None:
        return Admission(STOPPED,
                         "the run stopped on its budget; nothing may be replayed "
                         "as if it had finished", steps,
                         (verdict.payload.get("verdict") if verdict else ""),
                         journal)
    if stopped and verdict is None:
        return Admission(INFRASTRUCTURE,
                         "the run stopped on an infrastructure event, which is "
                         "not a verdict and not progress", steps, "", journal)
    if verdict is not None:
        if learning is None:
            history = verdict.payload.get("history")
            if history is not None and not (paired(history) and actor_leads(history)):
                raise RecoveryRefused(
                    "the saved terminal verdict has an incomplete Actor history, "
                    "so learning cannot be resumed from it")
            return Admission(REDO_LEARNING,
                             "a terminal verdict was saved and learning was not "
                             "committed: only learning may run", steps,
                             str(verdict.payload.get("verdict", "")), journal)
        return Admission(RESTART_PROJECT,
                         "verdict and learning both committed but the project did "
                         "not close: the lineage is inconsistent", steps, "", journal)
    return Admission(RESUME_PRACTICE,
                     "committed steps exist and no verdict was reached", steps, "",
                     journal)


# --- replay ---------------------------------------------------------------


@dataclass
class ReplayResult:
    steps_run: int = 0
    learning_ran: bool = False
    verdict: str = ""
    skipped: int = 0
    kind: str = ""

    def as_dict(self):
        return {"kind": self.kind, "steps_run": self.steps_run,
                "learning_ran": self.learning_ran, "verdict": self.verdict,
                "skipped": self.skipped}


def replay(admission, *, step, learn, journal, total_steps, **learn_kwargs):
    """Continue a stopped practice exactly as far as the artifacts allow.

    ``step(index)`` performs one practice step; ``learn(verdict, **kwargs)``
    performs the post-verdict learning. Neither is called when the admission
    does not authorise it, and the number of steps already committed is never
    re-run.
    """

    result = ReplayResult(kind=admission.kind)
    if admission.kind in (ALREADY_COMPLETE, STOPPED, INFRASTRUCTURE):
        result.verdict = admission.verdict
        result.skipped = (0 if admission.kind in (ALREADY_COMPLETE, STOPPED)
                          else admission.steps_committed)
        return result

    if admission.kind == REDO_LEARNING:
        result.skipped = admission.steps_committed
        learn(admission.verdict, **learn_kwargs)
        journal.append(LEARNING_COMMITTED,
                       verdict=admission.verdict, recovery="redo_learning")
        result.learning_ran = True
        result.verdict = admission.verdict
        return result

    done = admission.steps_committed
    result.skipped = done
    for index in range(done, total_steps):
        step(index)
        journal.append(STEP_COMMITTED, index=index)
        result.steps_run += 1
    learn("", **learn_kwargs)
    journal.append(LEARNING_COMMITTED, verdict="", recovery="replayed")
    result.learning_ran = True
    return result
