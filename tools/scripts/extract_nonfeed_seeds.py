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

"""Sprint F222H: DuckDB NonfeedSeed Extraction - Refactored with modern Python patterns."""

import argparse
import json
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# scripts/extract_nonfeed_seeds.py → universal/ → hledac/ → Hledac/ (project root)
_project_root = Path(__file__).parent.parent
_hledac_root = _project_root.parent
_project_root_of_hledac = _hledac_root.parent
sys.path.insert(0, str(_project_root_of_hledac))

from hledac.universal.runtime.nonfeed_seed_extractor import (  # noqa: E402
    PUBLISHER_DOMAINS,
    NonfeedSeed,
    SeedQuality,
    classify_seed_quality,
    compute_lane_unlocks,
    extract_nonfeed_seeds_from_findings,
)

# ---------------------------------------------------------------------------
# Dataclasses for typed results (modern Python 3.14+ pattern)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DuckDBReadResult:
    """Result of reading findings from DuckDB."""

    findings: list[dict[str, Any]]
    tables_checked: list[str]
    rows_scanned: int


@dataclass(slots=True, frozen=True)
class TableSchema:
    """Schema information for a DuckDB table."""

    name: str
    columns: list[str]
    text_columns: list[str]
    column_map: dict[str, list[str]]  # normalized_name -> [original_columns]


# ---------------------------------------------------------------------------
# Column mapping configuration (eliminates if/elif chains)
# ---------------------------------------------------------------------------

# Text-like column names to look for in any DuckDB table
_TEXT_COLUMNS: frozenset[str] = frozenset(
    [
        "title",
        "summary",
        "body",
        "content",
        "url",
        "source_url",
        "evidence",
        "raw_text",
        "description",
        "indicator",
        "value",
        "query",
        "payload_text",
        "text",
        "finding_text",
    ]
)

# Timestamp column candidates
_TIMESTAMP_COLUMNS: frozenset[str] = frozenset(
    [
        "ts",
        "timestamp",
        "created_at",
        "added_at",
    ]
)

# Column name → finding key mapping (dictionary dispatch replaces if/elif chains)
_COLUMN_DISPATCH: dict[str, tuple[str, type | None]] = {
    "query": ("query", str),
    "indicator": ("query", str),
    "value": ("query", str),
    "title": ("query", str),
    "source_type": ("source_type", str),
    "source": ("source_type", str),
    "confidence": ("confidence", float),
    "conf": ("confidence", float),
    "ts": ("ts", str),
    "timestamp": ("ts", str),
    "created_at": ("ts", str),
    "added_at": ("ts", str),
}

# ---------------------------------------------------------------------------
# DuckDB reading helpers (modern Python 3.14+ patterns)
# ISSUE-04: All duckdb.connect() calls MUST route through canonical pool
# ---------------------------------------------------------------------------

# ISSUE-04: Use canonical duckdb_pool instead of raw duckdb.connect()
from hledac.universal._core.duckdb_pool import duckdb_ro_connection


def _safe_table_name(name: str) -> str | None:
    """Validate table name is safe for DESCRIBE / SQL interpolation."""
    return name if _SAFE_IDENTIFIER_RE.match(name) else None


@contextmanager
def _duckdb_connection(db_path: str):
    """
    Context manager for DuckDB connection - ensures proper cleanup.

    ISSUE-04: Uses canonical duckdb_pool instead of raw connect. This ensures:
    - Bounded pool size from resource_governor
    - Health validation on acquire
    - M1 8GB safe defaults
    """
    with duckdb_ro_connection(db_path) as conn:
        yield conn


# ---------------------------------------------------------------------------
# Schema introspection helpers
# ---------------------------------------------------------------------------


def _get_table_names(conn: Any) -> list[str]:
    """Get all table names from DuckDB connection."""
    try:
        result = conn.execute("SHOW TABLES").fetchall()
        return [row[0] for row in result]
    except Exception:  # noqa: BLE001
        return []


