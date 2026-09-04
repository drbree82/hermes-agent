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
import signal
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def _run_backend(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float | None,
) -> tuple[str, str, int]:
    """Run one backend in a killable process group."""

    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return stdout, stderr, process.returncode
    except subprocess.TimeoutExpired as exc:
        # Hermes may spawn helper processes whose inherited pipes otherwise
        # keep communicate() blocked after the parent deadline.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        return _text(stdout or exc.stdout), _text(stderr or exc.stderr), 124


def _validate_artifact(task_id: str | None, workspace: Path) -> dict[str, object] | None:
    """Validate benchmark deliverables without trusting the final answer."""

    if not task_id or not workspace.exists():
        return None
    if task_id == "coding_broken_repo":
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return {
            "kind": "pytest",
            "passed": completed.returncode == 0,
            "return_code": completed.returncode,
            "output": (completed.stdout + completed.stderr)[-4000:],
        }
    if task_id == "sysadmin_broken_docker":
        compose = workspace / "docker-compose.yml"
        text = compose.read_text(encoding="utf-8") if compose.exists() else ""
        passed = "8080:8080" in text and "8081" not in text
        return {"kind": "static_compose_check", "passed": passed, "details": text[-2000:]}
    if task_id == "long_horizon_inventory":
        artifact = workspace / "INVENTORY.md"
        text = artifact.read_text(encoding="utf-8") if artifact.exists() else ""
        required = ("manifest", "pricing", "regions")
        return {
            "kind": "artifact_check",
            "passed": bool(text.strip()) and all(term in text.lower() for term in required),
            "path": str(artifact),
            "chars": len(text),
        }
    if task_id == "failure_recovery":
        artifact = workspace / "RECOVERY.md"
        text = artifact.read_text(encoding="utf-8") if artifact.exists() else ""
        return {
            "kind": "artifact_check",
            "passed": bool(text.strip()) and "missing" in text.lower(),
            "path": str(artifact),
            "chars": len(text),
        }
    return None


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
        "--backends", nargs="+", choices=("legacy", "arc_continuous"),
        default=("legacy", "arc_continuous"),
        help="Backends to run (defaults to the complete A/B pair)",
    )
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
        for backend in args.backends:
            usage_path = Path(temp_dir) / f"{backend}.json"
            # The sandbox used for repeatable runs may make the real
            # ~/.hermes read-only. Copy only user configuration/credentials
            # into an isolated temporary Hermes home; logs, sessions and
            # auxiliary continuous state then cannot leak between runs.
            benchmark_home = Path(temp_dir) / f"hermes-home-{backend}"
            benchmark_home.mkdir(parents=True, exist_ok=True)
            source_home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
            for home_file in ("config.yaml", ".env"):
                source_file = source_home / home_file
                if source_file.exists():
                    shutil.copy2(source_file, benchmark_home / home_file)
            if fixture is not None:
                run_workdir = Path(temp_dir) / backend / fixture.name
                shutil.copytree(fixture, run_workdir)
            else:
                run_workdir = args.workdir.expanduser().resolve() if args.workdir else root
            config_path = benchmark_home / "config.yaml"
            if config_path.exists():
                config_text = config_path.read_text(encoding="utf-8")
                config_text = config_text.replace(
                    "  cwd: ~/", f"  cwd: {run_workdir}", 1
                )
                config_path.write_text(config_text, encoding="utf-8")
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
            env = os.environ.copy()
            env["HERMES_HOME"] = str(benchmark_home)
            existing_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = (
                str(root)
                if not existing_pythonpath
                else os.pathsep.join((str(root), existing_pythonpath))
            )
            if task_id:
                env["HERMES_REASONING_BENCHMARK_TASK"] = str(task_id)
            stdout, stderr, return_code = _run_backend(
                command,
                cwd=run_workdir,
                env=env,
                timeout=args.timeout,
            )

            usage = {}
            if usage_path.exists():
                try:
                    usage = json.loads(usage_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    usage = {"usage_report_error": True}
            validation = None
            try:
                validation = _validate_artifact(task_id, run_workdir)
            except (OSError, subprocess.SubprocessError) as exc:
                validation = {"kind": "validator_error", "passed": False, "error": str(exc)}
            records.append(
                {
                    "backend": backend,
                    "return_code": return_code,
                    "timed_out": return_code == 124,
                    "status": "timeout" if return_code == 124 else "completed",
                    "stdout": stdout,
                    "stderr": stderr,
                    "usage": usage,
                    "artifact_validation": validation,
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
