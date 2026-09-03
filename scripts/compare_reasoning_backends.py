#!/usr/bin/env python3
"""Run one Hermes task through the legacy and ARC-inspired substrates.

This is deliberately a thin wrapper around the normal ``hermes -z`` entry
point. It does not create a second agent implementation or require ARC
credentials/services.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="The identical task to run twice")
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
    records = []
    with tempfile.TemporaryDirectory(prefix="hermes-reasoning-ab-") as temp_dir:
        for backend in ("legacy", "arc_continuous"):
            usage_path = Path(temp_dir) / f"{backend}.json"
            command = [
                sys.executable,
                "-m",
                "hermes_cli.main",
                "-z",
                args.prompt,
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
                completed = subprocess.run(
                    command,
                    cwd=root,
                    text=True,
                    capture_output=True,
                    timeout=args.timeout,
                    check=False,
                )
                stdout = completed.stdout
                stderr = completed.stderr
                return_code = completed.returncode
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout or ""
                stderr = exc.stderr or ""
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
                }
            )

    report = {"prompt": args.prompt, "runs": records}
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if all(item["return_code"] == 0 for item in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
