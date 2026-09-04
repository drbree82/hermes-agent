#!/usr/bin/env python3
"""Run the reusable Hermes reasoning task catalog through both backends."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--task-file",
        type=Path,
        default=root / "benchmarks" / "reasoning_tasks.json",
    )
    parser.add_argument("--task-id", action="append", help="Run only this task (repeatable)")
    parser.add_argument("--model")
    parser.add_argument("--provider")
    parser.add_argument("--toolsets")
    parser.add_argument("--timeout", type=float)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reasoning_benchmark_results"),
    )
    args = parser.parse_args()

    task_file = args.task_file.expanduser().resolve()
    catalog = json.loads(task_file.read_text(encoding="utf-8"))
    tasks = catalog.get("tasks", []) if isinstance(catalog, dict) else []
    selected_ids = set(args.task_id or [])
    if selected_ids:
        tasks = [task for task in tasks if task.get("id") in selected_ids]
    if not tasks:
        parser.error("no benchmark tasks selected")

    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    compare_script = root / "scripts" / "compare_reasoning_backends.py"
    failures = 0
    index: list[dict[str, object]] = []
    for task in tasks:
        task_id = str(task.get("id") or "")
        output_path = output_dir / f"{task_id}.json"
        command = [
            sys.executable,
            str(compare_script),
            "--task-file",
            str(task_file),
            "--task-id",
            task_id,
            "--output",
            str(output_path),
        ]
        if args.model:
            command.extend(["--model", args.model])
        if args.provider:
            command.extend(["--provider", args.provider])
        if args.toolsets:
            command.extend(["--toolsets", args.toolsets])
        if args.timeout is not None:
            command.extend(["--timeout", str(args.timeout)])
        completed = subprocess.run(command, cwd=root, check=False)
        if completed.returncode != 0:
            failures += 1
        index.append({"task_id": task_id, "result": str(output_path), "return_code": completed.returncode})
        print(f"{task_id}: {'ok' if completed.returncode == 0 else 'failed'} ({output_path})")

    index_path = output_dir / "index.json"
    index_path.write_text(json.dumps({"runs": index}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {index_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
