"""Optional caller-supplied evidence verifier for the protocol.

A ground-truth program supplied by the caller runs inside the restricted
container (docker_verifier.py): it receives the task string and the
candidate's answer over standard input and must print exactly PASS or FAIL. The verdict is computed by the caller-supplied program, not by the
Actor model; it is only as reliable as that program and its inputs. the Actor's memory and reasoning never enter the
container. The program itself is task-family-specific and supplied by
the experiment (RFC-0003: the algorithm stays dataset-decoupled).
"""
import json

from docker_verifier import verify_in_container

_PROBE_WRAPPER = (
    "import json,sys\n"
    "_input = json.load(sys.stdin)\n"
    "task, answer = _input['task'], _input['answer']\n"
)


def container_verifier(program_source, timeout=30, candidate_dir=None):
    """Build verify(task, public) whose verdict program_source computes.

    program_source is Python source that runs with -I (isolated) inside
    the restricted container, with `task` and `answer` already bound to
    the task string and the candidate's answer; it must print exactly
    PASS or FAIL. The values are delivered on standard input. Only the container's own Python is available.
    """
    probe = _PROBE_WRAPPER + program_source

    def verify(task, public):
        result = verify_in_container(
            candidate_dir, ["python", "-I", "-c", probe],
            input_data=json.dumps({"task": str(task), "answer": str(public["answer"])}),
            timeout=timeout)
        verdict = result.stdout.strip()
        if verdict not in ("PASS", "FAIL"):
            raise RuntimeError(
                "container verifier returned an unusable verdict: " + repr(verdict[:100]))
        return verdict

    verify.verifier_mode = ("container evidence; no host paths mounted" if candidate_dir is None
                            else "container evidence; caller-selected candidate mounted read-only")
    return verify


class EvidenceHost:
    """Model Actor and Curriculum; container-evidence Verifier.

    Wraps a ModelHost so the Protocol's verify() uses the caller's program
    instead of the Actor model. The program must itself be validated. Actor memory still reaches the Actor
    (as hints); the Verifier path never sees it.
    """

    def __init__(self, model_host, verify_fn):
        self.model_host = model_host
        self.verify_fn = verify_fn
        self.verifier_mode = getattr(verify_fn, "verifier_mode", "caller-supplied evidence")

    def run_actor(self, task, memory):
        return self.model_host.run_actor(task, memory)

    def verify(self, task, public):
        return self.verify_fn(task, public)

    def consolidate(self, memory, task, candidate, verdict):
        return self.model_host.consolidate(memory, task, candidate, verdict)

    def curriculum_review(self, task, verdict, memory):
        return self.model_host.curriculum_review(task, verdict, memory)