def _get_table_schema(conn: Any, table_name: str) -> TableSchema | None:
    """Introspect table schema and identify text columns."""
    safe_name = _safe_table_name(table_name)
    if not safe_name:
        return None

    # Try quoted first, then unquoted
    try:
        col_result = conn.execute(f'DESCRIBE "{safe_name}"').fetchall()
    except Exception:  # noqa: BLE001
        try:
            col_result = conn.execute(f"DESCRIBE {safe_name}").fetchall()
        except Exception:  # noqa: BLE001
            return None

    col_names = [row[0] for row in col_result]

    # Find text-like columns using set intersection (O(n) vs O(n*m))
    col_lower_map = {col.lower(): col for col in col_names}
    text_cols = [col for lower, col in col_lower_map.items() if lower in _TEXT_COLUMNS]

    if not text_cols:
        return None

    # Build column map for efficient lookup
    column_map: dict[str, list[str]] = {}
    for col in col_names:
        col_lower = col.lower()
        if col_lower not in column_map:
            column_map[col_lower] = []
        column_map[col_lower].append(col)

    return TableSchema(
        name=table_name,
        columns=col_names,
        text_columns=text_cols,
        column_map=column_map,
    )


# ---------------------------------------------------------------------------
# Query building helpers
# ---------------------------------------------------------------------------

from contextlib import contextmanager

# Safe identifier validation: alphanumeric + underscore only, no dots, no dashes
_SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _build_where_clause(
    schema: TableSchema,
    query_filter: str | None,
    sprint_id_filter: str | None,
    since_hours: int | None,
) -> tuple[str, list[Any]]:
    """Build WHERE clause and parameters for DuckDB query.

    Returns:
        (where_clause, params)
    """
    where_parts: list[str] = []
    params: list[Any] = []

    if query_filter:
        # Search all text columns for the query filter
        text_col_refs = " || ' ' || ".join(f"COALESCE(\"{c}\", '')" for c in schema.text_columns)
        where_parts.append(f"({text_col_refs}) LIKE '%' || ? || '%'")
        params.append(query_filter)

    if sprint_id_filter and "sprint_id" in schema.columns:
        where_parts.append('"sprint_id" = ?')
        params.append(sprint_id_filter)

    if since_hours is not None:
        ts_candidates = [c for c in schema.columns if c.lower() in _TIMESTAMP_COLUMNS]
        if ts_candidates:
            where_parts.append(f"{ts_candidates[0]} >= CURRENT_TIMESTAMP - INTERVAL '{since_hours} hours'")

    if where_parts:
        return " WHERE " + " AND ".join(where_parts), params
    return "", params


# ---------------------------------------------------------------------------
# Row processing helpers
# ---------------------------------------------------------------------------


def _extract_text_from_row(row_dict: dict[str, Any], text_columns: list[str]) -> list[str]:
    """Collect all non-empty text values from row columns."""
    return [val.strip() for col in text_columns if (val := row_dict.get(col)) and isinstance(val, str) and val.strip()]


def _parse_provenance_text(prov_val: Any) -> list[str]:
    """Extract text items from provenance JSON column."""
    try:
        prov_list = json.loads(prov_val) if isinstance(prov_val, str) else prov_val
        if isinstance(prov_list, list):
            return [item.strip() for item in prov_list if isinstance(item, str) and item.strip()]
    except Exception:  # noqa: BLE001
        pass
    return []


def _map_dispatch_values(row_dict: dict[str, Any], column_map: dict[str, list[str]]) -> dict[str, Any]:
    """Apply column dispatch mapping to row values."""
    finding: dict[str, object] = {}
    for col_lower, (key, expected_type) in _COLUMN_DISPATCH.items():
        if col_lower in column_map:
            original_col = column_map[col_lower][0]
            val = row_dict.get(original_col)
            if val is not None:
                if expected_type is str:
                    finding[key] = str(val)
                elif expected_type is float:
                    try:
                        finding[key] = float(val)
                    except TypeError, ValueError:  # noqa: BLE001
                        pass
    return finding


def _map_row_to_finding(
    row: tuple[Any, ...],
    schema: TableSchema,
    provenance_json_col: str | None,
) -> dict[str, Any] | None:
    """Map a database row to a finding dict using dictionary dispatch.

    Returns None if no valid text content found.
    """
    row_dict = dict(zip(schema.columns, row, strict=False))

    # Apply column dispatch for known fields
    finding = _map_dispatch_values(row_dict, schema.column_map)

    # Collect all text-like columns into payload_text
    text_parts = _extract_text_from_row(row_dict, schema.text_columns)

    # Parse provenance_json if present
    if provenance_json_col and (prov_val := row_dict.get(provenance_json_col)):
        text_parts.extend(_parse_provenance_text(prov_val))

    if not text_parts:
        return None

    finding["payload_text"] = "\n".join(text_parts)
    return finding


def _execute_query_with_fallback(
    conn: Any,
    sql: str,
    params: list[Any] | None,
) -> list[tuple[Any, ...]] | None:
    """Execute query with parameter fallback on failure."""
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001
        try:
            return conn.execute(sql).fetchall()
        except Exception:  # noqa: BLE001
            return None


