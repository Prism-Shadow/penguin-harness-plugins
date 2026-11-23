"""Stdio MCP bridge to the RSIAgent PenguinHarness text protocol.

Actor VMs and read-only Sessions are operator configured; they do not establish
full environment isolation or benchmark grading. Uses the host model configuration.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

_REQUIRED = ("project", "workspace", "provider", "model", "actor_agent",
             "verifier_agent", "curriculum_agent", "input", "snapshot", "report")
_PROPERTIES = {key: {"type": "string", "minLength": 1} for key in _REQUIRED}
_PROPERTIES["expected_digest"] = {"type": "string", "minLength": 64, "maxLength": 64}
_PROPERTIES["answer_output"] = {"type": "string", "minLength": 1, "description": "Optional new JSON answer file for a single evaluation task."}
_PROPERTIES["wave_workers"] = {"type": "integer", "minimum": 1, "maximum": 6}
_PROPERTIES["journal_dir"] = {"type": "string", "minLength": 1}
_PROPERTIES["deep_mode"] = {"type": "string", "enum": ["unified", "legacy"]}
_PROPERTIES["read_only"] = {"type": "boolean", "description": "Request host-enforced read-only Sessions (default true); false explicitly selects the unisolated text pilot."}
_PROPERTIES["private_paths"] = {"type": "array", "items": {"type": "string", "minLength": 1},
                                 "description": "Existing absolute paths that must be masked in host configuration."}


def tools():
    base = {"type": "object", "properties": _PROPERTIES, "additionalProperties": False}
    return [
        {"name": "rsi_train", "description": "Run RSIAgent text training via three existing PenguinHarness Agents. Does not isolate files or run GDPevo.",
         "inputSchema": {**base, "required": list(_REQUIRED)}},
        {"name": "rsi_evaluate", "description": "Evaluate text tasks against a saved snapshot and trusted digest. Does not enforce OS write protection.",
         "inputSchema": {**base, "required": list(_REQUIRED) + ["expected_digest"]}},
    ]


def call_tool(name, arguments, runner=subprocess.run):
    if name not in ("rsi_train", "rsi_evaluate"):
        raise ValueError("unknown tool")
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    required = list(_REQUIRED) + (["expected_digest"] if name == "rsi_evaluate" else [])
    if set(arguments) - set(_PROPERTIES) or any(
            not isinstance(arguments.get(k), str) or not arguments[k].strip()
            for k in required):
        raise ValueError("missing or invalid tool arguments")
    if "answer_output" in arguments and (name != "rsi_evaluate" or
            not isinstance(arguments["answer_output"], str) or not arguments["answer_output"].strip()):
        raise ValueError("answer_output requires an evaluation and a nonempty path")
    if "read_only" in arguments and type(arguments["read_only"]) is not bool:
        raise ValueError("read_only must be a boolean")
    if any(k in arguments for k in ("wave_workers", "journal_dir", "deep_mode")) and name != "rsi_train":
        raise ValueError("training options require rsi_train")
    if "wave_workers" in arguments and (type(arguments["wave_workers"]) is not int or
                                        not 1 <= arguments["wave_workers"] <= 6):
        raise ValueError("wave_workers must be an integer from 1 to 6")
    if "deep_mode" in arguments and arguments["deep_mode"] not in ("unified", "legacy"):
        raise ValueError("invalid deep_mode")
    if "journal_dir" in arguments and (not isinstance(arguments["journal_dir"], str) or
                                        not arguments["journal_dir"].strip() or
                                        arguments.get("deep_mode", "unified") != "legacy"):
        raise ValueError("journal_dir requires a path and legacy deep_mode")
    private_paths = arguments.get("private_paths", [])
    if not isinstance(private_paths, list) or not all(
            isinstance(path, str) and path.strip() for path in private_paths):
        raise ValueError("private_paths must be a list of nonempty strings")
    if private_paths and not arguments.get("read_only", True):
        raise ValueError("private_paths require read_only")
    cli = str(Path(__file__).with_name("cli.py"))
    command = [sys.executable, cli, "train" if name == "rsi_train" else "evaluate",
               "--host", "penguin"]
    for key in _REQUIRED:
        command.extend(["--" + key.replace("_", "-"), arguments[key]])
    for option in ("wave_workers", "journal_dir", "deep_mode"):
        if option in arguments:
            command.extend(["--" + option.replace("_", "-"), str(arguments[option])])
    # Request budgets and VM selection are trusted operator configuration.
    model_timeout = os.environ.get("RSI_MODEL_TIMEOUT")
    if model_timeout is not None:
        try:
            model_timeout_value = int(model_timeout)
        except ValueError as exc:
            raise ValueError("operator model timeout must be an integer") from exc
        if not 30 <= model_timeout_value <= 900:
            raise ValueError("operator model timeout must be between 30 and 900 seconds")
        command.extend(["--model-timeout", str(model_timeout_value)])
    vm_image = os.environ.get("RSI_VM_BASE_IMAGE")
    if vm_image:
        command.extend(["--vm-base-image", vm_image])
    if name == "rsi_evaluate":
        command.extend(["--expected-digest", arguments["expected_digest"]])
        if "answer_output" in arguments:
            command.extend(["--answer-output", arguments["answer_output"]])
    if arguments.get("read_only", True):
        # A read-only mode alone does not hide the Actor's stored state.
        # Require host masks for its data root and evaluation snapshot as well.
        command.extend(["--penguin-read-only", "--protect-private"])
        if os.environ.get("RSI_PENGUIN_LOCAL_API_TOKEN") == "1":
            command.append("--penguin-local-api-token")
    for path in private_paths:
        command.extend(["--private-path", path])
    # Environment capabilities are operator configuration, not MCP arguments.
    environment_url = os.environ.get("RSI_ENVIRONMENT_URL")
    environment_paths = os.environ.get("RSI_ENVIRONMENT_PATHS")
    if environment_url or environment_paths:
        if not environment_url or not environment_paths:
            raise ValueError("operator must configure environment URL and paths together")
        try:
            paths = json.loads(environment_paths)
        except json.JSONDecodeError as exc:
            raise ValueError("operator environment paths must be a JSON array") from exc
        if not isinstance(paths, list) or not paths or not all(
                isinstance(path, str) and path.strip() for path in paths):
            raise ValueError("operator environment paths must be a nonempty string array")
        request_limit = os.environ.get("RSI_ENVIRONMENT_MAX_REQUESTS")
        if request_limit is not None:
            try:
                limit = int(request_limit)
            except ValueError as exc:
                raise ValueError("operator request budget must be an integer") from exc
            if not 1 <= limit <= 64:
                raise ValueError("operator request budget must be between 1 and 64")
            command.extend(["--environment-max-requests", str(limit)])
        command.extend(["--environment-url", environment_url])
        for path in paths:
            command.extend(["--environment-path", path])
    # Training feedback is deliberately omitted from evaluation invocations.
    judge_path = os.environ.get("RSI_TRAINING_JUDGE_PATH")
    judge_ids = os.environ.get("RSI_TRAINING_TASK_IDS")
    if name == "rsi_train" and (judge_path or judge_ids):
        if not environment_url or not judge_path or not judge_ids:
            raise ValueError("operator must configure environment and training judge together")
        try:
            ids = json.loads(judge_ids)
        except json.JSONDecodeError as exc:
            raise ValueError("operator training task IDs must be a JSON array") from exc
        if not isinstance(ids, list) or not ids or not all(
                isinstance(task, str) and task.strip() for task in ids):
            raise ValueError("operator training task IDs must be a nonempty string array")
        command.extend(["--training-judge-path", judge_path])
        for task in ids:
            command.extend(["--training-task-id", task])
    # Executable selection is operator configuration, not model-supplied input.
    executable = os.environ.get("RSI_PENGUIN_EXECUTABLE")
    if executable:
        command.extend(["--penguin-executable", executable])
    try:
        timeout = int(os.environ.get("RSI_EXECUTION_TIMEOUT", "7200"))
    except ValueError as exc:
        raise ValueError("operator execution timeout must be an integer") from exc
    if not 1 <= timeout <= 14400:
        raise ValueError("operator execution timeout must be between 1 and 14400 seconds")
    try:
        result = runner(command, input="", capture_output=True, text=True,
                        timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("RSIAgent execution did not complete") from exc
    if result.returncode:
        raise RuntimeError("RSIAgent exited with code " + str(result.returncode) +
                           ": " + result.stderr[-1000:])
    try:
        return json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("RSIAgent returned invalid JSON") from exc


def dispatch(request):
    method = request.get("method")
    if method == "initialize":
        version = request.get("params", {}).get("protocolVersion", "2024-11-05")
        return {"protocolVersion": version, "capabilities": {"tools": {}},
                "serverInfo": {"name": "rsiagent", "version": "0.1.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": tools()}
    if method == "tools/call":
        params = request.get("params", {})
        try:
            output = call_tool(params.get("name"), params.get("arguments", {}))
            return {"content": [{"type": "text", "text": json.dumps(output)}], "isError": False}
        except (ValueError, RuntimeError) as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    raise ValueError("method not found")


def main():
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or "method" not in request:
                raise ValueError("invalid request")
            if "id" not in request:
                continue
            try:
                response = {"jsonrpc": "2.0", "id": request["id"], "result": dispatch(request)}
            except ValueError as exc:
                response = {"jsonrpc": "2.0", "id": request["id"],
                            "error": {"code": -32601, "message": str(exc)}}
        except (ValueError, json.JSONDecodeError):
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "invalid request"}}
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
