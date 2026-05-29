#!/usr/bin/env python3
"""
scripts/score_corroboration.py

CLI for Evidence Corroboration Scorer — Sprint F223D

Inputs:
  --report <path>     — JSON report with findings list (optional, mutually exclusive with --duckdb)
  --duckdb <path>      — DuckDB path + query to extract findings (optional)
  --seeds-json <path>  — Seeds JSON from nonfeed seed extractor (optional)

Outputs JSON:
  {
    "top_indicators": [...],
    "source_family_support": {...},
    "weak_unverified_indicators": [...],
    "recommended_next_pivots": [...],
    "summary": {...}
  }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from runtime.evidence_corroboration import (
    CorroborationScore,
    build_recommended_pivots,
    build_top_indicators,
    build_weak_unverified,
    score_indicators_by_corroboration,
)


def load_report(path: str) -> list[dict]:
    with open(path) as f:
        data = json.load(f)
    # Accept both list of findings and dict with findings key
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "findings" in data:
            return data["findings"]
        if "canonical_report_snapshot" in data:
            snap = data["canonical_report_snapshot"]
            if isinstance(snap, list):
                return snap
            if isinstance(snap, dict) and "findings" in snap:
                return snap["findings"]
    return []


def load_seeds(path: str) -> list[dict]:
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict) and "seeds" in data:
        return data["seeds"]
    if isinstance(data, list):
        return data
    return []


def source_family_summary(scores: list[CorroborationScore]) -> dict:
    family_counts: dict[str, int] = {}
    for s in scores:
        if s.source_family_count > 0:
            key = f"family_{s.source_family_count}"
            family_counts[key] = family_counts.get(key, 0) + 1
    return {
        "total_indicators": len(scores),
        "strong_count": sum(1 for s in scores if s.is_strong()),
        "weak_count": sum(1 for s in scores if s.is_weak()),
        "noise_count": sum(1 for s in scores if s.is_noise()),
        "by_family_count": family_counts,
        "avg_score": round(sum(s.score for s in scores) / len(scores), 2) if scores else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score indicators by cross-source corroboration")
    parser.add_argument("--report", type=str, help="JSON report with findings list")
    parser.add_argument("--duckdb", type=str, help="DuckDB path (requires --query)")
    parser.add_argument("--query", type=str, help="SQL query for DuckDB (use with --duckdb)")
    parser.add_argument("--seeds-json", type=str, help="Seeds JSON from nonfeed seed extractor")
    parser.add_argument("--output", type=str, help="Output JSON path (default: stdout)")
    args = parser.parse_args()

    findings: list[dict] = []

    if args.report:
        findings = load_report(args.report)
    elif args.seeds_json:
        seeds = load_seeds(args.seeds_json)
        # Score seeds as findings
        from runtime.evidence_corroboration import score_seeds_by_corroboration as _score_seeds
        scores = _score_seeds(seeds)
    elif args.duckdb:
        import duckdb
        if not args.query:
            print("ERROR: --query required with --duckdb", file=sys.stderr)
            sys.exit(1)
        conn = duckdb.connect(args.duckdb, read_only=True)
        rows = conn.execute(args.query).fetchall()
        col_names = [c[0] for c in conn.description] if conn.description else []
        findings = [dict(zip(col_names, row, strict=False)) for row in rows]
        conn.close()
    else:
        print("ERROR: provide --report, --seeds-json, or --duckdb", file=sys.stderr)
        sys.exit(1)

    if not args.seeds_json:
        scores = score_indicators_by_corroboration(findings)

    output = {
        "top_indicators": build_top_indicators(scores),
        "source_family_support": source_family_summary(scores),
        "weak_unverified_indicators": build_weak_unverified(scores),
        "recommended_next_pivots": build_recommended_pivots(scores),
        "_meta": {
            "total_scores": len(scores),
            "input_source": args.report or args.seeds_json or args.duckdb or "unknown",
        },
    }

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Wrote {len(scores)} scores to {args.output}")
    else:
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
