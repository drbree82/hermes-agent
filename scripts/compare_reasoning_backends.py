#!/usr/bin/env python3
"""Run one Hermes task through the legacy and ARC-inspired substrates.

This is deliberately a thin wrapper around the normal ``hermes -z`` entry
point. It does not create a second agent implementation or require ARC
credentials/services.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
    if task_id == "long_context_distributed_evidence":
        artifact = workspace / "LONG_REPORT.md"
        text = artifact.read_text(encoding="utf-8") if artifact.exists() else ""
        required = ("ANCHOR=ORBIT-7", "CONTROL=MIDDLE-42", "CHECKSUM=LATE-91")
        ledger_path = workspace / "INSPECTION_LEDGER.json"
        ledger: object = None
        ledger_error = ""
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            ledger_error = str(exc)
        entries = ledger if isinstance(ledger, list) else []
        by_id = {
            str(entry.get("id")): entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("id") is not None
        }
        evidence_dir = workspace / "evidence"
        expected_ids = {f"{index:03d}" for index in range(1, 51)}
        unverified_ids = {f"{index:03d}" for index in range(7, 50, 7)}
        ledger_valid = len(entries) == 50 and set(by_id) == expected_ids
        hash_valid = True
        status_valid = True
        for evidence_id in expected_ids:
            evidence_path = evidence_dir / f"{evidence_id}.txt"
            entry = by_id.get(evidence_id, {})
            try:
                digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            except OSError:
                digest = ""
            hash_valid = hash_valid and entry.get("sha256") == digest
            expected_status = "UNVERIFIED" if evidence_id in unverified_ids else "VERIFIED"
            status_valid = status_valid and entry.get("status") == expected_status
        anchor_facts = all(term in text for term in required)
        distractors_not_verified = True
        for evidence_id in unverified_ids:
            for line in text.splitlines():
                if not re.search(rf"\b{evidence_id}(?:\.txt)?\b", line, re.IGNORECASE):
                    continue
                lower = line.lower()
                explicitly_rejected = (
                    "unverified" in lower or "not verified" in lower
                    or "unsupported" in lower or "distractor" in lower
                )
                specific_status_line = bool(re.search(
                    rf"(?:id|evidence|fragment|file)?\s*{evidence_id}(?:\.txt)?\s*(?:is|:|[-(])",
                    lower,
                ))
                positive_claim = bool(
                    re.search(r"\bverified\b|\bconfirmed\b|\bfact\b", lower)
                )
                if specific_status_line and positive_claim and not explicitly_rejected:
                    distractors_not_verified = False
        synthesis = all(
            token in text.lower()
            for token in ("orbit-7", "middle-42", "late-91", "combined")
        )
        return {
            "kind": "distributed_evidence_check",
            "passed": (
                bool(text.strip()) and anchor_facts and synthesis
                and ledger_valid and hash_valid and status_valid
                and distractors_not_verified
            ),
            "path": str(artifact),
            "chars": len(text),
            "ledger_path": str(ledger_path),
            "ledger_entries": len(entries),
            "ledger_valid": ledger_valid,
            "ledger_hashes_valid": hash_valid,
            "ledger_statuses_valid": status_valid,
            "distractors_not_verified": distractors_not_verified,
            "synthesis_present": synthesis,
            "ledger_error": ledger_error,
        }
    if task_id == "long_coding_debug":
        report = workspace / "REPAIR_TRACE.md"
        text = report.read_text(encoding="utf-8") if report.exists() else ""
        required_ids = all(f"{index:03d}" in text for index in range(1, 13))
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=workspace, capture_output=True, text=True, timeout=30, check=False,
        )
        return {
            "kind": "coding_trace_check",
            "passed": (
                completed.returncode == 0 and required_ids
                and all(term in text for term in (
                    "INPUT_CONTRACT=stable-v2", "REGRESSION=cursor-reset",
                    "LATE_FAILURE=empty-batch",
                ))
            ),
            "pytest_passed": completed.returncode == 0,
            "evidence_ids_listed": sum(1 for index in range(1, 13) if f"{index:03d}" in text),
            "path": str(report),
        }
    if task_id == "long_operational_incident":
        report = workspace / "INCIDENT_REPORT.md"
        text = report.read_text(encoding="utf-8") if report.exists() else ""
        required_ids = all(f"{index:03d}" in text for index in range(1, 13))
        unverified_rejected = "007" not in text or not re.search(
            r"007[^\n]*(?:fact|verified|confirmed)", text, re.IGNORECASE
        )
        return {
            "kind": "incident_trace_check",
            "passed": (
                bool(text.strip()) and required_ids and unverified_rejected
                and all(term in text for term in (
                    "ROOT_CAUSE=stale-route", "MITIGATION=reload-after-route-fix",
                    "RECOVERY=healthcheck-green",
                ))
            ),
            "evidence_ids_listed": sum(1 for index in range(1, 13) if f"{index:03d}" in text),
            "unverified_rejected": unverified_rejected,
            "path": str(report),
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
        "--continuity-mode", choices=("adaptive", "always"), default=None,
        help="Override Tier-2 mode for arc_continuous (use always for eager A/B)",
    )
    parser.add_argument("--trajectory-message-threshold", type=int, default=None)
    parser.add_argument("--activation-message-threshold", type=int, default=None)
    parser.add_argument("--activation-tool-chars", type=int, default=None)
    parser.add_argument("--activation-context-ratio", type=float, default=None)
    parser.add_argument("--capsule-token-budget", type=int, default=None)
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
                continuity_overrides = []
                if args.continuity_mode == "always":
                    continuity_overrides.extend([
                        "  mode: always",
                        "  capsule_token_budget: 1200",
                        "  activation_context_ratio: 0.72",
                        "  activation_message_count: 24",
                        "  activation_tool_chars: 24000",
                        "  active_turn_tail_messages: 8",
                    ])
                if args.continuity_mode == "adaptive":
                    continuity_overrides.append("  mode: adaptive")
                if args.activation_message_threshold is not None:
                    continuity_overrides.append(f"  activation_message_count: {args.activation_message_threshold}")
                if args.activation_tool_chars is not None:
                    continuity_overrides.append(f"  activation_tool_chars: {args.activation_tool_chars}")
                if args.activation_context_ratio is not None:
                    continuity_overrides.append(f"  activation_context_ratio: {args.activation_context_ratio}")
                if args.capsule_token_budget is not None:
                    continuity_overrides.append(f"  capsule_token_budget: {args.capsule_token_budget}")
                if continuity_overrides:
                    config_text += (
                        "\nreasoning_continuity:\n"
                        + "\n".join(continuity_overrides) + "\n"
                    )
                if args.trajectory_message_threshold is not None:
                    config_text += (
                        "\nreasoning_continuity:\n"
                        f"  mode: {args.continuity_mode or 'adaptive'}\n"
                        f"  trajectory_message_threshold: {args.trajectory_message_threshold}\n"
                        f"  trajectory_compaction_min_new_messages: 8\n"
                        f"  trajectory_compaction_min_new_tool_chars: 12000\n"
                        "  active_turn_tail_messages: 8\n"
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
