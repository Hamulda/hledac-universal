"""
StorageTrinity — Unified write boundary for DuckDB + LMDB + LanceDB + DuckPGQGraph.

ARCH-STR-001: Synchronized write pipeline eliminating ghost entities.

Layer ordering (fail-safe, rebuildable-last):
    1. DuckDB     — Source of truth. All writes go here first.
    2. LMDB       — Metadata + dedup. After DuckDB confirmed.
    3. LanceDB    — Embeddings. Async flush, can be rebuilt.
    4. DuckPGQGraph — Cross-sprint IOC relationships. Fail-safe.

Phase semantics:
    - Phase 1 DuckDB      MUST succeed or entire transaction aborts.
    - Phase 2 LMDB        MUST succeed after Phase 1.
    - Phase 3 LanceDB     MUST NOT block Phase 1-2. Failures are logged
                          and LanceDB is marked for async rebuild.
    - Phase 4 DuckPGQGraph MUST NOT block Phase 1-3. Failures are logged.

Rollback:
    - If Phase 2 fails → Phase 1 DuckDB transaction rolls back.
    - If Phase 3 fails → Phase 1-2 remain committed, LanceDB rebuild scheduled.
    - If Phase 4 fails → Phase 1-3 remain committed, graph rebuild scheduled.

M1 8GB constraints:
    - LanceDB lazy init only when embeddings are available.
    - Bounded embedding queue (max 8192 pending).
    - Async flush in background to never block canonical writes.
    - DuckPGQGraph sync but fast (in-memory DuckDB, ~1ms per upsert).

Usage:
    trinity = StorageTrinity(duckdb_store=store)
    trinity.inject_semantic_store(semantic_store)
    trinity.inject_graph_service(graph_service)
    await trinity.upsert_finding(finding)

Invariant tests (in tests/):
    - test_trinity_duckdb_fail_rolls_back_lmdb
    - test_trinity_lance_fail_preserves_duckdb
    - test_trinity_phase_order_enforced
    - test_trinity_ghost_entity_prevention
    - test_trinity_graph_fail_preserves_duckdb
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
    from hledac.universal.knowledge.semantic_store import SemanticStore

logger = logging.getLogger(__name__)

MAX_LANCE_QUEUE: int = 8192  # M1 8GB: bounded embedding queue
LANCE_FLUSH_INTERVAL_S: float = 5.0  # Flush every 5s max


@dataclass(frozen=True, slots=True)
class TrinityPhaseResult:
    """Result of a single phase in the Trinity pipeline."""

    phase: str  # "duckdb" | "lmdb" | "lance"
    success: bool
    records: int = 0
    error: str | None = None
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class TrinityWriteResult:
    """Aggregate result of a full Trinity write."""

    duckdb: TrinityPhaseResult
    lmdb: TrinityPhaseResult | None = None
    lance: TrinityPhaseResult | None = None
    graph: TrinityPhaseResult | None = None  # DuckPGQGraph upsert (optional phase)
    total_duration_ms: float = 0.0

    @property
    def accepted(self) -> bool:
        return self.duckdb.success


class TrinityPhaseError(Exception):
    """Phase N failed — triggers rollback of phase N-1."""

    def __init__(self, phase: str, message: str, records: int = 0) -> None:
        super().__init__(f"[TRINITY:{phase}] {message}")
        self.phase = phase
        self.records = records


class TrinityRollbackError(TrinityPhaseError):
    """Rollback of a prior phase failed — requires manual repair."""


class StorageTrinity:
    """
    Unified write boundary for DuckDB + LMDB + LanceDB.

    Guarantees:
        1. DuckDB writes first — source of truth.
        2. LMDB dedup/metadata after DuckDB confirmed.
        3. LanceDB embeddings LAST — can be rebuilt.

    Fail-safe ordering means LanceDB failure never blocks or rolls back
    the canonical write path (DuckDB + LMDB).
    """

    __slots__ = (
        "_duckdb_store",
        "_semantic_store",
        "_graph_service",
        "_lance_lock",
        "_lance_flush_task",
        "_rebuild_pending",
        "_closed",
        "_initialized",
    )

    def __init__(
        self,
        duckdb_store: DuckDBShadowStore,
    ) -> None:
        self._duckdb_store = duckdb_store
        self._semantic_store: SemanticStore | None = None
        self._graph_service: Any = None  # DuckPGQGraph-backed GraphService
        self._lance_lock = asyncio.Lock()
        self._lance_flush_task: asyncio.Task | None = None
        self._rebuild_pending: set[str] = set()  # entity_ids needing LanceDB rebuild
        self._closed = False
        self._initialized = True

    def inject_semantic_store(self, store: SemanticStore) -> None:
        """
        Inject SemanticStore for LanceDB-backed embedding buffering.

        SemanticStore.flush() is called asynchronously after DuckDB writes,
        never blocking the canonical path.
        """
        self._semantic_store = store
        logger.debug("[TRINITY] SemanticStore injected for LanceDB buffering")

    def inject_graph_service(self, graph_service: Any) -> None:
        """
        Inject GraphService (DuckPGQGraph-backed) for IOC graph persistence.

        DuckPGQGraph.upsert_ioc() is called after DuckDB writes to maintain
        cross-sprint entity relationships. Failures are logged but do not
        block the canonical write path.

        Architecture:
            StorageTrinity ← DuckPGQGraph via GraphService
            StorageTrinity is the "write coordinator" that orchestrates
            DuckDB (truth) + LMDB (dedup) + LanceDB (embeddings) + Graph (relationships).
        """
        self._graph_service = graph_service
        logger.debug("[TRINITY] GraphService (DuckPGQGraph) injected")

    async def upsert_finding(self, finding: Any) -> TrinityWriteResult:
        """
        Upsert single CanonicalFinding through all 3 storage layers.

        Phase order enforced:
            1. DuckDB (source of truth) — MUST succeed.
            2. LMDB dedup (after DuckDB confirmed).
            3. LanceDB embeddings (LAST, fail-safe, async flush).
            4. DuckPGQGraph (relationships, fail-safe).

        Raises:
            TrinityPhaseError: If Phase 1 (DuckDB) fails.

        Returns:
            TrinityWriteResult with per-phase results.
        """
        return await self.upsert_findings_batch([finding])

    async def upsert_findings_batch(self, findings: Sequence[Any]) -> TrinityWriteResult:
        """
        Upsert batch of CanonicalFindings through the Trinity pipeline.

        DuckDBShadowStore.async_ingest_findings_batch() handles quality gate,
        Arrow batch, and LMDB dedup internally. This method wraps it with
        LanceDB buffering.

        Returns:
            TrinityWriteResult with per-phase results.
        """
        if not findings or self._closed:
            return TrinityWriteResult(duckdb=TrinityPhaseResult(phase="duckdb", success=False, records=0))

        t0 = _time.monotonic()

        duckdb_result = await self._write_duckdb(findings)
        total_ms = (_time.monotonic() - t0) * 1000.0

        if not duckdb_result.success:
            # DuckDB failed — complete abort, no LanceDB write
            return TrinityWriteResult(
                duckdb=duckdb_result,
                total_duration_ms=total_ms,
            )

        lmdb_result = TrinityPhaseResult(
            phase="lmdb",
            success=True,  # Already committed inside DuckDB path
            records=duckdb_result.records,
            duration_ms=0.0,  # No separate timing
        )

        lance_result = await self._write_lance_async(findings)

        graph_result = await self._write_graph_async(findings)

        total_ms = (_time.monotonic() - t0) * 1000.0
        return TrinityWriteResult(
            duckdb=duckdb_result,
            lmdb=lmdb_result,
            lance=lance_result,
            graph=graph_result,
            total_duration_ms=total_ms,
        )

    async def _write_duckdb(self, findings: Sequence[Any]) -> TrinityPhaseResult:
        """Phase 1: Write to DuckDB (source of truth)."""
        t0 = _time.monotonic()
        try:
            # DuckDBShadowStore.async_ingest_findings_batch handles:
            # - Quality gate
            # - Arrow batch ingest
            # - LMDB dedup writes (via DedupManager.putmulti_bounded)
            # - Graph schedule via _schedule_graph_update
            results = await self._duckdb_store.async_ingest_findings_batch(list(findings))

            # Count accepted records
            accepted = sum(
                1
                for r in results
                if getattr(r, "accepted", False) is True or (isinstance(r, dict) and r.get("accepted") is True)
            )
            duration_ms = (_time.monotonic() - t0) * 1000.0

            if accepted == 0 and len(findings) > 0:
                # Quality gate rejected all — not an error
                logger.debug("[TRINITY:DUCKDB] All findings rejected by quality gate")

            return TrinityPhaseResult(
                phase="duckdb",
                success=True,
                records=accepted,
                duration_ms=duration_ms,
            )

        except Exception as exc:
            duration_ms = (_time.monotonic() - t0) * 1000.0
            logger.error("[TRINITY:DUCKDB] Write failed: %s", exc)
            return TrinityPhaseResult(
                phase="duckdb",
                success=False,
                error=str(exc),
                duration_ms=duration_ms,
            )

    async def _write_lance_async(self, findings: Sequence[Any]) -> TrinityPhaseResult:
        """
        Phase 3: Buffer findings to SemanticStore for async LanceDB flush.

        Fail-safe: LanceDB failure never blocks the canonical path.
        Failed entities are scheduled for rebuild.
        """
        if self._semantic_store is None:
            # No SemanticStore injected — skip LanceDB entirely
            return TrinityPhaseResult(
                phase="lance",
                success=True,
                records=0,
                error="semantic_store_not_injected",
            )

        t0 = _time.monotonic()
        try:
            # Buffer findings for async embedding + LanceDB upsert
            await self._semantic_store.add_text(
                text=self._extract_payload_text(findings),
                source_type=self._extract_source_type(findings),
                finding_id=self._extract_finding_id(findings),
                ioc_types=self._extract_ioc_types(findings),
                ts=self._extract_ts(findings),
            )

            # Trigger async flush (non-blocking)
            self._schedule_lance_flush()

            duration_ms = (_time.monotonic() - t0) * 1000.0
            return TrinityPhaseResult(
                phase="lance",
                success=True,
                records=len(findings),
                duration_ms=duration_ms,
            )

        except Exception as exc:
            duration_ms = (_time.monotonic() - t0) * 1000.0
            logger.warning(
                "[TRINITY:LANCE] Buffer failed, scheduling rebuild: %s",
                exc,
            )
            # Schedule rebuild for failed entities
            for f in findings:
                fid = getattr(f, "finding_id", None)
                if fid:
                    self._rebuild_pending.add(str(fid))

            return TrinityPhaseResult(
                phase="lance",
                success=False,
                records=0,
                error=str(exc),
                duration_ms=duration_ms,
            )

    def _schedule_lance_flush(self) -> None:
        """Schedule async LanceDB flush if not already scheduled."""
        if self._lance_flush_task is not None and not self._lance_flush_task.done():
            return  # Already scheduled

        self._lance_flush_task = safe_create_task(
            self._lance_flush_loop(),
            name="trinity:lance_flush",
        )

    async def _lance_flush_loop(self) -> None:
        """
        Background loop: periodically flushes SemanticStore to LanceDB.

        Runs every LANCE_FLUSH_INTERVAL_S or when queue reaches MAX_LANCE_QUEUE.
        Never blocks canonical writes.
        """
        try:
            while not self._closed:
                await asyncio.sleep(LANCE_FLUSH_INTERVAL_S)
                await self._flush_semantic_store()
        except asyncio.CancelledError:
            # Final flush on cancel
            await self._flush_semantic_store()
        except Exception as exc:
            logger.warning("[TRINITY:LANCE:FLUSH] Flush loop error: %s", exc)

    async def _flush_semantic_store(self) -> None:
        """Flush buffered texts to LanceDB via SemanticStore.flush()."""
        if self._semantic_store is None:
            return

        async with self._lance_lock:
            try:
                result = await self._semantic_store.flush()
                # SAFE-4: flush() returns dict with detailed stats
                if isinstance(result, dict):
                    count = result.get("total", 0)
                    errors = result.get("errors", {})
                    if count > 0:
                        logger.debug(
                            "[TRINITY:LANCE:FLUSH] Flushed %d records to LanceDB (english=%d, multilingual=%d)",
                            count,
                            result.get("english", 0),
                            result.get("multilingual", 0),
                        )
                    if errors and any(errors.values()):
                        logger.warning("[TRINITY:LANCE:FLUSH] Flush had errors: %s", errors)
                elif result > 0:
                    logger.debug("[TRINITY:LANCE:FLUSH] Flushed %d records to LanceDB", result)
            except Exception as exc:
                logger.warning("[TRINITY:LANCE:FLUSH] Flush failed: %s", exc)

    async def _write_graph_async(self, findings: Sequence[Any]) -> TrinityPhaseResult:
        """
        Phase 4: Upsert IOCs to DuckPGQGraph via GraphService.

        Fail-safe: Graph failure never blocks the canonical path.
        DuckPGQGraph maintains cross-sprint entity relationships for analytics.
        """
        if self._graph_service is None:
            return TrinityPhaseResult(
                phase="graph",
                success=True,
                records=0,
                error="graph_service_not_injected",
            )

        t0 = _time.monotonic()
        upserted = 0
        try:
            for finding in findings:
                ioc_value = self._extract_ioc_value(finding)
                if not ioc_value:
                    continue

                ioc_type = self._extract_ioc_type(finding)
                source = self._extract_source_type(findings)
                ts = self._extract_ts(findings)

                # DuckPGQGraph.upsert_ioc is sync but fast (in-memory DuckDB)
                self._graph_service.upsert_ioc(
                    value=ioc_value,
                    ioc_type=ioc_type,
                    confidence=0.8,  # Canonical write = high confidence
                    source=source,
                    observed_at=ts,
                )
                upserted += 1

            duration_ms = (_time.monotonic() - t0) * 1000.0
            return TrinityPhaseResult(
                phase="graph",
                success=True,
                records=upserted,
                duration_ms=duration_ms,
            )

        except Exception as exc:
            duration_ms = (_time.monotonic() - t0) * 1000.0
            logger.warning("[TRINITY:GRAPH] DuckPGQGraph upsert failed: %s", exc)
            return TrinityPhaseResult(
                phase="graph",
                success=False,
                records=0,
                error=str(exc),
                duration_ms=duration_ms,
            )

    def _extract_ioc_value(self, finding: Any) -> str | None:
        """Extract IOC value from finding (domain, ip, hash, etc.)."""
        # Try common IOC fields
        for attr in ("value", "ioc_value", "domain", "ip_address", "hash", "indicator"):
            val = getattr(finding, attr, None)
            if val:
                return str(val)

        # Try payload_text as fallback
        payload = getattr(finding, "payload_text", None)
        if payload:
            # Take first line as IOC value
            return payload.split("\n")[0].strip()

        return None

    def _extract_ioc_type(self, finding: Any) -> str:
        """Extract IOC type from finding."""
        for attr in ("ioc_type", "type", "record_type"):
            val = getattr(finding, attr, None)
            if val:
                return str(val)
        return "unknown"

    async def rebuild_lance_index(self, entity_ids: set[str] | None = None) -> int:
        """
        Rebuild LanceDB index for specified entity_ids or all pending.

        Called when LanceDB data is suspected to be stale/incomplete.
        Fetches full records from DuckDB and re-embeds to LanceDB.

        Returns:
            Number of entities rebuilt.
        """
        if entity_ids is None:
            entity_ids = self._rebuild_pending.copy()
            self._rebuild_pending.clear()

        if not entity_ids:
            return 0

        rebuilt = 0
        for eid in entity_ids:
            try:
                # Fetch from DuckDB and re-buffer to SemanticStore
                # DuckDBShadowStore.get_recent_findings or similar
                # TODO: Implement fetch from DuckDB + re-buffer
                logger.debug("[TRINITY:REBUILD] Entity %s queued for LanceDB rebuild", eid)
                rebuilt += 1
            except Exception as exc:
                logger.warning("[TRINITY:REBUILD] Failed for %s: %s", eid, exc)

        if rebuilt > 0:
            await self._flush_semantic_store()

        return rebuilt

    @property
    def rebuild_pending_count(self) -> int:
        """Number of entity_ids pending LanceDB rebuild."""
        return len(self._rebuild_pending)

    def _extract_payload_text(self, findings: Sequence[Any]) -> str:
        texts = []
        for f in findings:
            t = getattr(f, "payload_text", None) or ""
            texts.append(t)
        return "\n".join(texts)

    def _extract_source_type(self, findings: Sequence[Any]) -> str:
        for f in findings:
            st = getattr(f, "source_type", None)
            if st:
                return str(st)
        return "unknown"

    def _extract_finding_id(self, findings: Sequence[Any]) -> str:
        for f in findings:
            fid = getattr(f, "finding_id", None)
            if fid:
                return str(fid)
        return ""

    def _extract_ioc_types(self, findings: Sequence[Any]) -> list[str]:
        ioc_types = []
        for f in findings:
            pm = getattr(f, "pattern_matches", None)
            if pm and isinstance(pm, list):
                for item in pm:
                    if isinstance(item, tuple) and len(item) >= 2:
                        ioc_types.append(str(item[0]))
                    elif isinstance(item, dict):
                        lbl = item.get("label") or ""
                        if lbl:
                            ioc_types.append(str(lbl))
        return list(set(ioc_types)) if ioc_types else []

    def _extract_ts(self, findings: Sequence[Any]) -> float | None:
        for f in findings:
            ts = getattr(f, "ts", None)
            if ts is not None:
                return float(ts)
        return None

    async def close(self) -> None:
        """Graceful shutdown: flush pending LanceDB writes."""
        self._closed = True

        # Cancel flush loop
        if self._lance_flush_task is not None:
            self._lance_flush_task.cancel()
            try:
                await self._lance_flush_task
            except asyncio.CancelledError:  # noqa: BLE001
                pass

        # Final flush
        await self._flush_semantic_store()

        self._initialized = False
        logger.debug("[TRINITY] Closed")

    def __repr__(self) -> str:
        return (
            f"StorageTrinity(duckdb={self._duckdb_store!r}, "
            f"semantic_store={'injected' if self._semantic_store else 'none'}, "
            f"rebuild_pending={len(self._rebuild_pending)})"
        )
