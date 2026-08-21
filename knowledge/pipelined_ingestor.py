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
import logging
import threading
from typing import TYPE_CHECKING, Any

from hledac.universal.utils.asyncx import parallel

if TYPE_CHECKING:
    from .duckdb_store import DuckDBShadowStore
__all__ = ["PipelinedIngestor"]
logger = logging.getLogger(__name__)

# ISSUE-026: threading.local is CORRECT here — not contextvars.
# Thread-bound resource: one event loop per dedicated executor thread.
# Called on duckdb_arrow_executor thread — single async context, no sharing.
# See mlx_memory/_core.py for rationale on thread-local vs contextvars.
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


def _call_async_arrow_wrapper(store: Any, findings: list[Any]) -> list[Any]:
    """
    Sync wrapper for async_record_canonical_findings_batch_arrow.

    Called on duckdb_arrow_executor thread. Reuses a thread-local event loop
    instead of creating a new one per call — eliminates ~0.5-2ms overhead.
    """
    try:
        loop = _get_or_create_arrow_loop()
        result = loop.run_until_complete(store.async_record_canonical_findings_batch_arrow(findings))
        return result
    except Exception as e:
        logger.error("[PIPELINE arrow-wrapper] exception: %s", e)
        return []


CHUNK_SIZE = 1024
_PIPELINE_QUEUE_MAXSIZE = 2
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

    __slots__ = ("_store", "_wal")

    def __init__(self, duckdb_store: DuckDBShadowStore, wal_manager: Any | None = None) -> None:
        self._store = duckdb_store
        self._wal = wal_manager

    async def ingest(self, findings: list[Any]) -> list[Any]:
        """
        Ingest list[CanonicalFinding] přes paralelní WAL + DuckDB pipeline.

        Vrací list[ActivationResult | FindingQualityDecision] 1:1 s inputem.
        Prázdný input → okamžitý return [].
        """
        if not findings:
            return []
        return await self._ingest_pipeline(findings)

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
            q = _get_queue()
            if q.full():
                await q.join()

            async def _storage_task(batch_findings: list[Any], batch_indices: list[int], q_ref: asyncio.Queue) -> None:
                try:
                    stored = await self._write_batch_parallel(batch_findings)
                    for idx, result in zip(batch_indices, stored, strict=False):
                        results[idx] = result
                finally:
                    q_ref.task_done()

            await q.put(None)
            # F350M-R ISSUE #31: safe_create_task with eager_start=True (WAL Arrow storage is hot path)
            from hledac.universal.utils.asyncx import safe_create_task

            task = safe_create_task(
                _storage_task(chunk_findings, list(range(chunk_start, chunk_end)), q), eager_start=True
            )
            pending_tasks.append((list(range(chunk_start, chunk_end)), task))
        for _indices, task in pending_tasks:
            try:
                await task
            except Exception:
                logger.warning("[PIPELINE] pending storage task failed")
        return results

    async def _write_batch_parallel(self, findings: list[Any]) -> list[Any]:
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
        wal_future = loop.run_in_executor(self._store._wal_executor, self._wal_put_many_sync, findings)
        duckdb_future = loop.run_in_executor(
            self._store._duckdb_arrow_executor, _call_async_arrow_wrapper, self._store, findings
        )
        wal_ok_or_exc: bool | Any
        duckdb_result: list[Any] | Any
        _result = await parallel(
            [wal_future, duckdb_future], taskgroup=True, policy="collect", ctx="pipelined_ingestor:wal_duckdb"
        )
        gathered = _result.ok
        wal_ok_or_exc, duckdb_result = gathered
        if isinstance(wal_ok_or_exc, Exception):
            logger.warning("[PIPELINE] WAL executor raised %s, falling back to legacy", type(wal_ok_or_exc).__name__)
            return await self._legacy_ingest(findings)
        wal_ok: bool = wal_ok_or_exc
        if isinstance(duckdb_result, Exception):
            logger.warning("[PIPELINE] DuckDB executor raised %s, falling back to legacy", type(duckdb_result).__name__)
            return await self._legacy_ingest(findings)
        if not wal_ok:
            logger.warning(
                "[PIPELINE] WAL phase failed (wal_ok=False), falling back to legacy for %d findings", len(findings)
            )
            return await self._legacy_ingest(findings)
        if duckdb_result and isinstance(duckdb_result, list):
            return duckdb_result
        logger.warning(
            "[PIPELINE] DuckDB Arrow returned empty/invalid result, falling back to legacy for %d findings",
            len(findings),
        )
        return await self._legacy_ingest(findings)

    def _wal_put_many_sync(self, findings: list[Any]) -> bool:
        """Sync WAL write na wal_executor thread."""
        try:
            wal = self._wal
            if wal is None:
                return False
            items = [(f"finding:{f.finding_id}", self._fingerprint_payload(f)) for f in findings]
            if not items:
                return True
            # P0-3 Fix: wal_put_many returns list[bool]; check with all() not truthiness
            # bool([False, False]) = True (truthy list!) but all([False, False]) = False
            wal_results = wal.wal_put_many(items) if hasattr(wal, "wal_put_many") else False
            lmdb_ok = all(wal_results) if isinstance(wal_results, list) else bool(wal_results)
            if not lmdb_ok:
                logger.warning("[PIPELINE WAL] batch WAL failed for %d items", len(items))
            return lmdb_ok
        except Exception as e:
            logger.error("[PIPELINE WAL] exception: %s", e)
            return False

    def _duckdb_arrow_sync(self, findings: list[Any]) -> tuple[int, str | None]:
        """Sync DuckDB Arrow insert na duckdb_arrow_executor thread."""
        try:
            return self._store._sync_record_canonical_findings_batch_arrow(findings)
        except Exception as e:
            logger.error("[PIPELINE DuckDB] exception: %s", e)
            return (0, "pipeline_duckdb_exception")

    async def _legacy_ingest(self, findings: list[Any]) -> list[Any]:
        """Fail-open legacy fallback pres async_record_canonical_findings_batch."""
        try:
            return await self._store.async_record_canonical_findings_batch(findings)
        except Exception as e:
            logger.error("[PIPELINE] legacy fallback failed: %s", e)
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
