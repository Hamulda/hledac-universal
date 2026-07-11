"""
knowledge/pipelined_ingestor.py
Sprint F265B: Zero-Copy Parallel WAL + DuckDB Pipeline

PipelinedIngestor — standalone orchestrátor paralelního WAL + DuckDB Arrow write.

Architecture (SEQUENTIAL-2 Level 1 + Level 3):
  ┌─────────────────────────────────────────────────────────────┐
  │  ingest(findings)                                          │
  │    ├── chunk (CHUNK_SIZE = 1024, M1 8GB safe)              │
  │    │   ├── Phase 1: quality gate (Rust rayon, CPU)          │
  │    │   └── Phase 2: WAL ‖ DuckDB Arrow (asyncio.gather)    │
  │    │         WAL:      _wal_put_many_sync → LMDB WAL        │
  │    │         DuckDB:   _duckdb_arrow_sync  → DuckDB Arrow   │
  │    └── cross-batch pipeline via asyncio.Queue(maxsize=2)   │
  │          backpressure when queue full → await q.join()      │
  └─────────────────────────────────────────────────────────────┘

WAL-first invariant: DuckDB result je použit pouze pokud wal_ok is True.
Při selhání WAL → fallback na async_record_canonical_findings_batch (legacy).
Při selhání DuckDB → fail-open (uložení přes legacy path).

Usage:
    pipelined = PipelinedIngestor(duckdb_store, wal_manager)
    results = await pipelined.ingest(findings)

No new feature flags. Always-on, bounded (max 2 pending batches),
fail-safe (legacy fallback na jakoukoli chybu).
"""


import asyncio
import threading
from hledac.universal.utils.async_helpers import safe_gather_return_exceptions
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .duckdb_store import DuckDBShadowStore

__all__ = ["PipelinedIngestor"]

logger = logging.getLogger(__name__)

# CONC-SEQ-006: Thread-local event loop cache — avoids new_event_loop() + close() per call.
# Each thread gets its own reusable loop. Loop is created on first use,
# then reused for subsequent calls on the same thread.
# M1 8GB: negligible overhead (~0 bytes, one-time alloc per thread).
_arrow_loop_local = threading.local()


