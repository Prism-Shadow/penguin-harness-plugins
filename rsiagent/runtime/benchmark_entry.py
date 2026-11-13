"""Run one public-workspace benchmark task through Penguin role sessions.

This entry point expects an already running, configured Penguin server with
three distinct agents and a verified sandbox. It does not install or configure
those prerequisites, and does not make host-side snapshot writers impossible.
"""
import argparse
import json
from pathlib import Path
import tempfile
import cli
from task_input import build_task


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("project", "workspace", "provider", "model", "actor-agent",
                 "verifier-agent", "curriculum-agent", "snapshot", "expected-digest",
                 "report", "answer-output", "environment-url"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--environment-path", action="append", required=True)
    parser.add_argument("--environment-max-requests", type=int, default=8)
    parser.add_argument("--penguin-executable", default="penguin")
    parser.add_argument("--penguin-local-api-token", action="store_true")
    parser.add_argument("--private-path", action="append", default=[])
    args = parser.parse_args(argv)
    # Never derive a training input or inspect grader/reference files here.
    task = build_task(args.workspace)
    with tempfile.TemporaryDirectory(prefix="rsiagent-benchmark-") as temporary:
        source = Path(temporary) / "input.json"
        source.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
        command = ["evaluate", "--host", "penguin", "--penguin-read-only", "--protect-private",
                   "--project", args.project, "--workspace", args.workspace,
                   "--provider", args.provider, "--model", args.model,
                   "--actor-agent", args.actor_agent, "--verifier-agent", args.verifier_agent,
                   "--curriculum-agent", args.curriculum_agent, "--input", str(source),
                   "--snapshot", args.snapshot, "--expected-digest", args.expected_digest,
                   "--report", args.report, "--answer-output", args.answer_output,
                   "--environment-url", args.environment_url,
                   "--environment-max-requests", str(args.environment_max_requests),
                   "--penguin-executable", args.penguin_executable]
        for path in args.environment_path:
            command.extend(["--environment-path", path])
        for path in args.private_path:
            command.extend(["--private-path", path])
        if args.penguin_local_api_token:
            command.append("--penguin-local-api-token")
        return cli.main(command)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        print(type(exc).__name__ + ": " + str(exc), file=__import__("sys").stderr)
        raise SystemExit(1)
