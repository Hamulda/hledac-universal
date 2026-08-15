"""DuckDB Store Protocol — F360: Extracted DuckDBShadowStore boundary.

This module defines DuckDBStoreProtocol — the typed contract that all DuckDB
store implementations must satisfy. Enables Protocol-based dependency injection
so callers don't need to know whether they're talking to DuckDBShadowStore
(the current monolithic implementation) or future extracted components.

ARCHITECTURE (F360 Phase 4+):
    duckdb_protocol.py       — Protocols (interface contracts)
    duckdb_arrow_builder.py  — Arrow batch building with fallbacks
    duckdb_quality_gate.py   — Quality gate (extracted)
    duckdb_wal_manager.py    — WAL management (extracted)
    duckdb_vector_store.py   — Vector operations (extracted)
    duckdb_graph_attachment.py — Graph attachment (extracted)
    query_executor.py       — SQL query execution (extracted)
    duckdb_store.py          — DuckDBShadowStore (composition root)

LAYER RESPONSIBILITIES:
    Layer 1 (duckdb_arrow_builder.py): Arrow batch building with fallbacks
    Layer 2 (duckdb_store.py): WAL, dedup, quality gate, graph, vector (composed)

STORAGE TRINITY (CLAUDE.md):
    Layer    | Tech        | Purpose
    ---------|-------------|------------------------------
    DuckDB   | SQL         | Canonical findings (this protocol)
    LMDB     | Key-value   | Entity/claim metadata
    LanceDB  | ANN         | RAG embeddings  ← DEPRECATED; DuckDB HNSW used
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from _core import aclose

if TYPE_CHECKING:
    from pathlib import Path
    from ._quality_types import FindingQualityDecision


@runtime_checkable
class DuckDBStoreProtocol(Protocol):
    """
    Typed contract for DuckDB-backed canonical store.

    F360: DuckDBShadowStore is the current implementation (composition root).
    Extracted components satisfy this protocol via duckdb_protocol.py.

    ═══════════════════════════════════════════════════════════════════
    ═ LIFECYCLE
    ═══════════════════════════════════════════════════════════════════
    """

    async def async_initialize(self) -> bool:
        """Initialize DuckDB connection, WAL replay, dedup bootstrap."""

    async def async_initialize_schema(self) -> bool:
        """Create/touch DB file and run CREATE TABLE IF NOT EXISTS."""

    async def aclose(self) -> None:
        """Graceful shutdown — closes connections, flushes WAL."""

    def close(self) -> None:
        """Sync close — for use outside async context."""

    def ensure_connected(self) -> None:
        """Ensure DuckDB connection is established (lazy init)."""

    # ── Properties ────────────────────────────────────────────────

    @property
    def db_path(self) -> Path | None:
        """DB file path or None for in-memory mode."""

    @property
    def is_initialized(self) -> bool:
        """True after async_initialize() has run successfully."""

    @property
    def is_closed(self) -> bool:
        """True after close() / aclose() has been called."""

    @property
    def startup_ready(self) -> Any:  # asyncio.Event
        """Event set when store is ready for queries."""

    # ── Canonical Write (Primary Path) ────────────────────────────

    async def async_ingest_findings_batch(
        self,
        findings: list[Any],
        flush_min: int = ...,
        flush_max_age: float = ...,
    ) -> dict[str, Any]:
        """
        PRIMARY canonical write path.

        Full pipeline:
          1. Quality assessment (stateful gate)
          2. WAL write (LMDB, before DuckDB)
          3. Dedup check (bloom filter)
          4. Arrow batch build
          5. DuckDB INSERT (with circuit breaker)
          6. Graph upsert (async, best-effort)
          7. Return {accepted, rejected, duplicates}

        Args:
            findings: List of CanonicalFinding objects.
            flush_min: Min batch size before flush.
            flush_max_age: Max age before flush.

        Returns:
            dict: {accepted: int, rejected: int, duplicates: int, ...}
        """

    # ── Canonical Read ────────────────────────────────────────────

    async def async_query_recent_findings(
        self,
        query: str,
        limit: int = 100,
        after_ts: float | None = None,
    ) -> list[Any]:
        """Query canonical_findings by keyword."""

    async def iter_batches_async(
        self,
        sql: str,
        batch_size: int = 1024,
        params: list[Any] | None = None,
    ) -> AsyncIterator[list[Any]]:
        """Iterate query results in batches (for large result sets)."""

    # ── Sprint Analytics ─────────────────────────────────────────

    async def async_record_sprint_delta(self, delta: dict[str, Any]) -> bool:
        """Record sprint_delta row."""

    async def async_record_sprint_scorecard(self, scorecard: dict[str, Any]) -> bool:
        """Record sprint_scorecard row."""

    async def async_record_research_episode(self, episode: dict[str, Any]) -> bool:
        """Record research_episodes row."""

    # ── Graph Operations ─────────────────────────────────────────

    def get_graph_stats(self) -> dict[str, Any]:
        """Return graph statistics."""

    def inject_graph(self, graph: Any) -> None:
        """Inject DuckPGQGraph or IOCGraph for entity enrichment."""

    # ── Target Profiles ───────────────────────────────────────────

    async def async_upsert_target_profile(self, profile: dict[str, Any]) -> bool:
        """Upsert target_profiles row."""


# ─────────────────────────────────────────────────────────────────────────────
# DuckDBArrowBuilder Protocol — F360 Phase 4
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class DuckDBArrowBuilderProtocol(Protocol):
    """
    Typed contract for DuckDBArrowBuilder — Arrow batch building.
    
    F360 Phase 4: DuckDBArrowBuilder handles Arrow IPC batch construction
    from CanonicalFinding objects with multiple fallback paths.
    """

    def build_arrow_batch(
        self,
        findings: list[Any],
    ) -> tuple[Any | None, Any]:  # (arrow_bytes_or_table, status)
        """Build Arrow batch from findings."""

    def get_metrics(self) -> dict[str, int]:
        """Return arrow metrics."""


# ─────────────────────────────────────────────────────────────────────────────
# DedupManager Protocol — F360 Phase 2
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class DedupManagerProtocol(Protocol):
    """
    Typed contract for DedupManager — enables Protocol-based DI in DuckDBShadowStore.

    F360 Phase 2: DuckDBShadowStore.__init__ accepts DedupManagerProtocol | None,
    with lazy init fallback. This reduces CBO by making DedupManager an injected
    dependency rather than a direct instantiation.

    DuckDBShadowStore calls these methods on _dedup_manager:
      - add_ioc_batch(iocs)            — IOC bloom filter + LMDB
      - store_persistent_dedup_batch   — batch persistent dedup storage
      - lookup_persistent_dedup        — LMDB lookup
      - semantic_dedup_cache           — property (SemanticDedupCache or None)
      - hot_cache_lookup              — in-process LRU lookup
      - add_to_hot_cache              — add to LRU cache
      - is_duplicate_ioc_batch        — batch IOC dedup check
      - get_runtime_status             — dedup subsystem status
      - close                          — graceful shutdown
    """

    def add_ioc_batch(self, iocs: list[Any]) -> None:
        """Add IOC batch to bloom filter and persistent store."""

    def store_persistent_dedup_batch(
        self, fingerprints: list[tuple[str, str]]
    ) -> None:
        """Store fingerprint → finding_id mappings in LMDB."""

    def lookup_persistent_dedup(self, fingerprint: str) -> str | None:
        """Lookup finding_id by fingerprint from persistent LMDB."""

    @property
    def semantic_dedup_cache(self) -> Any:
        """Return SemanticDedupCache instance or None."""

    def hot_cache_lookup(self, fingerprint: str) -> str | None:
        """In-process LRU cache lookup."""

    def add_to_hot_cache(self, fingerprint: str, finding_id: str) -> None:
        """Add fingerprint → finding_id to hot cache."""

    def is_duplicate_ioc_batch(
        self, iocs: list[Any]
    ) -> tuple[set[str], list[dict[str, Any]]]:
        """
        Check batch for duplicate IOCs.

        Returns (duplicate_iocs, new_iocs) where duplicate_iocs is set of
        duplicate IOC values and new_iocs is list of non-duplicate IOC dicts.
        """

    def get_runtime_status(self) -> dict[str, Any]:
        """Return dedup subsystem status (bloom, LMDB, semantic, hot cache)."""

    def close(self) -> None:
        """Close all dedup subsystems (LMDB, bloom filter, mmap stores)."""


# ─────────────────────────────────────────────────────────────────────────────
# QualityGate Protocol — F360 Phase 3
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class QualityGateProtocol(Protocol):
    """
    Typed contract for DuckDBQualityGate — enables Protocol-based DI in DuckDBShadowStore.

    F360 Phase 3: DuckDBShadowStore.__init__ accepts QualityGateProtocol | None,
    with lazy init fallback. This reduces CBO by making DuckDBQualityGate an
    injected dependency rather than a direct instantiation.

    DuckDBShadowStore calls these methods on _quality_gate:
      - _assess_finding_quality(finding) → FindingQualityDecision

    Note: _quality_state (QualityAssessmentState from quality_assessment.py) is
    a separate state object owned by duckdb_store itself, NOT injected.
    """

    def _assess_finding_quality(self, finding: Any) -> "FindingQualityDecision":
        """Apply quality rules to a single finding. Returns FindingQualityDecision."""
