#!/usr/bin/env python3
"""Run one Hermes task through the legacy and ARC-inspired substrates.

This is deliberately a thin wrapper around the normal ``hermes -z`` entry
point. It does not create a second agent implementation or require ARC
credentials/services.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", help="The identical task to run twice")
    parser.add_argument(
        "--task-file",
        type=Path,
        help="JSON task catalog; use with --task-id instead of a positional prompt",
    )
    parser.add_argument("--task-id", help="Task id from --task-file")
    parser.add_argument(
        "--fixture",
        type=Path,
        help="Copy this fixture into a fresh workspace for each backend run",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        help="Existing workspace for both runs when --fixture is not used",
    )
    parser.add_argument("--model", help="Model passed to both Hermes runs")
    parser.add_argument("--provider", help="Provider passed to both Hermes runs")
    parser.add_argument("--toolsets", help="Toolsets passed to both Hermes runs")
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the comparison JSON here (default: stdout)",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    prompt = args.prompt
    fixture = args.fixture
    task_id = args.task_id
    if args.task_file:
        catalog = json.loads(args.task_file.expanduser().read_text(encoding="utf-8"))
        tasks = catalog.get("tasks") if isinstance(catalog, dict) else None
        selected = next(
            (task for task in tasks or [] if task.get("id") == args.task_id),
            None,
        )
        if selected is None:
            parser.error(f"unknown --task-id {args.task_id!r} in {args.task_file}")
        prompt = selected.get("prompt")
        task_id = selected.get("id")
        if selected.get("fixture") and fixture is None:
            fixture = root / selected["fixture"]
    if not prompt:
        parser.error("provide PROMPT or --task-file TASKS.json --task-id ID")
    if fixture is not None:
        fixture = fixture.expanduser()
        if not fixture.is_absolute():
            fixture = (root / fixture).resolve()
        if not fixture.is_dir():
            parser.error(f"fixture directory does not exist: {fixture}")
    if args.workdir and fixture:
        parser.error("--workdir and --fixture are mutually exclusive")

    records = []
    with tempfile.TemporaryDirectory(prefix="hermes-reasoning-ab-") as temp_dir:
        for backend in ("legacy", "arc_continuous"):
            usage_path = Path(temp_dir) / f"{backend}.json"
            if fixture is not None:
                run_workdir = Path(temp_dir) / backend / fixture.name
                shutil.copytree(fixture, run_workdir)
            else:
                run_workdir = args.workdir.expanduser().resolve() if args.workdir else root
            command = [
                sys.executable,
                "-m",
                "hermes_cli.main",
                "-z",
                prompt,
                "--reasoning-backend",
                backend,
                "--usage-file",
                str(usage_path),
            ]
            if args.model:
                command.extend(["--model", args.model])
            if args.provider:
                command.extend(["--provider", args.provider])
            if args.toolsets:
                command.extend(["--toolsets", args.toolsets])
            try:
                env = os.environ.copy()
                existing_pythonpath = env.get("PYTHONPATH")
                env["PYTHONPATH"] = (
                    str(root)
                    if not existing_pythonpath
                    else os.pathsep.join((str(root), existing_pythonpath))
                )
                if task_id:
                    env["HERMES_REASONING_BENCHMARK_TASK"] = str(task_id)
                completed = subprocess.run(
                    command,
                    cwd=run_workdir,
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=args.timeout,
                    check=False,
                )
                stdout = completed.stdout
                stderr = completed.stderr
                return_code = completed.returncode
            except subprocess.TimeoutExpired as exc:
                stdout = _text(exc.stdout)
                stderr = _text(exc.stderr)
                return_code = 124

            usage = {}
            if usage_path.exists():
                try:
                    usage = json.loads(usage_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    usage = {"usage_report_error": True}
            records.append(
                {
                    "backend": backend,
                    "return_code": return_code,
                    "stdout": stdout,
                    "stderr": stderr,
                    "usage": usage,
                    "workspace": str(run_workdir),
                }
            )

    report = {
        "task_id": task_id,
        "prompt": prompt,
        "fixture": str(fixture) if fixture else None,
        "runs": records,
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if all(item["return_code"] == 0 for item in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
