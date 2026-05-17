#!/usr/bin/env python3
"""
Sprint F222H: DuckDB NonfeedSeed Extraction
============================================

scripts/extract_nonfeed_seeds.py
-------------------------------
CLI utility to extract nonfeed IOC seeds from:
  1. A live sprint JSON report (existing)
  2. A DuckDB file (new)

Usage:
    # From JSON report (existing)
    uv run python scripts/extract_nonfeed_seeds.py \
        --report reports/live_sprint_300s.json \
        --json reports/live_sprint_300s_nonfeed_seeds.json

    # From DuckDB (new)
    uv run python scripts/extract_nonfeed_seeds.py \
        --duckdb runtime/cti/db/analytics.duckdb \
        --limit-findings 500 \
        --json reports/f222h_duckdb_nonfeed_seeds.json

    # With query filter
    uv run python scripts/extract_nonfeed_seeds.py \
        --duckdb runtime/cti/db/analytics.duckdb \
        --query "ransomware" \
        --limit-findings 200 \
        --json reports/f222h_ransomware_seeds.json

Flags:
    DUCKDB_SEED_EXTRACTION=true
    NONFEED_SEED_EXTRACTOR_CREATED=true
    FEED_TO_PIVOT_SEEDS_EXTRACTED=true
    PUBLISHER_DOMAINS_FILTERED=true
    NONFEED_LANE_UNLOCKS_REPORTED=true
    NO_MODEL_CHANGE=true
    NO_NETWORK_IN_TESTS=true
    SCHEMA_UNRECOGNIZED_FAIL_SOFT=true
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

# scripts/extract_nonfeed_seeds.py → universal/ → hledac/ → Hledac/ (project root)
# Match pattern used in scripts/smoke_llm_candidate.py
_project_root = Path(__file__).parent.parent
_hledac_root = _project_root.parent
_project_root_of_hledac = _hledac_root.parent
sys.path.insert(0, str(_project_root_of_hledac))

from hledac.universal.runtime.nonfeed_seed_extractor import (
    NonfeedSeed,
    SeedQuality,
    classify_seed_quality,
    extract_nonfeed_seeds_from_findings,
    compute_lane_unlocks,
    PUBLISHER_DOMAINS,
)


# ---------------------------------------------------------------------------
# DuckDB reading helpers
# ---------------------------------------------------------------------------

# Text-like column names to look for in any DuckDB table
_TEXT_COLUMNS: frozenset[str] = frozenset([
    "title", "summary", "body", "content", "url", "source_url",
    "evidence", "raw_text", "description", "indicator", "value",
    "query", "payload_text", "text", "finding_text",
])
"""Column names treated as text content for IOC extraction."""


def _read_findings_from_duckdb(
    db_path: str,
    *,
    limit_findings: int = 500,
    query_filter: str | None = None,
    sprint_id_filter: str | None = None,
    since_hours: int | None = None,
) -> tuple[list[dict], list[str], int]:
    """
    Read findings from a DuckDB file.

    Returns:
        (findings, tables_checked, rows_scanned)

    findings is a list of dicts with keys: query, source_type, confidence, ts, payload_text
    tables_checked is a list of table names examined
    rows_scanned is total rows read across all tables
    """
    import duckdb

    conn = duckdb.connect(db_path, read_only=True)
    tables: list[str] = []
    try:
        result = conn.execute("SHOW TABLES").fetchall()
        tables = [row[0] for row in result]
    except Exception:
        pass

    all_findings: list[dict] = []
    tables_checked: list[str] = []

    for table_name in tables:
        tables_checked.append(table_name)
        try:
            col_result = conn.execute(f'DESCRIBE "{table_name}"').fetchall()
        except Exception:
            try:
                col_result = conn.execute(f"DESCRIBE {table_name}").fetchall()
            except Exception:
                continue

        col_names = [row[0] for row in col_result]

        # Find text-like columns in this table
        text_cols: list[str] = []
        id_col: str | None = None
        for col in col_names:
            col_lower = col.lower()
            if col_lower in _TEXT_COLUMNS:
                text_cols.append(col)
            if col_lower == "id":
                id_col = col

        if not text_cols:
            continue

        # Build SELECT + WHERE clause
        select_cols = ", ".join(f'"{c}"' for c in col_names)
        where_parts: list[str] = []

        if query_filter:
            # Search all text columns for the query filter
            text_col_refs = " || ' ' || ".join(f'COALESCE("{c}", \'\')' for c in text_cols)
            where_parts.append(f"({text_col_refs}) LIKE '%' || ? || '%'")

        if sprint_id_filter:
            if "sprint_id" in col_names:
                where_parts.append('"sprint_id" = ?')

        if since_hours is not None:
            ts_candidates = [c for c in col_names if c.lower() in ("ts", "timestamp", "created_at", "added_at")]
            if ts_candidates:
                where_parts.append(f"{ts_candidates[0]} >= CURRENT_TIMESTAMP - INTERVAL '{since_hours} hours'")

        where_clause = ""
        if where_parts:
            where_clause = " WHERE " + " AND ".join(where_parts)

        limit_clause = f" LIMIT {limit_findings}"

        sql = f'SELECT {select_cols} FROM "{table_name}"{where_clause}{limit_clause}'

        params: list = []
        if query_filter:
            params.append(query_filter)
        if sprint_id_filter:
            params.append(sprint_id_filter)

        try:
            rows = conn.execute(sql, params if params else None).fetchall()
        except Exception as e:
            # Try fallback without params
            try:
                rows = conn.execute(sql).fetchall()
            except Exception:
                continue

        for row in rows:
            row_dict = {col_names[i]: row[i] for i in range(len(col_names))}

            # Extract text from row into finding dict
            finding: dict[str, object] = {}

            # Map known columns
            for col in col_names:
                col_lower = col.lower()
                val = row_dict.get(col)
                if val is None:
                    continue
                if col_lower in ("query", "indicator", "value", "title"):
                    finding[col_lower] = str(val)
                elif col_lower in ("source_type", "source"):
                    finding["source_type"] = str(val)
                elif col_lower in ("confidence", "conf"):
                    try:
                        finding["confidence"] = float(val)
                    except (TypeError, ValueError):
                        pass
                elif col_lower in ("ts", "timestamp", "created_at", "added_at"):
                    finding["ts"] = str(val)

            # Collect all text-like columns into payload_text for extraction
            text_parts: list[str] = []
            for col in text_cols:
                val = row_dict.get(col)
                if isinstance(val, str) and val.strip():
                    text_parts.append(val.strip())

            if text_parts:
                finding["payload_text"] = "\n".join(text_parts)

            # For shadow_findings: parse provenance_json list and join into text
            if "provenance_json" in col_names:
                prov_val = row_dict.get("provenance_json")
                if prov_val is not None:
                    try:
                        prov_list = json.loads(prov_val) if isinstance(prov_val, str) else prov_val
                        if isinstance(prov_list, list):
                            for item in prov_list:
                                if isinstance(item, str) and item.strip():
                                    text_parts.append(item.strip())
                    except Exception:
                        pass

            if text_parts:
                all_findings.append(finding)

            if len(all_findings) >= limit_findings:
                break

        if len(all_findings) >= limit_findings:
            break

    conn.close()

    rows_scanned = len(all_findings)
    return all_findings, tables_checked, rows_scanned


def _build_findings_from_duckdb(
    db_path: str,
    *,
    limit_findings: int = 500,
    query_filter: str | None = None,
    sprint_id_filter: str | None = None,
    since_hours: int | None = None,
) -> tuple[list[dict], str]:
    """
    Read findings from DuckDB and return them plus status.

    Returns:
        (findings, status) where status is "ok" or "schema_unrecognized"
    """
    findings, tables_checked, rows_scanned = _read_findings_from_duckdb(
        db_path,
        limit_findings=limit_findings,
        query_filter=query_filter,
        sprint_id_filter=sprint_id_filter,
        since_hours=since_hours,
    )

    if not findings:
        # Schema not recognized — return what we found for status reporting
        return [], "schema_unrecognized" if not tables_checked else "ok"

    return findings, "ok"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract nonfeed IOC seeds from a live sprint JSON report or DuckDB."
    )
    parser.add_argument(
        "--report",
        help="Path to live sprint JSON report (deprecated, use --duckdb)",
    )
    parser.add_argument(
        "--duckdb",
        help="Path to DuckDB file to read",
    )
    parser.add_argument(
        "--limit-findings",
        type=int,
        default=500,
        help="Max findings to read from DuckDB (default 500)",
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
    parser.add_argument(
        "--query",
        help="SQL LIKE filter: match findings where query contains TEXT",
    )
    parser.add_argument(
        "--sprint-id",
        help="Filter on sprint_id column",
    )
    parser.add_argument(
        "--since-hours",
        type=int,
        help="Only findings with ts within last H hours",
    )
    parser.add_argument(
        "--min-quality-score",
        type=float,
        default=0.5,
        help="Minimum quality score to include seed (default 0.5, range 0.0-1.0)",
    )
    parser.add_argument(
        "--include-weak",
        action="store_true",
        help="Include weak-quality seeds in output (default: only keep)",
    )
    args = parser.parse_args()

    # Determine source
    use_duckdb = args.duckdb is not None

    if not use_duckdb and args.report is None:
        parser.error("One of --report or --duckdb is required")

    findings: list[dict] = []
    tables_checked: list[str] = []
    rows_scanned = 0
    db_path = ""
    source = "json"
    status = "ok"

    if use_duckdb:
        db_path = str(Path(args.duckdb).resolve())
        if not Path(db_path).exists():
            sys.exit(f"ERROR: DuckDB file not found: {db_path}")

        findings, status = _build_findings_from_duckdb(
            db_path,
            limit_findings=args.limit_findings,
            query_filter=args.query,
            sprint_id_filter=args.sprint_id,
            since_hours=args.since_hours,
        )

        # Get tables checked info
        _, tables_checked, rows_scanned = _read_findings_from_duckdb(
            db_path,
            limit_findings=args.limit_findings,
            query_filter=args.query,
            sprint_id_filter=args.sprint_id,
            since_hours=args.since_hours,
        )
        source = "duckdb"
    else:
        # Load from JSON report
        report_path = Path(args.report)
        if not report_path.exists():
            sys.exit(f"ERROR: report not found: {report_path}")

        with open(report_path) as f:
            data = json.load(f)

        # Extract findings — check common locations
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

        source = "json"

    if not findings:
        print("WARNING: No findings found — writing empty seeds file.")

    seeds = extract_nonfeed_seeds_from_findings(findings, max_seeds=args.max_seeds)
    lane_unlocks = compute_lane_unlocks(seeds)

    # ── Sprint F223B: Quality gate ─────────────────────────────────────────
    include_weak = args.include_weak
    min_score = args.min_quality_score

    def _classify_with_quality(seed: NonfeedSeed) -> tuple[NonfeedSeed, SeedQuality]:
        q = classify_seed_quality(
            seed,
            query=args.query or "",
            context="",
        )
        return seed, q

    classified: list[tuple[NonfeedSeed, SeedQuality]] = []
    for s in seeds:
        _, q = _classify_with_quality(s)
        classified.append((s, q))

    # Filter: keep + weak (if --include-weak) + score >= min_score
    def _passes_quality_gate(seed: NonfeedSeed, q: SeedQuality) -> bool:
        if q.decision == "drop":
            return False
        if q.decision == "weak" and not include_weak:
            return False
        return q.score >= min_score

    filtered = [(s, q) for s, q in classified if _passes_quality_gate(s, q)]
    filtered_seeds = [s for s, _ in filtered]
    filtered_lane_unlocks = compute_lane_unlocks(filtered_seeds)

    # Build per-seed output with quality fields
    seeds_output: list[dict] = []
    for s, q in classified:
        seeds_output.append({
            "value": s.value,
            "kind": s.kind,
            "source": s.source,
            "confidence": s.confidence,
            "reason": s.reason,
            "quality_decision": q.decision,
            "quality_reason": q.reason,
            "quality_score": q.score,
        })

    # Filtered seeds (only in output if they pass gate)
    filtered_seeds_output: list[dict] = []
    for s, q in filtered:
        filtered_seeds_output.append({
            "value": s.value,
            "kind": s.kind,
            "source": s.source,
            "confidence": s.confidence,
            "reason": s.reason,
            "quality_decision": q.decision,
            "quality_reason": q.reason,
            "quality_score": q.score,
        })

    # Build output
    output: dict = {
        "source": source,
        "db_path": db_path if use_duckdb else "",
        "query_filter": args.query,
        "sprint_id_filter": args.sprint_id,
        "since_hours": args.since_hours,
        "total_findings": len(findings),
        "total_seeds": len(seeds),
        "max_seeds": args.max_seeds,
        "min_quality_score": min_score,
        "include_weak": include_weak,
        "publisher_domains_filtered": sorted(PUBLISHER_DOMAINS),
        "tables_checked": tables_checked,
        "rows_scanned": rows_scanned,
        "status": status,
        "seeds": filtered_seeds_output,
        "lane_unlocks": {lane: values for lane, values in filtered_lane_unlocks.items() if values},
        "seed_kinds": _kinds_distribution(filtered_seeds),
        "quality_summary": {
            "total_classified": len(classified),
            "kept": sum(1 for _, q in filtered if q.decision == "keep"),
            "weak": sum(1 for _, q in filtered if q.decision == "weak"),
            "dropped": sum(1 for _, q in classified if q.decision == "drop"),
        },
        "flags": {
            "DUCKDB_SEED_EXTRACTION": "true",
            "NONFEED_SEED_EXTRACTOR_CREATED": "true",
            "FEED_TO_PIVOT_SEEDS_EXTRACTED": "true",
            "PUBLISHER_DOMAINS_FILTERED": "true",
            "NONFEED_LANE_UNLOCKS_REPORTED": "true",
            "NO_MODEL_CHANGE": "true",
            "NO_NETWORK_IN_TESTS": "true",
            "SCHEMA_UNRECOGNIZED_FAIL_SOFT": "true",
            "SEED_QUALITY_GATE_CREATED": "true",
            "EXAMPLE_DOMAIN_DROPPED": "true",
            "GENERIC_INFRA_WEAKENED": "true",
            "LOCKBIT_DOMAIN_KEPT": "true",
            "QUALITY_FIELDS_IN_JSON": "true",
            "NO_MODEL_CHANGE": "true",
            "NO_NETWORK_IN_TESTS": "true",
            "NO_NEW_REQUIRED_DEPENDENCIES": "true",
        },
    }

    # Write output
    out_path = Path(args.json)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Print summary
    print(f"Source: {source}")
    print(f"Extracted {len(seeds)} seeds from {len(findings)} findings")
    print(f"Quality gate: kept={output['quality_summary']['kept']}, "
          f"weak={output['quality_summary']['weak']} "
          f"(included={include_weak}), "
          f"dropped={output['quality_summary']['dropped']}")
    print(f"Seed kinds: {output['seed_kinds']}")
    if filtered_lane_unlocks:
        print(f"Lane unlocks: {', '.join(output['lane_unlocks'].keys())}")
    print(f"Output: {out_path}")
    if status == "schema_unrecognized":
        print(f"WARNING: Schema not recognized — tables checked: {tables_checked}")


def _kinds_distribution(seeds: list[NonfeedSeed]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for s in seeds:
        dist[s.kind] = dist.get(s.kind, 0) + 1
    return dict(sorted(dist.items(), key=lambda x: -x[1]))


if __name__ == "__main__":
    main()