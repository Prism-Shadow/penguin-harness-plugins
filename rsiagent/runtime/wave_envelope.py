"""Wave handoff envelope, ported from upstream ``explore/phase1_wave.py``.

The Curriculum's reply crosses the boundary as a *lossless transport envelope*,
not as prose the harness tries to interpret. Upstream is explicit that the
parser reads the envelope and never the strategy:

    {"decision": "WAVE" | "SATURATED" | "STALLED",
     "rationale": "<why>",
     "projects": [{"project_id": "...", "instruction": "..."}]}

Rules kept:
- an unknown decision, a missing/empty rationale, or a non-list projects field
  makes the whole handoff unusable (return None) rather than half-parsed;
- WAVE must carry at least one project; SATURATED and STALLED must carry none
  (a saturation claim with work attached is contradictory);
- project ids are path-safe slugs, so a project can name a workspace later.
"""
import json
import re
from dataclasses import dataclass

_DECISIONS = ("WAVE", "SATURATED", "STALLED")
_PROJECT_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")


@dataclass(frozen=True)
class WaveProject:
    project_id: str
    instruction: str


@dataclass(frozen=True)
class WaveDecision:
    decision: str
    rationale: str
    projects: tuple
    raw: str = ""


def parse_wave_handoff(text):
    """Parse the Curriculum's handoff envelope; None when it is unusable."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        blocks = re.findall(r"```(?:[A-Za-z0-9_.+-]+)?[ \t]*\n(.*?)```", stripped, re.S)
        if len(blocks) == 1:
            stripped = blocks[0].strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        first, last = stripped.find("{"), stripped.rfind("}")
        if first < 0 or last <= first:
            return None
        try:
            value = json.loads(stripped[first:last + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None

    decision = value.get("decision")
    rationale = value.get("rationale")
    projects = value.get("projects")
    if decision not in _DECISIONS:
        return None
    if not isinstance(rationale, str) or not rationale.strip():
        return None
    if not isinstance(projects, list):
        return None

    parsed = []
    for item in projects:
        if not isinstance(item, dict):
            return None
        project_id = item.get("project_id")
        instruction = item.get("instruction")
        if (not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id)
                or not isinstance(instruction, str) or not instruction.strip()):
            return None
        parsed.append(WaveProject(project_id, instruction.strip()))

    if decision == "WAVE" and not parsed:
        return None
    if decision in ("SATURATED", "STALLED") and parsed:
        return None
    return WaveDecision(decision, rationale.strip(), tuple(parsed), stripped)


def should_continue(decision, waves_done, wave_limit):
    """Whether another wave may start, and why not when it may not."""
    if decision is None:
        return False, "unusable handoff"
    if decision.decision == "SATURATED":
        return False, "SATURATED"
    if decision.decision == "STALLED":
        return False, "STALLED"
    if waves_done >= wave_limit:
        return False, "WAVE_LIMIT_REACHED"
    return True, ""
