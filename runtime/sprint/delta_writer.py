"""
runtime/sprint/delta_writer.py — Sprint delta JSON serialization

F350M-R: Handles writing sprint_delta records to DuckDB at TEARDOWN.

Usage:
    await write_sprint_delta(
        store=ctx.store,
        sprint_id=ctx.sprint_id,
        query=query,
        new_findings=result.accepted_findings,
        ...
    )
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

logger = logging.getLogger(__name__)


def _derive_top_source(hits_per_source: dict[str, int]) -> str:
    """Return source with most hits, or empty string if no data."""
    if not hits_per_source:
        return ""
    return max(hits_per_source, key=lambda k: hits_per_source[k])


async def write_sprint_delta(
    store: DuckDBShadowStore,
    sprint_id: str,
    query: str,
    new_findings: int,
    dedup_hits: int,
    ioc_nodes: int,
    uma_baseline_gib: float,
    uma_peak_gib: float,
    synthesis_success: bool,
    duration_s: float,
    hits_per_source: dict[str, int],
    seed_state: Any = None,
) -> None:
    """
    Write sprint_delta record to DuckDB at TEARDOWN.

    Args:
        store: DuckDBShadowStore instance
        sprint_id: Unique sprint identifier
        query: Sprint query string
        new_findings: Number of new findings
        dedup_hits: Number of deduplication hits
        ioc_nodes: Number of IOC nodes
        uma_baseline_gib: UMA memory baseline at sprint start
        uma_peak_gib: Peak UMA memory during sprint
        synthesis_success: Whether synthesis succeeded
        duration_s: Actual sprint duration
        hits_per_source: Dictionary of source -> hit count
        seed_state: Optional seed state for deterministic replay
    """
    try:
        findings_per_min = new_findings / (duration_s / 60.0) if duration_s > 0 else 0.0
        top_source = _derive_top_source(hits_per_source)

        row = {
            "sprint_id": sprint_id,
            "ts": time.time(),
            "query": query,
            "duration_s": duration_s,
            "new_findings": new_findings,
            "dedup_hits": dedup_hits,
            "ioc_nodes": ioc_nodes,
            "ioc_new_this_sprint": new_findings,
            "uma_peak_gib": uma_peak_gib - uma_baseline_gib,
            "synthesis_success": synthesis_success,
            "findings_per_minute": findings_per_min,
            "top_source_type": top_source,
            "synthesis_confidence": 1.0 if synthesis_success else 0.0,
        }

        if seed_state is not None:
            row["prng_seed"] = seed_state.prng_seed
            row["tot_iv"] = seed_state.tot_iv
            row["config_hash"] = seed_state.config_hash
            row["seed_created_at"] = seed_state.created_at

        if not await store.wait_until_ready(timeout_s=20.0):
            logger.info("[TEARDOWN] DuckDB store not ready after 20s timeout — recording anyway (fail-safe)")

        await store.async_record_sprint_delta(row)

        logger.info(
            f"[TEARDOWN] sprint_delta written: {new_findings} findings, {dedup_hits} dedup hits, "
            f"UMA delta: {uma_peak_gib - uma_baseline_gib:+.2f}GiB, "
            f"top_source: {top_source!r}, findings_per_min: {findings_per_min:.2f}"
        )
    except Exception as exc:
        logger.warning(f"[TEARDOWN] sprint_delta write failed: {exc}")
