"""Build one text task from a benchmark workspace's public input files.

Only input/prompt.txt and input/payloads are read. Reference answers and grader
files are intentionally outside this interface. Output is an evaluate input JSON.
"""
import json
import re
from pathlib import Path
import sys

MAX_TOTAL_BYTES = 1048576
MAX_FILES = 100


def build_task(workspace):
    root = Path(workspace).resolve(strict=True)
    input_dir = root / "input"
    prompt = input_dir / "prompt.txt"
    payload_dir = input_dir / "payloads"
    if input_dir.is_symlink() or prompt.is_symlink() or payload_dir.is_symlink():
        raise ValueError("input paths must not be symlinks")
    if not prompt.is_file() or not payload_dir.is_dir():
        raise ValueError("workspace must contain input/prompt.txt and input/payloads")
    files = [prompt]
    for path in sorted(payload_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError("payload paths must not be symlinks")
        if path.is_file():
            files.append(path)
    if len(files) > MAX_FILES:
        raise ValueError("too many input files")
    size = 0
    sections = []
    for path in files:
        raw = path.read_bytes()
        size += len(raw)
        if size > MAX_TOTAL_BYTES:
            raise ValueError("input files exceed size limit")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("input files must be UTF-8 text") from exc
        sections.append("--- " + path.relative_to(root).as_posix() + " ---\n" + text)
    return {"tasks": ["\n\n".join(sections)]}


def build_training(workspace, target_id, domain):
    """Build a catalog from public train_* task inputs only.

    A target must be selected explicitly. No test directory, reference answer,
    grading script or evaluation task is read by this function.
    """
    root = Path(workspace).resolve(strict=True)
    tasks_root = root / "train_tasks"
    if tasks_root.is_symlink() or not tasks_root.is_dir():
        raise ValueError("workspace must contain a non-symlink train_tasks directory")
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("training domain must be nonempty")
    catalog = {}
    for entry in sorted(tasks_root.iterdir()):
        if not re.fullmatch(r"train_[0-9]+", entry.name):
            continue
        if entry.is_symlink() or not entry.is_dir():
            raise ValueError("training task must be a non-symlink directory")
        catalog[entry.name] = build_task(entry)["tasks"][0]
    if not catalog or target_id not in catalog:
        raise ValueError("target must identify a training task")
    return {"domain": domain, "training_tasks": catalog,
            "target": "Training task ID: {}\n{}".format(target_id, catalog[target_id]),
            "wave_size": min(6, len(catalog))}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 1:
        data = build_task(args[0])
    elif len(args) == 4 and args[0] == "--training":
        data = build_training(args[1], args[2], args[3])
    else:
        raise ValueError("usage: task_input.py WORKSPACE | --training WORKSPACE TARGET_ID DOMAIN")
    print(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
