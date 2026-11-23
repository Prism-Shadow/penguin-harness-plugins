"""Resource guard for branch VMs, ported from upstream ``env/controller_oom.py``.

Upstream kills a runaway branch before the host OOM killer takes the whole run
down, and treats that kill as an infrastructure event rather than a task
verdict. This module does the same with what a normal user can read:

- ``VmSampler`` reads a process's resident memory and CPU time from ``/proc``;
- ``ResourceGuard`` polls sampled branches, terminates one that exceeds its
  memory ceiling or runs past its wall-clock budget, and records the event;
- a guard kill is reported as ``infrastructure``, never as a verdict.
"""
import os
import time
from dataclasses import dataclass, field


class ResourceStop(RuntimeError):
    """A branch was stopped for exceeding its budget (an infrastructure event)."""


@dataclass
class Sample:
    pid: int
    rss_bytes: int
    cpu_seconds: float
    wall_seconds: float


def sample_process(pid, started_at=None):
    """Read RSS and CPU time for a pid from /proc (Linux)."""
    with open("/proc/{}/status".format(pid), "r", encoding="utf-8") as handle:
        status = handle.read()
    rss_kb = 0
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            rss_kb = int(line.split()[1])
            break
    cpu_seconds = 0.0
    try:
        with open("/proc/{}/stat".format(pid), "r", encoding="utf-8") as handle:
            fields = handle.read().split()
        ticks = os.sysconf("SC_CLK_TCK")
        cpu_seconds = (int(fields[13]) + int(fields[14])) / ticks
    except (OSError, IndexError, ValueError):
        pass
    wall = time.time() - started_at if started_at else 0.0
    return Sample(pid, rss_kb * 1024, cpu_seconds, wall)


@dataclass
class ResourceGuard:
    """Ceilings for one branch; ``ok`` decides without side effects."""

    memory_limit_bytes: int = 4 * 1024 ** 3
    wall_limit_seconds: float = 1800.0
    cpu_limit_seconds: float = 1500.0
    events: list = field(default_factory=list)

    def check(self, sample):
        """Return None when the sample is inside budget, else the reason."""
        if sample.rss_bytes > self.memory_limit_bytes:
            return "memory {} MiB over {} MiB".format(
                sample.rss_bytes // (1024 ** 2), self.memory_limit_bytes // (1024 ** 2))
        if sample.wall_seconds > self.wall_limit_seconds:
            return "wall clock {:.0f}s over {:.0f}s".format(
                sample.wall_seconds, self.wall_limit_seconds)
        if sample.cpu_seconds > self.cpu_limit_seconds:
            return "cpu {:.0f}s over {:.0f}s".format(
                sample.cpu_seconds, self.cpu_limit_seconds)
        return None

    def enforce(self, sample, terminate):
        """Terminate the branch when over budget; return the recorded event."""
        reason = self.check(sample)
        if reason is None:
            return None
        try:
            terminate(sample.pid)
        except Exception as exc:            # terminating twice is not a failure
            reason = reason + " (terminate raised {})".format(repr(exc)[:100])
        event = {"kind": "resource_stop", "pid": sample.pid,
                 "reason": reason, "infrastructure": True}
        self.events.append(event)
        raise ResourceStop(reason)


def is_infrastructure_stop(exc):
    """A guard kill is infrastructure, not a task verdict."""
    return isinstance(exc, ResourceStop)
