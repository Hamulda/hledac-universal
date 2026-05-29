#!/usr/bin/env python3
"""
dump_asyncio_tasks.py — Safe asyncio task dumper for stuck sprint diagnosis.

Usage:
    python tools/dump_asyncio_tasks.py <PID>
    python tools/dump_asyncio_tasks.py <PID> --output-dir /tmp/dumps

This is a MANUAL operator tool only. No runtime integration, no automatic calls.
Run manually when a sprint is stuck and you need to inspect asyncio task state.

Requires: Python 3.14+ for `python -m asyncio ps/pstree` commands.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def _run_asyncio_command(pid: int, subcommand: str, timeout: float = 10.0) -> tuple[str, str, int]:
    """Run `python -m asyncio <subcommand> <pid>` and return (stdout, stderr, returncode)."""
    cmd = [sys.executable, "-m", "asyncio", subcommand, str(pid)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        )
        return proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        return "", f"Timeout after {timeout}s", -1
    except FileNotFoundError:
        return "", "python executable not found", -1
    except PermissionError:
        return "", "Permission denied", -1


def dump_asyncio_tasks(pid: int, output_dir: str | None = None) -> list[str]:
    """
    Dump asyncio ps and pstree for the given PID.

    Returns list of output file paths.
    """
    if output_dir is None:
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "reports",
            "runtime_dumps",
        )

    dump_dir = Path(output_dir)
    dump_dir.mkdir(parents=True, exist_ok=True)

    timestamp = int(time.time())
    results: list[str] = []

    for subcommand in ("ps", "pstree"):
        stdout, stderr, returncode = _run_asyncio_command(pid, subcommand)

        filename = f"asyncio_{subcommand}_{pid}_{timestamp}.txt"
        filepath = dump_dir / filename

        content = f"""=== asyncio {subcommand} for PID {pid} ===
Timestamp: {time.strftime("%Y-%m-%d %H:%M:%S")} (epoch {timestamp})
Command: python -m asyncio {subcommand} {pid}
Return code: {returncode}

--- STDOUT ---
{stdout if stdout else "(no output)"}

--- STDERR ---
{stderr if stderr else "(no stderr)"}
"""
        filepath.write_text(content)
        results.append(str(filepath))

        print(f"Saved: {filepath}", file=sys.stderr)

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dump asyncio task state for a running process (Python 3.14+).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dump tasks for a stuck sprint (from project root)
  PYTHONPATH="$PWD" python tools/dump_asyncio_tasks.py 12345

  # With custom output directory
  python tools/dump_asyncio_tasks.py 12345 --output-dir /tmp/dumps

  # Full workflow:
  # 1. Start sprint in background
  PYTHONPATH="$PWD" python -m hledac.universal.__main__ &
  SPRINT_PID=$!
  # 2. If stuck, dump tasks
  python tools/dump_asyncio_tasks.py $SPRINT_PID
  # 3. Check outputs in reports/runtime_dumps/
        """,
        suggest_on_error=True,
        color=True,
    )
    parser.add_argument("pid", type=int, help="Process ID to inspect")
    parser.add_argument(
        "--output-dir", "-o",
        help="Output directory (default: reports/runtime_dumps/)",
    )
    parser.add_argument(
        "--timeout", "-t",
        type=float, default=10.0,
        help="Timeout for each asyncio command (default: 10.0s)",
    )

    args = parser.parse_args()

    # Validate PID exists
    try:
        os.kill(args.pid, 0)  # Signal 0 just checks existence
    except OSError as e:
        print(f"Error: Process {args.pid} does not exist or is not accessible: {e}", file=sys.stderr)
        return 1

    print(f"Dumping asyncio tasks for PID {args.pid}...", file=sys.stderr)

    try:
        files = dump_asyncio_tasks(args.pid, args.output_dir)
    except Exception as e:
        print(f"Error during dump: {e}", file=sys.stderr)
        return 1

    print(f"\nDumped {len(files)} file(s):", file=sys.stderr)
    for f in files:
        print(f"  {f}")

    print("\nDone.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
