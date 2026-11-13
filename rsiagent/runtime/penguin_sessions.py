"""PenguinHarness CLI role sessions, without filesystem isolation.

This adapter launches separate persistent host Sessions. It does not prevent a
Session from accessing shared files and is not an independent verifier sandbox.
"""
import json
import subprocess


class PenguinSessionError(RuntimeError):
    pass


class PenguinSessions:
    def __init__(self, executable, project, agent, workspace, provider, model,
                 timeout_seconds=90, runner=subprocess.run, session_factory=None,
                 session_guard=None):
        if not executable or not all(isinstance(x, str) and x for x in executable):
            raise ValueError("executable must be a nonempty argument list")
        if not all(isinstance(x, str) and x for x in
                   (project, agent, workspace, provider, model)):
            raise ValueError("project, agent, workspace, provider and model are required")
        if type(timeout_seconds) is not int or timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        self.executable = list(executable)
        self.project, self.agent, self.workspace = project, agent, workspace
        self.provider, self.model, self.timeout_seconds = provider, model, timeout_seconds
        self.runner = runner
        self.session_factory = session_factory
        self.session_guard = session_guard

    def turn(self, message, session_id=None, approve="deny-all"):
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be nonempty")
        if approve not in ("deny-all", "read-only", "allow-all", "always-ask"):
            raise ValueError("invalid approval mode")
        args = self.executable + ["run"]
        if session_id is None and self.session_factory is not None:
            if approve != "deny-all":
                raise ValueError("read-only sessions require deny-all approval")
            session_id = self.session_factory(self.project, self.agent, self.workspace,
                                              self.provider, self.model)
        if session_id is None:
            args += ["--project-id", self.project, "--agent-id", self.agent,
                     "--workspace", self.workspace, "--provider", self.provider,
                     "--model-id", self.model, "--approve", approve]
        elif isinstance(session_id, str) and session_id.strip():
            # The host rejects workspace/model/provider when resuming.
            args += ["--session", session_id]
        else:
            raise ValueError("session_id must be a nonempty string")
        args += ["--timeout", str(self.timeout_seconds) + "s",
                 "--json", "--message", message]
        if self.session_guard is not None:
            if session_id is None:
                raise PenguinSessionError("guarded session must be created before execution")
            self.session_guard(session_id)
        try:
            result = self.runner(args, capture_output=True, text=True,
                                 timeout=self.timeout_seconds + 20, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PenguinSessionError("Penguin session execution failed") from exc
        finally:
            if self.session_guard is not None:
                self.session_guard(session_id)
        if result.returncode:
            raise PenguinSessionError("Penguin session exited with code " + str(result.returncode))
        try:
            output = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise PenguinSessionError("Penguin session returned invalid JSON") from exc
        if (not isinstance(output, dict) or output.get("status") != "completed" or
                not isinstance(output.get("sessionId"), str) or
                not output["sessionId"].strip() or
                not isinstance(output.get("text"), str) or not output["text"].strip()):
            raise PenguinSessionError("Penguin session did not complete with a usable result")
        if session_id is not None and output["sessionId"] != session_id:
            raise PenguinSessionError("Penguin resumed a different session")
        return output["sessionId"], output["text"]


class PenguinRoleClient:
    """ChatClient-compatible role calls backed by separate host Sessions.

    Each role must use its own independently configured agent. Distinct agent
    IDs and sessions are not a filesystem or process security boundary. The
    operator must separately enforce verifier isolation and snapshot read-only
    access before claiming those properties.
    """
    def __init__(self, roles, actor_approve="deny-all", log=None):
        required = {"actor", "verifier", "curriculum"}
        if set(roles) != required:
            raise ValueError("actor, verifier and curriculum sessions are required")
        if len({roles[k].agent for k in required}) != len(required):
            raise ValueError("roles must use distinct agent IDs")
        if actor_approve not in ("deny-all", "read-only", "allow-all", "always-ask"):
            raise ValueError("invalid actor approval mode")
        self.roles = dict(roles)
        self.actor_approve = actor_approve
        self.calls = {}
        self.log = log
        self._sessions = {}

    def release_history(self, history):
        """Drop a coordinator-side session reference without deleting host data."""
        if history is None:
            return
        key = id(history)
        entry = self._sessions.get(key)
        if entry is not None and entry[1] is history:
            del self._sessions[key]

    def complete(self, role, system, user, max_tokens=512, temperature=0.0, history=None):
        if role not in self.roles:
            raise ValueError("unknown role")
        # Hold the list strongly so Python cannot recycle its id into another
        # session. State is owned by the host, not replayed from this list.
        key = id(history) if history is not None else None
        if key is not None and key in self._sessions:
            old_role, old_history, sid = self._sessions[key]
            if old_role != role or old_history is not history:
                raise PenguinSessionError("role history reused across roles")
        else:
            sid = None
        prompt = system + "\n\n" + user
        approval = self.actor_approve if role == "actor" else "deny-all"
        session_id, text = self.roles[role].turn(prompt, session_id=sid, approve=approval)
        if history is not None:
            self._sessions[key] = (role, history, session_id)
            history.extend([{"role": "user", "content": user},
                            {"role": "assistant", "content": text}])
        self.calls[role] = self.calls.get(role, 0) + 1
        if self.log is not None:
            self.log({"role": role, "system": system, "user": user, "reply": text})
        return text
