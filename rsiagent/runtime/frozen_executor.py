"""Run a command with a frozen snapshot mounted read-only in a container.

This protects the bind mount from writes made inside this container. It does
not protect against writers on the host, authenticate the expected digest, or
make the normal PenguinHarness CLI execute inside this boundary.
"""
import hmac
from pathlib import Path
import subprocess
from docker_verifier import _BOOTSTRAP
from rsi import FrozenMemory


def run_with_frozen_snapshot(snapshot_path, expected_digest, command, *,
                             image="python:3.11-slim", timeout=30):
    path = Path(snapshot_path).resolve(strict=True)
    if not path.is_file():
        raise ValueError("snapshot must be a regular file")
    if not isinstance(expected_digest, str) or not expected_digest:
        raise ValueError("trusted digest is required")
    if not command or not all(isinstance(x, str) and x for x in command):
        raise ValueError("command must be a nonempty argument list")
    before = FrozenMemory.load(path)
    if not hmac.compare_digest(before.sha256, expected_digest):
        raise ValueError("snapshot differs from trusted digest")
    stat = path.stat()
    args = ["docker", "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--pids-limit", "64", "--memory", "256m",
            "--cpus", "1", "--user", str(stat.st_uid) + ":" + str(stat.st_gid),
            "--mount", "type=bind,src=" + str(path) +
            ",dst=/frozen/memory.json,readonly", "-i", image,
            "python", "-I", "-c", _BOOTSTRAP, *command]
    try:
        return subprocess.run(args, text=True, capture_output=True,
                              check=True, timeout=timeout)
    finally:
        # Even a failed or timed-out process must not skip the integrity check.
        try:
            after = FrozenMemory.load(path)
        except (OSError, ValueError) as exc:
            raise RuntimeError("snapshot changed while execution was running") from exc
        if not hmac.compare_digest(after.sha256, expected_digest):
            raise RuntimeError("snapshot changed while execution was running")
