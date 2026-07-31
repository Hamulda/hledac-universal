"""DuckDB Store Protocol — F360: Extracted DuckDBShadowStore boundary.

This module defines DuckDBStoreProtocol — the typed contract that all DuckDB
store implementations must satisfy. Enables Protocol-based dependency injection
so callers don't need to know whether they're talking to DuckDBShadowStore
(the current monolithic implementation) or future extracted components.

ARCHITECTURE (F360):
    duckdb_protocol.py  — Protocol (interface contract)
    duckdb_canonical.py — Canonical SQL store (findings, runs, deltas, etc.)
    duckdb_vector_store.py — HNSW vector operations (rag_embeddings, entity_embeddings)
    duckdb_wal_manager.py — WAL + LMDB lifecycle
    duckdb_quality_gate.py — Stateful quality assessment
    duckdb_analytics.py — Scorecard, FTS5, arrow metrics
    duckdb_store.py     — DuckDBShadowStore (monolithic, refactoring target)

vs duckdb_rag_store.py:
    duckdb_rag_store.py is a thin facade that delegates to duckdb_store.py.
    It is NOT a separate RAG engine — DuckDB HNSW lives in DuckDBShadowStore.
    After F360, duckdb_rag_store.py → duckdb_rag_facade.py (no logic change).

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

if TYPE_CHECKING:
    from pathlib import Path
    from ._quality_types import FindingQualityDecision


@runtime_checkable
class DuckDBStoreProtocol(Protocol):
    """
    Typed contract for DuckDB-backed canonical store.

    F360: DuckDBShadowStore is the current implementation (monolithic).
    Future extracted DuckDBCanonical will also satisfy this protocol.

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
        params: list[Any] | None = None,
        batch_size: int = 500,
    ) -> AsyncIterator[Any]:
        """Streaming batch query — yields lists of row dicts."""

    async def async_query_arrow_batches(
        self,
        sql: str,
        params: list[Any] | None = None,
        batch_size: int = 2048,
    ) -> AsyncIterator[Any]:
        """Streaming Arrow batch query — yields pyarrow.RecordBatch."""

    def iter_batches(
        self,
        sql: str,
        params: list[Any] | None = None,
        batch_size: int = 500,
    ) -> AsyncIterator[list[tuple]]:
        """Sync streaming batch query."""

    # ── FTS5 Search ──────────────────────────────────────────────

    async def fts_search_findings(
        self,
        query: str,
        k: int = 10,
        after_ts: float | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search over canonical_findings.payload_text using FTS5."""

    # ── Vector / RAG ─────────────────────────────────────────────

    async def upsert_rag_embeddings(self, chunks: list[dict[str, Any]]) -> int:
        """Batch upsert RAG document chunk embeddings (LIST<FLOAT> vectors)."""

    async def vector_search_rag(
        self,
        query_vector: list[float],
        k: int = 10,
        document_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        ANN vector search over rag_embeddings using DuckDB HNSW.

        Args:
            query_vector: 384-dim query embedding.
            k: Number of results (default 10, max 100, M1 8GB safe).
            document_id: Optional filter to specific document.

        Returns:
            List of dicts: {chunk_id, document_id, content, metadata_json, distance}
        """

    async def vector_search_rag_mmr(
        self,
        query_vector: list[float],
        k: int = 10,
        fetch_k: int = 20,
        lambda_: float = 0.5,
    ) -> list[dict[str, Any]]:
        """MMR (Maximal Marginal Relevance) reranked vector search."""

    async def hybrid_search_rag(
        self,
        query_text: str,
        query_vector: list[float],
        k: int = 10,
        fts_weight: float = 0.4,
        vec_weight: float = 0.6,
    ) -> list[dict[str, Any]]:
        """Hybrid FTS5 + vector ANN search with Reciprocal Rank Fusion."""

    async def upsert_entity_embeddings(self, entities: list[dict[str, Any]]) -> int:
        """Batch upsert entity alias/identity embeddings."""

    async def vector_search_entities(
        self,
        query_vector: list[float],
        k: int = 10,
        entity_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """ANN vector search over entity_embeddings for identity clustering."""

    # ── Analytics / Scorecard ───────────────────────────────────

    async def upsert_scorecard(self, scorecard: dict[str, Any]) -> bool:
        """Upsert sprint_scorecard row."""

    async def async_record_sprint_delta(self, delta: dict[str, Any]) -> bool:
        """Record sprint_delta metrics."""

    async def async_record_source_hit(
        self,
        sprint_id: str,
        ts: float,
        source_type: str,
        findings_count: int,
        ioc_count: int,
        hit_rate: float,
    ) -> bool:
        """Record per-sprint source attribution."""

    async def get_stats(self) -> dict[str, Any]:
        """Return aggregate store statistics."""

    # ── Graph Attachments ────────────────────────────────────────

    def inject_graph(self, graph: Any) -> None:
        """Attach DuckPGQGraph or IOCGraph for entity enrichment."""

    def get_graph_attachment_kind(self) -> str | None:
        """Return kind of attached graph or None."""

    async def get_connected_iocs(
        self,
        ioc_value: str,
        depth: int = 2,
        ioc_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Graph traversal: find IOC nodes connected to given IOC."""

    async def annotate_findings_with_graph_context(
        self, findings: list[Any]
    ) -> list[Any]:
        """Enrich finding list with graph-derived context (aliases, relationships)."""

    # ── Quality Gate ─────────────────────────────────────────────

    def get_quality_rejection_ledger(self) -> dict[str, int]:
        """Return quality rejection counts by reason code."""

    # ── WAL ───────────────────────────────────────────────────────

    def wal_manager(self) -> Any:  # WALManager
        """Return WALManager instance for this store."""

    # ── Maintenance ────────────────────────────────────────────────

    async def async_vacuum_if_needed(self) -> bool:
        """Run VACUUM if conditions are met (duckdb_vacuum feature flag)."""

    async def vacuum_async(self) -> None:
        """Force VACUUM regardless of conditions."""

    async def async_healthcheck(self) -> dict[str, Any]:
        """Return health status (connection, WAL, dedup, memory)."""

    # ── Target / Entity Memory ───────────────────────────────────

    async def upsert_target_memory(self, target_id: str, memory: dict[str, Any]) -> bool:
        """Upsert target_memory row."""

    async def async_get_target_memory(self, target_id: str) -> dict[str, Any] | None:
        """Fetch target memory by target_id."""

    async def upsert_episode(self, episode: dict[str, Any]) -> bool:
        """Upsert research_episode row."""

    # ── Research Sessions ────────────────────────────────────────

    async def async_record_research_session(
        self, session: dict[str, Any]
    ) -> bool:
        """Record research session memory."""

    # ── Hypothesis Tracking ────────────────────────────────────────

    async def async_record_hypothesis_tracking(
        self, hypothesis: dict[str, Any]
    ) -> bool:
        """Record hypothesis tracking row."""

    # ── Entity Observations ──────────────────────────────────────

    async def async_record_entity_observations_bulk(
        self, observations: list[dict[str, Any]]
    ) -> int:
        """Bulk record entity observations for temporal tracking."""

    # ── DHT Metadata ──────────────────────────────────────────────

    async def async_ingest_dht_metadata(self, metadata: dict[str, Any]) -> bool:
        """Ingest DHT torrent metadata (infohash, name, files, peers)."""

    # ── IOC Co-occurrence ────────────────────────────────────────

    async def async_ingest_cooccurrence_batch(
        self, cooccurrences: list[dict[str, Any]]
    ) -> int:
        """Bulk ingest IOC co-occurrence pairs for speculative edge mining."""

    # ── Target Profiles ────────────────────────────────────────────

    async def async_upsert_target_profile(self, profile: dict[str, Any]) -> bool:
        """Upsert target_profiles row."""


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