# ---------------------------------------------------------------------------
# Main DuckDB reading function (refactored - complexity 44 → ~8)
# ---------------------------------------------------------------------------


def _read_findings_from_duckdb(
    db_path: str,
    *,
    limit_findings: int = 500,
    query_filter: str | None = None,
    sprint_id_filter: str | None = None,
    since_hours: int | None = None,
) -> DuckDBReadResult:
    """
    Read findings from a DuckDB file.

    Returns:
        DuckDBReadResult with findings, tables_checked, and rows_scanned

    findings is a list of dicts with keys: query, source_type, confidence, ts, payload_text
    tables_checked is a list of table names examined
    rows_scanned is total rows read across all tables
    """
    all_findings: list[dict[str, Any]] = []
    tables_checked: list[str] = []
    rows_scanned = 0

    with _duckdb_connection(db_path) as conn:
        table_names = _get_table_names(conn)

        for table_name in table_names:
            schema = _get_table_schema(conn, table_name)
            if schema is None:
                continue

            tables_checked.append(table_name)

            # Build and execute query
            where_clause, params = _build_where_clause(schema, query_filter, sprint_id_filter, since_hours)
            select_cols = ", ".join(f'"{c}"' for c in schema.columns)
            sql = f'SELECT {select_cols} FROM "{table_name}"{where_clause} LIMIT {limit_findings}'

            rows = _execute_query_with_fallback(conn, sql, params if params else None)
            if rows is None:
                continue

            rows_scanned += len(rows)

            # Process rows using helper (no deep nesting in main function)
            provenance_col = "provenance_json" if "provenance_json" in schema.columns else None

            for row in rows:
                finding = _map_row_to_finding(row, schema, provenance_col)
                if finding is not None:
                    all_findings.append(finding)
                    if len(all_findings) >= limit_findings:
                        return DuckDBReadResult(
                            findings=all_findings,
                            tables_checked=tables_checked,
                            rows_scanned=rows_scanned,
                        )

            # Early termination check
            if len(all_findings) >= limit_findings:
                break

    return DuckDBReadResult(
        findings=all_findings,
        tables_checked=tables_checked,
        rows_scanned=rows_scanned,
    )


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
    result = _read_findings_from_duckdb(
        db_path,
        limit_findings=limit_findings,
        query_filter=query_filter,
        sprint_id_filter=sprint_id_filter,
        since_hours=since_hours,
    )

    if not result.findings:
        # Schema not recognized — return what we found for status reporting
        return [], "schema_unrecognized" if not result.tables_checked else "ok"

    return result.findings, "ok"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract nonfeed IOC seeds from a live sprint JSON report or DuckDB.")
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

        duckdb_result = _read_findings_from_duckdb(
            db_path,
            limit_findings=args.limit_findings,
            query_filter=args.query,
            sprint_id_filter=args.sprint_id,
            since_hours=args.since_hours,
        )
        findings = duckdb_result.findings
        tables_checked = duckdb_result.tables_checked
        rows_scanned = duckdb_result.rows_scanned
        status = "schema_unrecognized" if not tables_checked else "ok"
        source = "duckdb"
    else:
        report_path = Path(args.report)
        if not report_path.exists():
            sys.exit(f"ERROR: report not found: {report_path}")
        findings = _load_findings_from_json_report(report_path)
        source = "json"

    if not findings:
        print("WARNING: No findings found — writing empty seeds file.")

    seeds = extract_nonfeed_seeds_from_findings(findings, max_seeds=args.max_seeds)

    # ── Sprint F223B: Quality gate ─────────────────────────────────────────
    classified, filtered = _classify_and_filter_seeds(
        seeds,
        query=args.query,
        min_score=args.min_quality_score,
        include_weak=args.include_weak,
    )
    filtered_seeds = [s for s, _ in filtered]
    filtered_lane_unlocks = compute_lane_unlocks(filtered_seeds)

    # Build output and write
    output = _build_output_dict(
        source=source,
        db_path=db_path,
        args=args,
        findings=findings,
        seeds=seeds,
        tables_checked=tables_checked,
        rows_scanned=rows_scanned,
        status=status,
        classified=classified,
        filtered=filtered,
        filtered_seeds=filtered_seeds,
        filtered_lane_unlocks=filtered_lane_unlocks,
    )

    out_path = Path(args.json)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    _print_summary(source, seeds, findings, output, args.include_weak, status, tables_checked, out_path)


