"""Execute a verifier against a read-only candidate in a restricted container.

This boundary deliberately does not mount actor memory or credentials. It is not
a VM and does not roll back side effects outside the container.
"""
from pathlib import Path
import subprocess

# Apply the restriction after AppArmor transition and before verifier exec.
_BOOTSTRAP = (
    "import ctypes,os,sys; "
    "libc=ctypes.CDLL(None,use_errno=True); "
    "r=libc.prctl(38,1,0,0,0); "
    "r==0 or sys.exit(125); "
    "os.execvp(sys.argv[1],sys.argv[1:])"
)


def verify_in_container(candidate_dir, command, input_data="", *,
                        image="python:3.11-slim", timeout=30):
    candidate = None if candidate_dir is None else Path(candidate_dir).resolve(strict=True)
    if candidate is not None and not candidate.is_dir():
        raise ValueError("candidate must be a directory")
    if not command or not all(isinstance(x, str) and x for x in command):
        raise ValueError("command must be a nonempty argument list")
    # No host path is mounted unless explicitly requested by the caller.
    args = ["docker", "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL",
            "--pids-limit", "64", "--memory", "256m", "--cpus", "1",
            "--user", "65534:65534", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m"]
    if candidate is not None:
        args.extend(["--mount", "type=bind,src=" + str(candidate) +
                     ",dst=/candidate,readonly", "--workdir", "/candidate"])
    args.extend(["-i", image, "python", "-I", "-c", _BOOTSTRAP, *command])
    return subprocess.run(args, input=input_data, text=True, capture_output=True,
                          check=True, timeout=timeout)