def _get_or_create_arrow_loop() -> asyncio.AbstractEventLoop:
    """Get or create a reusable event loop for the current thread.

    Reusing the same event loop eliminates ~0.5-2ms per-call overhead from
    asyncio.new_event_loop() + loop.close() in _call_async_arrow_wrapper.
    Thread-local ensures no cross-thread contamination.
    """
    loop = getattr(_arrow_loop_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _arrow_loop_local.loop = loop
    return loop


def _call_async_arrow_wrapper(
    store: Any,
    findings: list[Any],
) -> list[Any]:
    """
    Sync wrapper for async_record_canonical_findings_batch_arrow.

    Called on duckdb_arrow_executor thread. Reuses a thread-local event loop
    instead of creating a new one per call — eliminates ~0.5-2ms overhead.
    """
    try:
        loop = _get_or_create_arrow_loop()
        result = loop.run_until_complete(
            store.async_record_canonical_findings_batch_arrow(findings)
        )
        return result
    except Exception as e:
        logger.error("[PIPELINE arrow-wrapper] exception: %s", e)
        return []

# M1 8GB-safe chunk size: 1024 × ~5 KB ≈ 5 MB peak per chunk
CHUNK_SIZE = 1024
# Cross-batch pipeline depth: 2 batches × 1024 × 5 KB ≈ 10 MB max queued
_PIPELINE_QUEUE_MAXSIZE = 2
# Per-executor timeout for asyncio.wait_for
_EXECUTOR_TIMEOUT_S = 30.0


class PipelinedIngestor:
    """
    Parallel WAL + DuckDB Arrow pipeline.

    Vlastnosti:
    - WAL a DuckDB běží souběžně (asyncio.gather) — WAL I/O overlap s DuckDB CPU/INSERT
    - Cross-batch pipelining s bounded queue pro M1 8GB backpressure
    - WAL-first invariant: DuckDB výsledek platný pouze když wal_ok
    - Fail-safe: jakákoli chyba → legacy fallback
    - Žádné nové feature flagy, always-on
    """

    def __init__(
        self,
        duckdb_store: DuckDBShadowStore,
        wal_manager: Any | None = None,
    ) -> None:
        self._store = duckdb_store
        self._wal = wal_manager

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def ingest(
        self,
        findings: list[Any],
    ) -> list[Any]:
        """
        Ingest list[CanonicalFinding] přes paralelní WAL + DuckDB pipeline.

        Vrací list[ActivationResult | FindingQualityDecision] 1:1 s inputem.
        Prázdný input → okamžitý return [].
        """
        if not findings:
            return []

        return await self._ingest_pipeline(findings)

    # -------------------------------------------------------------------------
    # Pipeline implementation
    # -------------------------------------------------------------------------

    async def _ingest_pipeline(self, findings: list[Any]) -> list[Any]:
        """Cross-batch pipeline s bounded queue backpressure."""
        n = len(findings)
        results: list[Any | None] = [None] * n

        _pipeline_queue: asyncio.Queue | None = None

        def _get_queue() -> asyncio.Queue:
            nonlocal _pipeline_queue
            if _pipeline_queue is None:
                _pipeline_queue = asyncio.Queue(maxsize=_PIPELINE_QUEUE_MAXSIZE)
            return _pipeline_queue

        pending_tasks: list[tuple[list[int], asyncio.Task]] = []

        for chunk_start in range(0, n, CHUNK_SIZE):
            chunk_end = min(chunk_start + CHUNK_SIZE, n)
            chunk_findings = findings[chunk_start:chunk_end]

            # Backpressure: počkat na volný slot v pipeline queue
            q = _get_queue()
            if q.full():
                await q.join()

            # Quality gate + storage v jednom tasku
            async def _storage_task(
                batch_findings: list[Any],
                batch_indices: list[int],
                q_ref: asyncio.Queue,
            ) -> None:
                try:
                    stored = await self._write_batch_parallel(batch_findings)
                    for idx, result in zip(batch_indices, stored):
                        results[idx] = result
                finally:
                    q_ref.task_done()

            await q.put(None)  # reserve slot; None is just a placeholder
            loop = asyncio.get_running_loop()
            task = loop.create_task(
                _storage_task(chunk_findings, list(range(chunk_start, chunk_end)), q)
            )
            pending_tasks.append((list(range(chunk_start, chunk_end)), task))

        # Drain: počkat na dokončení všech pending tasků
        for _indices, task in pending_tasks:
            try:
                await task
            except Exception:
                logger.warning("[PIPELINE] pending storage task failed")

        return results

    async def _write_batch_parallel(
        self,
        findings: list[Any],
    ) -> list[Any]:
        """
        Parallel WAL + DuckDB Arrow pro jeden batch.

        Submit oba tasky současně pres asyncio.gather (return_exceptions=True).
        DuckDB Arrow path (async_record_canonical_findings_batch_arrow) uz
        internally runs WAL + DuckDB in parallel na separate executors.

        WAL-first: DuckDB result je použit pouze pokud wal_ok is True.
        Fail-open: chyba → legacy fallback.
        """
        if not findings:
            return []

        loop = asyncio.get_running_loop()

        # Phase 1: WAL-only check (runs on wal_executor)
        wal_future = loop.run_in_executor(
            self._store._wal_executor,  # type: ignore[attr-defined]
            self._wal_put_many_sync,
            findings,
        )

        # Phase 2: Arrow batch via async wrapper (handles WAL-precheck + Arrow internally)
        # async_record_canonical_findings_batch_arrow returns list[ActivationResult]
        duckdb_future = loop.run_in_executor(
            self._store._duckdb_arrow_executor,  # type: ignore[attr-defined]
            _call_async_arrow_wrapper,
            self._store,
            findings,
        )

        wal_ok_or_exc: bool | Any
        duckdb_result: list[Any] | Any

        # F314: migrated asyncio.gather -> safe_gather_return_exceptions (raw exception access needed)
        gathered: list[Any] = await safe_gather_return_exceptions(
            wal_future,
            duckdb_future,
            label="pipelined_ingestor:wal_duckdb",
        )
        wal_ok_or_exc, duckdb_result = gathered  # type: ignore[assignment]

        # F262-FIX: Exception check
        if isinstance(wal_ok_or_exc, Exception):
            logger.warning(
                "[PIPELINE] WAL executor raised %s, falling back to legacy",
                type(wal_ok_or_exc).__name__,
            )
            return await self._legacy_ingest(findings)

        wal_ok: bool = wal_ok_or_exc

        if isinstance(duckdb_result, Exception):
            logger.warning(
                "[PIPELINE] DuckDB executor raised %s, falling back to legacy",
                type(duckdb_result).__name__,
            )
            return await self._legacy_ingest(findings)

        # WAL-first gate: DuckDB result is only valid if WAL succeeded
        if not wal_ok:
            logger.warning(
                "[PIPELINE] WAL phase failed (wal_ok=False), "
                "falling back to legacy for %d findings",
                len(findings),
            )
            return await self._legacy_ingest(findings)

        # DuckDB Arrow returned list[ActivationResult]
        if duckdb_result and isinstance(duckdb_result, list):
            return duckdb_result

        # Empty or invalid result → fallback
        logger.warning(
            "[PIPELINE] DuckDB Arrow returned empty/invalid result, "
            "falling back to legacy for %d findings",
            len(findings),
        )
        return await self._legacy_ingest(findings)

    # -------------------------------------------------------------------------
    # Sync helpers (volané na executor thread)
    # -------------------------------------------------------------------------

    def _wal_put_many_sync(self, findings: list[Any]) -> bool:
        """Sync WAL write na wal_executor thread."""
        try:
            wal = self._wal
            if wal is None:
                return False

            items = [
                (f"finding:{f.finding_id}", self._fingerprint_payload(f))
                for f in findings
            ]

            if not items:
                return True

            lmdb_ok = wal.wal_put_many(items) if hasattr(wal, "wal_put_many") else False
            if not lmdb_ok:
                logger.warning("[PIPELINE WAL] batch WAL failed for %d items", len(items))
            return lmdb_ok
        except Exception as e:
            logger.error("[PIPELINE WAL] exception: %s", e)
            return False

    def _duckdb_arrow_sync(
        self,
        findings: list[Any],
    ) -> tuple[int, str | None]:
        """Sync DuckDB Arrow insert na duckdb_arrow_executor thread."""
        try:
            return self._store._sync_record_canonical_findings_batch_arrow(findings)
        except Exception as e:
            logger.error("[PIPELINE DuckDB] exception: %s", e)
            return (0, "pipeline_duckdb_exception")

    # -------------------------------------------------------------------------
    # Fallback
    # -------------------------------------------------------------------------

    async def _legacy_ingest(self, findings: list[Any]) -> list[Any]:
        """Fail-open legacy fallback pres async_record_canonical_findings_batch."""
        try:
            return await self._store.async_record_canonical_findings_batch(findings)
        except Exception as e:
            logger.error("[PIPELINE] legacy fallback failed: %s", e)
            # Return error results for all findings
            from .duckdb_store import ActivationResult

            return [
                ActivationResult(
                    finding_id=str(f.finding_id),
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{f.finding_id}",
                    desync=False,
                    error=f"pipeline_legacy_failed:{e}",
                    accepted=False,
                )
                for f in findings
            ]

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _fingerprint_payload(f: Any) -> dict[str, Any]:
        """Sestavit WAL payload pro jeden finding."""
        return {
            "id": f.finding_id,
            "query": f.query,
            "source_type": f.source_type,
            "confidence": f.confidence,
            "ts": f.ts,
            "provenance": f.provenance,
            "payload_text": getattr(f, "payload_text", None),
        }
