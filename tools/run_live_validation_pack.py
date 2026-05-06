#!/usr/bin/env python3
"""Live Validation Pack Runner — F209B

One command to run live validation with absolute paths.
Default: dry-run only (prints commands without executing).

Usage:
    python tools/run_live_validation_pack.py --base-dir /path/to/base --tag f209b --query "domain:example.com" --profile active300
    python tools/run_live_validation_pack.py --base-dir /path/to/base --tag f209b --query "domain:example.com" --profile active300 --execute
"""

import argparse
import sys
from pathlib import Path

# Absolute paths — never relative
REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
BENCHMARK_SCRIPT = REPO_ROOT / "benchmarks/live_sprint_measurement.py"
VALIDATOR_SCRIPT = REPO_ROOT / "tools/live_multisource_validator.py"
TRACE_SCRIPT = REPO_ROOT / "tools/report_truth_trace.py"


def build_benchmark_cmd(base_dir: Path, tag: str, query: str, profile: str) -> list[str]:
    output_json = base_dir / f"benchmark_{tag}.json"
    output_md = base_dir / f"benchmark_{tag}.md"
    return [
        sys.executable, str(BENCHMARK_SCRIPT),
        "--profile", profile,
        "--query", query,
        "--dry-run",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ]


def build_validator_cmd(base_dir: Path, tag: str, profile: str, query_type: str = "domain") -> list[str]:
    input_json = base_dir / f"benchmark_{tag}.json"
    output_json = base_dir / f"validation_{tag}.json"
    output_md = base_dir / f"validation_{tag}.md"
    return [
        sys.executable, str(VALIDATOR_SCRIPT),
        "--input-json", str(input_json),
        "--output-json", str(output_json),
        "--output-md", str(output_md),
        "--profile", profile,
        "--query-type", query_type,
    ]


def build_trace_cmd(base_dir: Path, tag: str) -> list[str]:
    benchmark_json = base_dir / f"benchmark_{tag}.json"
    validation_json = base_dir / f"validation_{tag}.json"
    output_json = base_dir / f"truth_trace_{tag}.json"
    output_md = base_dir / f"truth_trace_{tag}.md"
    return [
        sys.executable, str(TRACE_SCRIPT),
        "--benchmark-json", str(benchmark_json),
        "--validation-json", str(validation_json),
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ]


def run_dry_run(base_dir: Path, tag: str, query: str, profile: str) -> None:
    print(f"[dry-run] Base dir: {base_dir}")
    print()

    bm_cmd = build_benchmark_cmd(base_dir, tag, query, profile)
    print("[benchmark] " + " ".join(bm_cmd))
    print()

    val_cmd = build_validator_cmd(base_dir, tag, profile)
    print("[validator] " + " ".join(val_cmd))
    print()

    trace_cmd = build_trace_cmd(base_dir, tag)
    print("[trace] " + " ".join(trace_cmd))


def run_execute(base_dir: Path, tag: str, query: str, profile: str) -> None:
    import subprocess

    base_dir.mkdir(parents=True, exist_ok=True)

    print(f"[execute] Base dir: {base_dir}")

    bm_cmd = build_benchmark_cmd(base_dir, tag, query, profile)
    print("[benchmark] " + " ".join(bm_cmd))
    result = subprocess.run(bm_cmd)
    if result.returncode != 0:
        print(f"[ERROR] Benchmark failed with code {result.returncode}")
        sys.exit(result.returncode)
    print()

    val_cmd = build_validator_cmd(base_dir, tag, profile)
    print("[validator] " + " ".join(val_cmd))
    result = subprocess.run(val_cmd)
    if result.returncode != 0:
        print(f"[ERROR] Validator failed with code {result.returncode}")
        sys.exit(result.returncode)
    print()

    trace_cmd = build_trace_cmd(base_dir, tag)
    print("[trace] " + " ".join(trace_cmd))
    result = subprocess.run(trace_cmd)
    if result.returncode != 0:
        print(f"[ERROR] Trace failed with code {result.returncode}")
        sys.exit(result.returncode)

    print()
    print(f"[done] Results in {base_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Validation Pack Runner")
    parser.add_argument("--base-dir", type=str, required=True, help="Working directory for artifacts")
    parser.add_argument("--tag", type=str, required=True, help="Sprint tag (e.g. f209b)")
    parser.add_argument("--query", type=str, required=True, help="Sprint query string")
    parser.add_argument("--profile", type=str, default="active300", help="Profile name (default: active300)")
    parser.add_argument("--query-type", type=str, default="domain", help="Query type (default: domain)")
    parser.add_argument("--execute", action="store_true", help="Actually execute commands (default is dry-run)")
    args = parser.parse_args()

    base_dir = Path(args.base_dir)

    if args.execute:
        run_execute(base_dir, args.tag, args.query, args.profile)
    else:
        run_dry_run(base_dir, args.tag, args.query, args.profile)


if __name__ == "__main__":
    main()
