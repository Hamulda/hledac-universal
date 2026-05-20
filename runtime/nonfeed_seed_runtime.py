"""
runtime/nonfeed_seed_runtime.py — F223A: Runtime Pivot Prelude from Findings/DuckDB
==============================================================================

Bounded runtime pivot prelude for nonfeed_diagnostic profiles.

When a text threat query arrives and the nonfeed_diagnostic profile is active,
this module extracts seeds from recent DuckDB findings BEFORE nonfeed lanes run.
The extracted seeds (domains/IPs/URLs/hashes/CVEs) are threaded into
SprintResult pivot_seed_* fields so build_lane_query() can shape lane queries.

F241B: Seed quality gate wired — classify_seed_quality() applied to all
extracted seeds. deep_osint_m1 profile: only "keep" seeds unlock lanes.
nonfeed_diagnostic: all seeds kept for diagnostics but quality telemetry surfaced.
deep_osint_m1 with DROP seed: no lane unlock (seed_context_available=False).

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
    SeedQuality,
    classify_seed_quality,
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

# F241B: Profile flag for deep_osint_m1 — passed from caller to avoid
# a circular import from acquisition_strategy. Set by sprint_scheduler
# before calling run_runtime_pivot_prelude.
_ACQUISITION_PROFILE: str = "default"


def _should_allow_low_quality_seed_for_profile() -> bool:
    """
    F241B: Return True if low-quality / DROP seeds should still be used
    for lane unlock. Only true for nonfeed_diagnostic profile.

    deep_osint_m1: False — DROP seeds must not unlock CT/DOH/WAYBACK/PASSIVE_DNS.
    nonfeed_diagnostic: True — all seeds kept for diagnostic purposes, but
        quality telemetry is surfaced so operators can see what was filtered.
    default: False — conservative default.
    """
    return _ACQUISITION_PROFILE == "nonfeed_diagnostic"


def _classify_and_filter_seeds(
    all_seeds: list,  # list of NonfeedSeed
    query: str = "",
) -> tuple[list, list, dict, list, int]:
    """
    F241B: Apply classify_seed_quality() to all seeds.

    Returns (kept_seeds, dropped_seeds, drop_reasons_histogram,
            kept_by_quality, drop_by_quality_count).
    For deep_osint_m1 profile: DROP/weak seeds are excluded from kept_seeds
    so they cannot unlock lanes. For nonfeed_diagnostic: all seeds kept
    in kept_seeds for pivot but drop decisions are still tracked in
    drop_by_quality_count.

    Bounds:
        max samples: 10 each (kept/dropped)
        drop_reasons_histogram: at most 20 unique reasons
    """
    kept: list = []
    dropped: list = []
    drop_reasons: dict[str, int] = {}
    # Track quality decisions separately from pivot inclusion
    kept_by_quality: list = []  # seeds that PASSED the quality gate
    drop_by_quality_count: int = 0  # seeds that FAILED the quality gate (even if bypassed)
    context_text = query  # query used as context for classify_seed_quality

    for seed in all_seeds:
        q = classify_seed_quality(seed, query=context_text, context="")
        if q.decision == "keep":
            kept.append(seed)
            kept_by_quality.append(seed)
        elif q.decision == "weak":
            # weak seeds: keep for nonfeed_diagnostic, drop for deep_osint_m1
            if _should_allow_low_quality_seed_for_profile():
                kept.append(seed)  # diagnostic bypass: include in pivot
            else:
                dropped.append(seed)
                reason_key = q.reason or "weak_seed"
                drop_reasons[reason_key] = drop_reasons.get(reason_key, 0) + 1
                drop_by_quality_count += 1
        else:  # drop
            drop_by_quality_count += 1
            reason_key = q.reason or "dropped"
            drop_reasons[reason_key] = drop_reasons.get(reason_key, 0) + 1
            # F241B: nonfeed_diagnostic bypasses quality gate — all seeds kept for diagnostics
            if _should_allow_low_quality_seed_for_profile():
                kept.append(seed)  # diagnostic bypass: include dropped seed in pivot
            else:
                dropped.append(seed)

    # Bounded sampling
    return (
        kept[:10],
        dropped[:10],
        dict(list(drop_reasons.items())[:20]),
        kept_by_quality[:10],
        drop_by_quality_count,
    )


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
    acquisition_profile: str = "default",
) -> dict:
    """
    F223A: Runtime pivot prelude — extract seeds from query + DuckDB findings.
    F233D: Also activated for deep_osint_m1 staged research profile.
    F241B: Seed quality gate — classify_seed_quality() applied to all seeds.
        deep_osint_m1: only "keep" seeds unlock lanes.
        nonfeed_diagnostic: all seeds kept for diagnostics, quality telemetry surfaced.
        other profiles: only "keep" seeds unlock lanes.

    Called BEFORE nonfeed acquisition lanes run, so that seed_context is
    available when build_lane_query() shapes lane queries.

    Args:
        query: The sprint query string.
        duckdb_store: DuckDBShadowStore instance (may be None).
        nonfeed_diagnostic_active: True if nonfeed_diagnostic or deep_osint_m1 profile is active.
        existing_findings: Optional list of finding dicts from current sprint
            result fields. Preferred source over DuckDB.
        acquisition_profile: Profile name for quality gate decisions (F241B).
            Values: "deep_osint_m1", "nonfeed_diagnostic", "default".

    Returns:
        dict with keys:
          - pivot_seed_domains: tuple[str, ...]      # QUALITY-GATED: only kept seeds
          - pivot_seed_ips: tuple[str, ...]          # QUALITY-GATED
          - pivot_seed_urls: tuple[str, ...]          # QUALITY-GATED
          - pivot_seed_hashes: tuple[str, ...]        # QUALITY-GATED
          - pivot_seed_cves: tuple[str, ...]          # QUALITY-GATED
          - seed_context_available: bool               # False if all seeds dropped by quality
          - seed_context_propagated: bool
          - lanes_unlocked_by_seed_context: list[str] # computed from kept seeds only
          - seed_context_skip_reason: str              # empty string = not skipped
          - seed_context_source: str                   # F227A
          - seed_quality_checked: bool                # F241B: True when quality gate ran
          - seed_quality_keep_count: int              # F241B: count of kept seeds
          - seed_quality_drop_count: int              # F241B: count of dropped seeds
          - seed_quality_drop_reasons: dict           # F241B: reason→count histogram
          - seed_quality_kept_sample: list             # F241B: up to 10 kept values
          - seed_quality_dropped_sample: list         # F241B: up to 10 dropped values
          - seed_quality_bypass_reason: str            # F241B: "diagnostic_profile" or ""
    """
    # F241B: Set the module-level profile for _should_allow_low_quality_seed_for_profile
    global _ACQUISITION_PROFILE
    _ACQUISITION_PROFILE = acquisition_profile

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
        # F241B: seed quality telemetry (always present, defaults for non-active profiles)
        "seed_quality_checked": False,
        "seed_quality_keep_count": 0,
        "seed_quality_drop_count": 0,
        "seed_quality_drop_reasons": {},
        "seed_quality_kept_sample": [],
        "seed_quality_dropped_sample": [],
        "seed_quality_bypass_reason": "",
    }

    if not nonfeed_diagnostic_active:
        result["seed_context_skip_reason"] = "profile_not_nonfeed_diagnostic_or_deep_osint"
        return result

    # Collect ALL extracted seeds (before quality gate) for classification
    _all_seeds: list = []
    _domains_q: set[str] = set()
    _ips_q: set[str] = set()
    _urls_q: set[str] = set()
    _hashes_q: set[str] = set()
    _cves_q: set[str] = set()

    # Step 1: direct extraction from query
    query_seeds = extract_nonfeed_seeds_from_text(query, max_seeds=_MAX_SEEDS_FROM_QUERY)
    _all_seeds.extend(query_seeds)
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
            _all_seeds.extend(findings_seeds)
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
                # F251A: Use duckdb_store async method instead of inline :memory: connect.
                # Search shadow_findings for rows matching the text query keyword.
                # extract_nonfeed_seeds_from_findings() scans payload_text/title/query fields.
                rows = await duckdb_store.async_query_findings_by_text(
                    like_pattern=query,
                    limit=_MAX_ROWS_FROM_DUCKDB,
                )
                # F251A: Explicit row-count check — no rows means offline memory had no seeds.
                # Set skip reason so planner preserves diagnostic action (extract_more_seeds_from_duckdb).
                if not rows:
                    result["seed_context_skip_reason"] = "offline_memory_no_seeds"
                    return result
                if rows:
                    findings_seeds = extract_nonfeed_seeds_from_findings(
                        rows, max_seeds=_MAX_SEEDS_FROM_FINDINGS
                    )
                    _all_seeds.extend(findings_seeds)
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
            _seed_source = "query"
    result["seed_context_source"] = _seed_source

    # F241B: Apply seed quality gate — classify and filter seeds
    # Returns: (kept_seeds, dropped_seeds, drop_reasons, kept_by_quality, drop_count)
    _kept_seeds, _dropped_seeds, _drop_reasons, _kept_by_quality, _drop_by_quality_count = _classify_and_filter_seeds(
        _all_seeds, query=query
    )

    # Rebuild domain/ip/url/hash/cve sets from KEEPT seeds only
    _kept_domains: set[str] = set()
    _kept_ips: set[str] = set()
    _kept_urls: set[str] = set()
    _kept_hashes: set[str] = set()
    _kept_cves: set[str] = set()
    for s in _kept_seeds:
        if s.kind == "domain":
            _kept_domains.add(s.value)
        elif s.kind == "ip":
            _kept_ips.add(s.value)
        elif s.kind == "url":
            _kept_urls.add(s.value)
        elif s.kind == "hash":
            _kept_hashes.add(s.value)
        elif s.kind == "cve":
            _kept_cves.add(s.value)

    # F241B: Build quality telemetry
    # _drop_by_quality_count: seeds that FAILED the quality gate (regardless of bypass)
    # _kept_by_quality: seeds that PASSED the quality gate
    _dropped_values = [s.value for s in _dropped_seeds]
    _kept_values = [_s.value for _s in _kept_by_quality]  # only quality-passed seeds

    result["seed_quality_checked"] = True
    result["seed_quality_keep_count"] = _drop_by_quality_count + len(_kept_by_quality)  # total classified
    result["seed_quality_drop_count"] = _drop_by_quality_count
    result["seed_quality_drop_reasons"] = _drop_reasons
    result["seed_quality_kept_sample"] = _kept_values[:10]
    result["seed_quality_dropped_sample"] = _dropped_values[:10]
    if _should_allow_low_quality_seed_for_profile():
        result["seed_quality_bypass_reason"] = "diagnostic_profile"
    else:
        result["seed_quality_bypass_reason"] = ""

    # Apply hard caps from KEPT seeds only
    _dom_list = sorted(_kept_domains)[:10]
    _ip_list = sorted(_kept_ips)[:10]
    _url_list = sorted(_kept_urls)[:10]
    _hash_list = sorted(_kept_hashes)[:20]
    _cve_list = sorted(_kept_cves)[:20]

    result["pivot_seed_domains"] = tuple(_dom_list)
    result["pivot_seed_ips"] = tuple(_ip_list)
    result["pivot_seed_urls"] = tuple(_url_list)
    result["pivot_seed_hashes"] = tuple(_hash_list)
    result["pivot_seed_cves"] = tuple(_cve_list)

    # F241B: seed_context_available reflects QUALITY-GATED seed availability
    # If all seeds were dropped, no lanes should be unlocked
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
        # F241B: all seeds were dropped by quality gate — no lane unlock
        result["lanes_unlocked_by_seed_context"] = []
        result["seed_context_skip_reason"] = "all_seeds_dropped_by_quality"

    return result