# benchmarks/migrate_schema.py
"""
Migration script: benchmark_results JSON schema v1 → v2.0.

Renames legacy field names to the v2.0 schema:
  total_wall_clock_seconds  → wall_clock_s
  research_runtime_seconds  → research_runtime_s
  time_to_first_finding_seconds  → time_to_first_finding_s
  time_to_first_high_confidence_seconds  → time_to_first_high_confidence_s
  time_to_first_deep_read_seconds  → time_to_first_deep_read_s
  final_synthesis_duration_seconds  → final_synthesis_duration_s
  rss_start_mb            → rss_start_mb  (unchanged)
  rss_peak_mb             → rss_peak_mb   (unchanged)
  rss_before_synthesis_mb → rss_before_synthesis_mb
  rss_after_synthesis_mb  → rss_after_synthesis_mb
  memory_delta_synthesis_mb → memory_delta_synthesis_mb
  (remaining memory/ acquisition/ synthesis/ lanes fields kept as-is)

Also adds ``schema_version: "2.0"`` to every processed file.

Usage:
  python -m benchmarks.migrate_schema [--dry-run] [path]

  path   : directory or file to process (default: benchmark_results/)
  --dry-run : print what would change without writing
"""
from __future__ import annotations



import json
import sys
from argparse import ArgumentParser
from pathlib import Path

# Legacy → v2.0 rename map (top-level keys only)
_RENAME = {
    "total_wall_clock_seconds": "wall_clock_s",
    "research_runtime_seconds": "research_runtime_s",
    "time_to_first_finding_seconds": "time_to_first_finding_s",
    "time_to_first_high_confidence_seconds": "time_to_first_high_confidence_s",
    "time_to_first_deep_read_seconds": "time_to_first_deep_read_s",
    "final_synthesis_duration_seconds": "final_synthesis_duration_s",
}

_COUNTERS_RENAME = {
    "ct_attempts": "ct_attempts",
    "ct_successes": "ct_successes",
    "ct_failures": "ct_failures",
    "ct_timeouts": "ct_timeouts",
    "ct_candidates": "ct_candidates",
}

_TYPE_MAP = {"wall_clock_s": float, "findings_count": int, "rss_delta_mb": dict}


def migrate_record(data: dict) -> tuple[dict, list[str]]:
    """Apply schema v1→v2.0 rename in-place. Returns (new_data, list_of_renames)."""
    renames: list[str] = []
    for old, new in _RENAME.items():
        if old in data:
            data[new] = data.pop(old)
            renames.append(f"{old} → {new}")
    return data, renames


def process_file(path: Path, dry_run: bool) -> list[str]:
    """Process one JSON file. Returns list of rename messages."""
    messages: list[str] = []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  SKIP {path.name}: {exc}")
        return messages

    if not isinstance(data, dict):
        print(f"  SKIP {path.name}: not a dict")
        return messages

    _, renames = migrate_record(data)

    if data.get("schema_version") == "2.0" and not renames:
        print(f"  OK   {path.name}: already v2.0, no changes")
        return messages

    for r in renames:
        messages.append(f"  RENAME {r} in {path.name}")

    if not dry_run:
        data["schema_version"] = "2.0"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        tmp.replace(path)
        messages.append(f"  WROTE {path.name}")

    return messages


def main() -> None:
    parser = ArgumentParser(description="Migrate benchmark JSON from v1 to v2.0 schema")
    parser.add_argument("path", nargs="?", default="benchmark_results")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target = Path(args.path)
    if not target.exists():
        print(f"Error: {target} does not exist", file=sys.stderr)
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "MIGRATING"
    print(f"[{mode}] benchmark schema v1 → v2.0")

    if target.is_file():
        for msg in process_file(target, args.dry_run):
            print(msg)
    else:
        json_files = sorted(target.glob("*.json"))
        if not json_files:
            print(f"No .json files found in {target}")
            sys.exit(0)
        print(f"Found {len(json_files)} files in {target}")
        for fp in json_files:
            for msg in process_file(fp, args.dry_run):
                print(msg)

    print("Done.")


if __name__ == "__main__":
    main()
