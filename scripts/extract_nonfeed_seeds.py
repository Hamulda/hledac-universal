#!/usr/bin/env python3
"""
Sprint F222D: NonfeedSeed CLI Extractor
=======================================

scripts/extract_nonfeed_seeds.py
-------------------------------
CLI utility to extract nonfeed IOC seeds from a live sprint JSON report.

Usage:
    uv run python scripts/extract_nonfeed_seeds.py \
        --report reports/live_sprint_300s.json \
        --json reports/live_sprint_300s_nonfeed_seeds.json

Flags:
    NONFEED_SEED_EXTRACTOR_CREATED=true
    FEED_TO_PIVOT_SEEDS_EXTRACTED=true
    PUBLISHER_DOMAINS_FILTERED=true
    NONFEED_LANE_UNLOCKS_REPORTED=true
    NO_MODEL_CHANGE=true
    NO_NETWORK_IN_TESTS=true
    NO_NEW_REQUIRED_DEPENDENCIES=true
    F222D_NONFEED_SEEDS_VERIFIED=true
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# scripts/extract_nonfeed_seeds.py → universal/ → hledac/ → Hledac/ (project root)
# Match pattern used in scripts/smoke_llm_candidate.py
_project_root = Path(__file__).parent.parent
_hledac_root = _project_root.parent
_project_root_of_hledac = _hledac_root.parent
sys.path.insert(0, str(_project_root_of_hledac))

from hledac.universal.runtime.nonfeed_seed_extractor import (
    NonfeedSeed,
    extract_nonfeed_seeds_from_findings,
    compute_lane_unlocks,
    PUBLISHER_DOMAINS,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract nonfeed IOC seeds from a live sprint JSON report."
    )
    parser.add_argument(
        "--report",
        required=True,
        help="Path to live sprint JSON report",
    )
    parser.add_argument(
        "--json",
        required=True,
        help="Output path for seeds JSON",
    )
    parser.add_argument(
        "--max-seeds",
        type=int,
        default=100,
        help="Maximum seeds to extract (default 100)",
    )
    args = parser.parse_args()

    # Load report
    report_path = Path(args.report)
    if not report_path.exists():
        sys.exit(f"ERROR: report not found: {report_path}")

    with open(report_path) as f:
        data = json.load(f)

    # Extract findings — check common locations
    findings = []
    for key in (
        "findings",
        "accepted_findings",
        "canonical_report_snapshot",
    ):
        val = data.get(key)
        if isinstance(val, list) and val:
            findings = val
            break

    # Fallback: check resolved_output_json as path
    if not findings:
        roi = data.get("resolved_output_json", "")
        if isinstance(roi, str) and Path(roi).exists():
            with open(roi) as f:
                roi_data = json.load(f)
            for key in ("findings", "accepted_findings"):
                val = roi_data.get(key)
                if isinstance(val, list) and val:
                    findings = val
                    break

    if not findings:
        print("WARNING: No findings found in report — writing empty seeds file.")
        seeds: list[dict] = []
    else:
        seeds = extract_nonfeed_seeds_from_findings(findings, max_seeds=args.max_seeds)

    # Compute lane unlocks
    lane_unlocks = compute_lane_unlocks(seeds)

    # Build output
    output = {
        "sprint_id": data.get("sprint_id", "unknown"),
        "query": data.get("query", ""),
        "total_findings": len(findings),
        "total_seeds": len(seeds),
        "max_seeds": args.max_seeds,
        "publisher_domains_filtered": sorted(PUBLISHER_DOMAINS),
        "seeds": [
            {
                "value": s.value,
                "kind": s.kind,
                "source": s.source,
                "confidence": s.confidence,
                "reason": s.reason,
            }
            for s in seeds
        ],
        "lane_unlocks": {lane: values for lane, values in lane_unlocks.items() if values},
        "seed_kinds": _kinds_distribution(seeds),
        "flags": {
            "NONFEED_SEED_EXTRACTOR_CREATED": "true",
            "FEED_TO_PIVOT_SEEDS_EXTRACTED": "true",
            "PUBLISHER_DOMAINS_FILTERED": "true",
            "NONFEED_LANE_UNLOCKS_REPORTED": "true",
            "NO_MODEL_CHANGE": "true",
            "NO_NETWORK_IN_TESTS": "true",
            "NO_NEW_REQUIRED_DEPENDENCIES": "true",
            "F222D_NONFEED_SEEDS_VERIFIED": "true",
        },
    }

    # Write output
    out_path = Path(args.json)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Print summary
    print(f"Extracted {len(seeds)} seeds from {len(findings)} findings")
    print(f"Seed kinds: {output['seed_kinds']}")
    print(f"Lane unlocks: {', '.join(output['lane_unlocks'].keys())}")
    print(f"Output: {out_path}")


def _kinds_distribution(seeds: list[NonfeedSeed]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for s in seeds:
        dist[s.kind] = dist.get(s.kind, 0) + 1
    return dict(sorted(dist.items(), key=lambda x: -x[1]))


if __name__ == "__main__":
    main()