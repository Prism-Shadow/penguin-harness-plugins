"""Run the text-only RSIAgent protocol with persisted train/evaluation state.

Environment observations and optional Actor VMs are operator configured. Do not
treat its model-based verifier as a benchmark score. In chat mode the API key
is read from standard input and is never written to the report or snapshot.
Penguin mode uses the host Project's existing model configuration.
"""
import argparse
import hmac
import re
import json
import os
import sys
from pathlib import Path

from explore import ExploreRun
from model_host import ChatClient
from penguin_sessions import PenguinSessions, PenguinRoleClient
from rsi import FrozenMemory


def _read_object(path):
    with open(path, "r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    return value


def _write_new(path, value):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(os.fspath(path), flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())


def _integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(name + " must be an integer from " + str(low) + " to " + str(high))
    return value


def main(argv=None, key_stream=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("train", "evaluate"))
    parser.add_argument("--host", choices=("chat", "penguin"), default="chat")
    parser.add_argument("--base-url")
    parser.add_argument("--environment-url", help="operator-provided loopback HTTP origin")
    parser.add_argument("--environment-path", action="append", default=[], help="allowed GET path prefix")
    parser.add_argument("--environment-max-requests", type=int, default=8, help="maximum observations per role call (1-64)")
    parser.add_argument("--training-judge-path", help="operator-enabled training feedback path")
    parser.add_argument("--training-task-id", action="append", default=[])
    parser.add_argument("--model", required=True)
    parser.add_argument("--vm-base-image",
                        help="prepared qcow2 base image; gives each Actor attempt a disposable VM")
    parser.add_argument("--model-timeout", type=int, default=180,
                        help="seconds per model request (30-900)")
    parser.add_argument("--penguin-executable", default="penguin")
    parser.add_argument("--penguin-read-only", action="store_true")
    parser.add_argument("--protect-private", action="store_true",
                        help="require host masks for Penguin data and evaluation snapshot")
    parser.add_argument("--private-path", action="append", default=[],
                        help="existing absolute path that the host must mask")
    parser.add_argument("--penguin-local-api-token", action="store_true",
                        help="explicitly use local admin API token for read-only sessions")
    parser.add_argument("--project")
    parser.add_argument("--workspace")
    parser.add_argument("--provider")
    parser.add_argument("--actor-agent")
    parser.add_argument("--verifier-agent")
    parser.add_argument("--curriculum-agent")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--answer-output", type=Path, help="create a JSON answer for a single evaluation task")
    parser.add_argument("--expected-digest", help="trusted training snapshot SHA-256 (required for evaluate)")
    parser.add_argument("--deep-mode", choices=("unified", "legacy"), default="unified")
    parser.add_argument("--wave-workers", type=int, default=1, help="concurrent broad-wave attempts (1-6)")
    parser.add_argument("--journal-dir", type=Path, help="existing directory for legacy deep-practice recovery")
    args = parser.parse_args(argv)
    _integer(args.wave_workers, "wave-workers", 1, 6)
    _integer(args.model_timeout, "model-timeout", 30, 900)
    if args.phase != "train" and (args.wave_workers != 1 or args.journal_dir is not None):
        raise ValueError("wave workers and journal are only valid for training")
    if args.journal_dir is not None:
        if args.deep_mode != "legacy":
            raise ValueError("--journal-dir requires --deep-mode legacy")
        if not args.journal_dir.is_dir():
            raise ValueError("--journal-dir must be an existing directory")
    if args.answer_output is not None:
        if args.phase != "evaluate":
            raise ValueError("answer output is only valid for evaluation")
        if args.answer_output.resolve() in (args.snapshot.resolve(), args.report.resolve(), args.input.resolve()):
            raise ValueError("answer output must differ from input, snapshot and report")
        if args.answer_output.exists():
            raise FileExistsError("refusing to replace an existing answer")
    if args.protect_private and not args.penguin_read_only:
        raise ValueError("--protect-private requires --penguin-read-only")
    if args.protect_private:
        home = os.environ.get("PENGUIN_HOME")
        if not home:
            raise ValueError("--protect-private requires PENGUIN_HOME")
        root = Path(home).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("PENGUIN_HOME must be a directory")
        args.private_path.append(str(root))
        if args.phase == "evaluate":
            snapshot = args.snapshot.resolve(strict=True)
            if not snapshot.is_file():
                raise ValueError("evaluation snapshot must be a file")
            if snapshot.stat().st_nlink != 1:
                raise ValueError("protected snapshot must not have hard-link aliases")
            args.private_path.append(str(snapshot))
        else:
            # The future snapshot does not exist yet. Mask its existing parent
            # so an old read-only role Session cannot later read the new file.
            snapshot_parent = args.snapshot.parent.resolve(strict=True)
            if not snapshot_parent.is_dir():
                raise ValueError("training snapshot parent must be a directory")
            args.private_path.append(str(snapshot_parent))
        args.private_path = list(dict.fromkeys(args.private_path))
    if args.penguin_read_only and args.host != "penguin":
        raise ValueError("--penguin-read-only requires --host penguin")
    if args.penguin_local_api_token and not args.penguin_read_only:
        raise ValueError("--penguin-local-api-token requires --penguin-read-only")
    if args.private_path and not args.penguin_read_only:
        raise ValueError("--private-path requires --penguin-read-only")
    if args.phase == "evaluate" and (not args.expected_digest or
            not re.fullmatch(r"[0-9a-f]{64}", args.expected_digest)):
        raise ValueError("evaluate requires a trusted lowercase SHA-256 --expected-digest")
    if args.phase == "train" and args.expected_digest is not None:
        raise ValueError("--expected-digest is only valid for evaluate")
    if args.snapshot.resolve() == args.report.resolve():
        raise ValueError("snapshot and report paths must differ")
    if args.report.exists() or (args.phase == "train" and args.snapshot.exists()):
        raise FileExistsError("refusing to replace an existing snapshot or report")
    data = _read_object(args.input)
    if args.host == "chat":
        if not args.base_url:
            raise ValueError("chat host requires --base-url")
        key = (key_stream if key_stream is not None else sys.stdin).read().strip()
        if not key:
            raise ValueError("bearer key is required on standard input")
        client = ChatClient(args.base_url, key, args.model)
        # Long reasoning turns on large observation contexts need more
        # headroom than the client default.
        client.timeout = args.model_timeout
    else:
        role_ids = {"actor": args.actor_agent, "verifier": args.verifier_agent,
                    "curriculum": args.curriculum_agent}
        if not all((args.project, args.workspace, args.provider, *role_ids.values())):
            raise ValueError("penguin host requires project, workspace, provider, and three role agents")
        session_factory = None
        if args.penguin_read_only:
            from functools import partial
            from penguin_readonly import create_readonly_session, verify_readonly_session
            auth_mode = "local-api-token" if args.penguin_local_api_token else "cookie"
            session_factory = partial(create_readonly_session,
                auth_mode=auth_mode, private_paths=args.private_path,
                require_no_network=args.protect_private)
        roles = {role: PenguinSessions([args.penguin_executable], args.project, agent,
                                       args.workspace, args.provider, args.model,
                                       **({"session_factory": session_factory,
                                           "session_guard": partial(verify_readonly_session,
                                               args.project, agent, auth_mode=auth_mode,
                                               require_masks=bool(args.private_path),
                                               required_paths=tuple(args.private_path),
                                               require_no_network=args.protect_private)}
                                          if session_factory is not None else {}))
                 for role, agent in role_ids.items()}
        for role in roles.values():
            role.timeout_seconds = args.model_timeout
        client = PenguinRoleClient(roles)
    if args.vm_base_image:
        from vm_client import VMClient
        client = VMClient(client, args.vm_base_image)
    if args.phase != "train" and (args.training_judge_path or args.training_task_id):
        raise ValueError("training judge is unavailable during evaluation")
    if args.environment_url or args.environment_path or args.training_judge_path or args.training_task_id:
        if not args.environment_url or not args.environment_path:
            raise ValueError("environment URL and at least one allowed path are required")
        from environment_client import EnvironmentClient
        client = EnvironmentClient(client, args.environment_url, args.environment_path,
                                   max_requests=args.environment_max_requests,
                                   training_judge_path=args.training_judge_path,
                                   training_task_ids=args.training_task_id,
                                   training_catalog=data.get("training_tasks"))
    if args.phase == "train":
        domain, target = data.get("domain"), data.get("target")
        if not all(isinstance(x, str) and x.strip() for x in (domain, target)):
            raise ValueError("training requires nonempty domain and target")
        waves = _integer(data.get("waves", 1), "waves", 1, 10)
        wave_size = _integer(data.get("wave_size", 2), "wave_size", 1, 6)
        practices = _integer(data.get("max_practices", 2), "max_practices", 0, 10)
        exclusions = data.get("held_out_exclusions", [])
        if not isinstance(exclusions, list) or not all(isinstance(x, str) for x in exclusions):
            raise ValueError("held_out_exclusions must be a list of strings")
        training_tasks = data.get("training_tasks")
        if training_tasks is not None:
            if (not isinstance(training_tasks, dict) or not training_tasks or
                    not all(isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", key)
                            and isinstance(value, str) and value.strip()
                            for key, value in training_tasks.items())):
                raise ValueError("training_tasks must map valid IDs to nonempty tasks")
            if not any(target == "Training task ID: {}\n{}".format(key, value)
                       for key, value in training_tasks.items()):
                raise ValueError("target must exactly match a task in the training catalog")
        run = ExploreRun(client, max_practices=practices, held_out=exclusions,
                         wave_workers=args.wave_workers, journal_dir=args.journal_dir)
        run._reject_held_out(target)
        if training_tasks is not None:
            for task in training_tasks.values():
                run._reject_held_out(task)
        for _ in range(waves):
            run.broad(domain, wave_size=wave_size, training_tasks=training_tasks)
            if run.broad_status in ("SATURATED", "STALLED"):
                break
        if run.broad_status == "WAVE_COMPLETED":
            # The configured cap was reached; do not call it saturation.
            run.broad_status = "WAVE_LIMIT_REACHED"
        if run.broad_status == "STALLED":
            status = "BROAD_STALLED"
        else:
            status = (run.deep_unified(target) if args.deep_mode == "unified"
                      else run.deep(target))
        frozen = run.freeze()
        frozen.save(args.snapshot)
        report = run.report()
        report.update({"phase": "train", "model": args.model, "target_status": status,
                       "snapshot_digest": frozen.sha256, "waves": waves,
                       "waves_completed": run.waves_completed, "broad_status": run.broad_status,
                       "wave_size": wave_size, "max_practices": practices,
                       "deep_mode": args.deep_mode, "wave_workers": args.wave_workers,
                       "journal_enabled": args.journal_dir is not None, "host": args.host,
                       "host_reported_read_only": args.penguin_read_only,
                       "required_masked_paths": len(args.private_path)})
    else:
        tasks = data.get("tasks")
        if not isinstance(tasks, list) or not tasks or not all(isinstance(x, str) and x.strip() for x in tasks):
            raise ValueError("evaluation requires a nonempty list of task strings")
        if args.answer_output is not None and len(tasks) != 1:
            raise ValueError("answer output requires exactly one evaluation task")
        frozen = FrozenMemory.load(args.snapshot)
        if not hmac.compare_digest(frozen.sha256, args.expected_digest):
            raise ValueError("snapshot differs from the trusted training digest")
        run = ExploreRun(client)
        try:
            results = run.evaluate(tasks, frozen)
        finally:
            # Check the on-disk artifact even when model execution fails. This
            # detects replacement, but does not prevent writes or ABA changes.
            try:
                after = FrozenMemory.load(args.snapshot)
            except (OSError, ValueError) as exc:
                raise RuntimeError("snapshot changed while evaluation was running") from exc
            if not hmac.compare_digest(after.sha256, args.expected_digest):
                raise RuntimeError("snapshot changed while evaluation was running")
        report = {"phase": "evaluate", "model": args.model, "host": args.host,
                  "host_reported_read_only": args.penguin_read_only,
                  "required_masked_paths": len(args.private_path),
                  "snapshot_digest": frozen.sha256, "results": results,
                  "role_calls": dict(client.calls),
                  "limitations": ["text-only model verifier; not benchmark grading",
                                  "no independently isolated environment"]}
    if args.answer_output is not None:
        answer = report["results"][0]["answer"]
        if not isinstance(answer, dict):
            raise ValueError("answer output requires an answer JSON object")
        _write_new(args.answer_output, answer)
    _write_new(args.report, report)
    summary = {"phase": args.phase, "snapshot_digest": frozen.sha256,
               "report": str(args.report)}
    if args.phase == "train":
        # An artifact can exist even when exploration stalls or hits a budget.
        # Expose these states to MCP callers rather than implying success.
        summary.update(target_status=report["target_status"],
                       broad_status=report["broad_status"],
                       waves_completed=report["waves_completed"],
                       lessons=report["lessons"])
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        raise SystemExit(1)