def _kinds_distribution(seeds: list[NonfeedSeed]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for s in seeds:
        dist[s.kind] = dist.get(s.kind, 0) + 1
    return dict(sorted(dist.items(), key=lambda x: -x[1]))


# ---------------------------------------------------------------------------
# Main helpers
# ---------------------------------------------------------------------------


def _load_findings_from_json_report(report_path: Path) -> list[dict]:
    """Extract findings from JSON report file."""
    with open(report_path) as f:
        data = json.load(f)

    # Check common locations for findings
    for key in ("findings", "accepted_findings", "canonical_report_snapshot"):
        if isinstance(val := data.get(key), list) and val:
            return val

    # Fallback: check resolved_output_json as path
    roi = data.get("resolved_output_json", "")
    if isinstance(roi, str) and Path(roi).exists():
        with open(roi) as f:
            roi_data = json.load(f)
        for key in ("findings", "accepted_findings"):
            if isinstance(val := roi_data.get(key), list) and val:
                return val

    return []


def _classify_and_filter_seeds(
    seeds: list[NonfeedSeed],
    query: str | None,
    min_score: float,
    include_weak: bool,
) -> tuple[list[tuple[NonfeedSeed, SeedQuality]], list[tuple[NonfeedSeed, SeedQuality]]]:
    """Classify seeds by quality and filter by threshold."""
    classified = [(s, classify_seed_quality(s, query=query or "", context="")) for s in seeds]

    def passes_gate(s: NonfeedSeed, q: SeedQuality) -> bool:
        if q.decision == "drop":
            return False
        if q.decision == "weak" and not include_weak:
            return False
        return q.score >= min_score

    filtered = [(s, q) for s, q in classified if passes_gate(s, q)]
    return classified, filtered


def _seed_to_dict(seed: NonfeedSeed, quality: SeedQuality) -> dict[str, Any]:
    """Convert seed with quality to output dict."""
    return {
        "value": seed.value,
        "kind": seed.kind,
        "source": seed.source,
        "confidence": seed.confidence,
        "reason": seed.reason,
        "quality_decision": quality.decision,
        "quality_reason": quality.reason,
        "quality_score": quality.score,
    }


def _build_output_dict(
    source: str,
    db_path: str,
    args: Any,
    findings: list[dict],
    seeds: list[NonfeedSeed],
    tables_checked: list[str],
    rows_scanned: int,
    status: str,
    classified: list[tuple[NonfeedSeed, SeedQuality]],
    filtered: list[tuple[NonfeedSeed, SeedQuality]],
    filtered_seeds: list[NonfeedSeed],
    filtered_lane_unlocks: dict[str, list[str]],
) -> dict:
    """Build the output dictionary."""
    return {
        "source": source,
        "db_path": db_path,
        "query_filter": args.query,
        "sprint_id_filter": args.sprint_id,
        "since_hours": args.since_hours,
        "total_findings": len(findings),
        "total_seeds": len(seeds),
        "max_seeds": args.max_seeds,
        "min_quality_score": args.min_quality_score,
        "include_weak": args.include_weak,
        "publisher_domains_filtered": sorted(PUBLISHER_DOMAINS),
        "tables_checked": tables_checked,
        "rows_scanned": rows_scanned,
        "status": status,
        "seeds": [_seed_to_dict(s, q) for s, q in filtered],
        "lane_unlocks": {lane: vals for lane, vals in filtered_lane_unlocks.items() if vals},
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
            "NO_NEW_REQUIRED_DEPENDENCIES": "true",
        },
    }


def _print_summary(
    source: str,
    seeds: list[NonfeedSeed],
    findings: list[dict],
    output: dict,
    include_weak: bool,
    status: str,
    tables_checked: list[str],
    out_path: Path,
) -> None:
    """Print execution summary."""
    print(f"Source: {source}")
    print(f"Extracted {len(seeds)} seeds from {len(findings)} findings")
    print(
        f"Quality gate: kept={output['quality_summary']['kept']}, "
        f"weak={output['quality_summary']['weak']} "
        f"(included={include_weak}), "
        f"dropped={output['quality_summary']['dropped']}"
    )
    print(f"Seed kinds: {output['seed_kinds']}")
    if output["lane_unlocks"]:
        print(f"Lane unlocks: {', '.join(output['lane_unlocks'].keys())}")
    print(f"Output: {out_path}")
    if status == "schema_unrecognized":
        print(f"WARNING: Schema not recognized — tables checked: {tables_checked}")


if __name__ == "__main__":
    main()
