"""
runtime/nonfeed_seed_runtime.py — F223A: Runtime Pivot Prelude from Findings/DuckDB
==============================================================================

Bounded runtime pivot prelude for nonfeed_diagnostic profiles.

When a text threat query arrives and the nonfeed_diagnostic profile is active,
this module extracts seeds from recent DuckDB findings BEFORE nonfeed lanes run.
The extracted seeds (domains/IPs/URLs/hashes/CVEs) are threaded into
SprintResult pivot_seed_* fields so build_lane_query() can shape lane queries.

Safety invariants:
  - No network I/O
  - No model/MLX load
  - No broad DB scan (default row cap <= 1000)
  - Fail-soft: missing DB or schema → no crash, skip reason recorded
  - DuckDB only accessed when duckdb_store is available and initialized
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .nonfeed_seed_extractor import (
    extract_nonfeed_seeds_from_findings,
    extract_nonfeed_seeds_from_text,
)

if TYPE_CHECKING:
    from ..knowledge.duckdb_store import DuckDBShadowStore

# Upper bounds for runtime pivot prelude seed extraction
_MAX_SEEDS_FROM_QUERY: int = 50
_MAX_SEEDS_FROM_FINDINGS: int = 100
_MAX_ROWS_FROM_DUCKDB: int = 1000

# Lane unlock mapping: seed kind → expected nonfeed lanes
_LANES_BY_SEED: dict[str, list[str]] = {
    "domain": ["CT", "DOH", "WAYBACK", "PASSIVE_DNS"],
    "ip": ["CT", "PASSIVE_DNS"],
    "url": ["WAYBACK"],
    "hash": ["CT"],
    "cve": ["CT"],
}


def _is_text_query_without_direct_seeds(query: str) -> bool:
    """
    Returns True if the query is a text/threat query without direct IOC seeds.

    A domain/IP/URL query has direct seeds (already extractable from the query).
    A text threat query (e.g. 'ransomware group APT29') does not — we need
    DuckDB findings to extract seeds.
    """
    _extracted = extract_nonfeed_seeds_from_text(query, max_seeds=1)
    if _extracted:
        return False
    return True


def _compute_lanes_unlocked(
    domains: tuple[str, ...],
    ips: tuple[str, ...],
    urls: tuple[str, ...],
    hashes: tuple[str, ...],
    cves: tuple[str, ...],
) -> list[str]:
    """Compute which lanes are unlocked by the extracted seeds."""
    lanes: set[str] = set()
    if domains:
        lanes.update(_LANES_BY_SEED["domain"])
    if ips:
        lanes.update(_LANES_BY_SEED["ip"])
    if urls:
        lanes.update(_LANES_BY_SEED["url"])
    if hashes:
        lanes.update(_LANES_BY_SEED["hash"])
    if cves:
        lanes.update(_LANES_BY_SEED["cve"])
    return sorted(lanes)


async def run_runtime_pivot_prelude(
    query: str,
    duckdb_store: "DuckDBShadowStore | None",
    nonfeed_diagnostic_active: bool,
    existing_findings: "list[dict] | None" = None,
) -> dict:
    """
    F223A: Runtime pivot prelude — extract seeds from query + DuckDB findings.

    Called BEFORE nonfeed acquisition lanes run, so that seed_context is
    available when build_lane_query() shapes lane queries.

    Args:
        query: The sprint query string.
        duckdb_store: DuckDBShadowStore instance (may be None).
        nonfeed_diagnostic_active: True if nonfeed_diagnostic profile is active.
        existing_findings: Optional list of finding dicts from current sprint
            result fields. Preferred source over DuckDB.

    Returns:
        dict with keys:
          - pivot_seed_domains: tuple[str, ...]
          - pivot_seed_ips: tuple[str, ...]
          - pivot_seed_urls: tuple[str, ...]
          - pivot_seed_hashes: tuple[str, ...]
          - pivot_seed_cves: tuple[str, ...]
          - seed_context_available: bool
          - seed_context_propagated: bool
          - lanes_unlocked_by_seed_context: list[str]
          - seed_context_skip_reason: str  # empty string = not skipped
    """
    result: dict = {
        "pivot_seed_domains": (),
        "pivot_seed_ips": (),
        "pivot_seed_urls": (),
        "pivot_seed_hashes": (),
        "pivot_seed_cves": (),
        "seed_context_available": False,
        "seed_context_propagated": False,
        "lanes_unlocked_by_seed_context": [],
        "seed_context_skip_reason": "",
        "seed_context_source": "",  # F227A: "query" | "duckdb" | "findings" | ""
    }

    if not nonfeed_diagnostic_active:
        result["seed_context_skip_reason"] = "profile_not_nonfeed_diagnostic"
        return result

    # Step 1: direct extraction from query
    query_seeds = extract_nonfeed_seeds_from_text(query, max_seeds=_MAX_SEEDS_FROM_QUERY)
    _domains_q: set[str] = set()
    _ips_q: set[str] = set()
    _urls_q: set[str] = set()
    _hashes_q: set[str] = set()
    _cves_q: set[str] = set()
    for s in query_seeds:
        if s.kind == "domain":
            _domains_q.add(s.value)
        elif s.kind == "ip":
            _ips_q.add(s.value)
        elif s.kind == "url":
            _urls_q.add(s.value)
        elif s.kind == "hash":
            _hashes_q.add(s.value)
        elif s.kind == "cve":
            _cves_q.add(s.value)

    # Step 2: for text queries, try existing_findings OR DuckDB
    if _is_text_query_without_direct_seeds(query):
        if existing_findings:
            findings_seeds = extract_nonfeed_seeds_from_findings(
                existing_findings, max_seeds=_MAX_SEEDS_FROM_FINDINGS
            )
            for s in findings_seeds:
                if s.kind == "domain":
                    _domains_q.add(s.value)
                elif s.kind == "ip":
                    _ips_q.add(s.value)
                elif s.kind == "url":
                    _urls_q.add(s.value)
                elif s.kind == "hash":
                    _hashes_q.add(s.value)
                elif s.kind == "cve":
                    _cves_q.add(s.value)
        elif duckdb_store is not None:
            try:
                loop = asyncio.get_running_loop()

                def _read() -> list[dict]:
                    import duckdb

                    conn = duckdb.connect(":memory:")
                    try:
                        conn.execute(
                            "CREATE TABLE IF NOT EXISTS cf ("
                            "  id VARCHAR, ts TIMESTAMP, query VARCHAR, "
                            "  title VARCHAR, payload_text VARCHAR"
                            ")"
                        )
                        try:
                            conn.execute(
                                "INSERT INTO cf "
                                "SELECT id, ts, query, title, payload_text "
                                "FROM shadow_findings "
                                "ORDER BY ts DESC LIMIT ?",
                                (_MAX_ROWS_FROM_DUCKDB,),
                            )
                        except Exception:
                            pass
                        rows = conn.execute(
                            "SELECT id, ts, query, title, payload_text "
                            "FROM cf "
                            "ORDER BY ts DESC LIMIT ?",
                            (_MAX_ROWS_FROM_DUCKDB,),
                        ).fetchall()
                        return [
                            {
                                "id": r[0],
                                "ts": r[1],
                                "query": r[2],
                                "title": r[3],
                                "payload_text": r[4],
                            }
                            for r in rows
                        ]
                    finally:
                        conn.close()

                rows = await loop.run_in_executor(None, _read)
                if rows:
                    findings_seeds = extract_nonfeed_seeds_from_findings(
                        rows, max_seeds=_MAX_SEEDS_FROM_FINDINGS
                    )
                    for s in findings_seeds:
                        if s.kind == "domain":
                            _domains_q.add(s.value)
                        elif s.kind == "ip":
                            _ips_q.add(s.value)
                        elif s.kind == "url":
                            _urls_q.add(s.value)
                        elif s.kind == "hash":
                            _hashes_q.add(s.value)
                        elif s.kind == "cve":
                            _cves_q.add(s.value)
            except Exception:
                result["seed_context_skip_reason"] = "duckdb_read_error"
                return result

    # F227A: Track which source contributed the seeds
    _seed_source = ""
    if _domains_q or _ips_q or _urls_q or _hashes_q or _cves_q:
        # Step 1 contributed: query had direct IOC seeds
        _step1_contributed = bool(
            extract_nonfeed_seeds_from_text(query, max_seeds=1)
        )
        if _step1_contributed:
            _seed_source = "query"
        elif existing_findings:
            _seed_source = "findings"
        elif duckdb_store is not None:
            _seed_source = "duckdb"
        else:
            _seed_source = "query"  # Default to query since step1 ran

    result["seed_context_source"] = _seed_source

    # Apply hard caps
    _dom_list = sorted(_domains_q)[:10]
    _ip_list = sorted(_ips_q)[:10]
    _url_list = sorted(_urls_q)[:10]
    _hash_list = sorted(_hashes_q)[:20]
    _cve_list = sorted(_cves_q)[:20]

    result["pivot_seed_domains"] = tuple(_dom_list)
    result["pivot_seed_ips"] = tuple(_ip_list)
    result["pivot_seed_urls"] = tuple(_url_list)
    result["pivot_seed_hashes"] = tuple(_hash_list)
    result["pivot_seed_cves"] = tuple(_cve_list)

    _has_seeds = bool(_dom_list or _ip_list or _url_list or _hash_list or _cve_list)
    result["seed_context_available"] = _has_seeds
    result["seed_context_propagated"] = _has_seeds

    if _has_seeds:
        result["lanes_unlocked_by_seed_context"] = _compute_lanes_unlocked(
            domains=result["pivot_seed_domains"],
            ips=result["pivot_seed_ips"],
            urls=result["pivot_seed_urls"],
            hashes=result["pivot_seed_hashes"],
            cves=result["pivot_seed_cves"],
        )
        result["seed_context_skip_reason"] = ""
    else:
        result["seed_context_skip_reason"] = "no_seeds_extracted"

    return result