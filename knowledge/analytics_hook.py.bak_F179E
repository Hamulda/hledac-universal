"""
Shadow Analytics Hook — Sprint 8AX
==================================

Non-blocking, fail-open shadow ingest of findings into DuckDB.

HOOK LOCATION
-------------
Called from EvidenceLog.append() after a finding event is already in the log.
The hook is entirely opt-in via GHOST_DUCKDB_SHADOW=1.

DESIGN
------
- duckdb is NOT imported on boot — deferred to first use inside _ShadowRecorder
- Feature flag is cached at module level after first check
- Bounded asyncio.Queue(maxsize=200) — put_nowait only, drop on full
- Shadow failures are logged as WARNING, never propagate
- aclose() attempts final flush with 2s timeout, then gives up cleanly

PRODUCTION PATH
---------------
GHOST_DUCKDB_SHADOW=1 + RAMDISK_ACTIVE=False:
  → DB_ROOT / "analytics.duckdb" (file-backed, durable)

:memory: FALLBACK
----------------
Used only when:
  1. DB_ROOT is unavailable (degraded), OR
  2. Explicitly requested in tests
Session-only persistence expected — not treated as a bug.

SCHEMA (mirrors duckdb_store.py)
--------------------------------
shadow_findings: id, run_id, query, url, title, source,
                 source_type, relevance_score, confidence, ts

ADAPTER SHAPE
-------------
{
    "id": finding_id,
    "run_id": run_id,
    "query": query,
    "url": url or None,
    "title": title or None,
    "source": source or None,
    "source_type": source_type,
    "relevance_score": relevance_score or None,
    "confidence": confidence or 0.0,
}

DEFERRED ITEMS
--------------
- Entity ingest (future sprint)
- Kuzu sync bridge (future sprint)
- LanceDB/vector ingest (future sprint)
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature flag — cached after first access, never re-checked
# ---------------------------------------------------------------------------

_SHADOW_ENABLED: Optional[bool] = None


def _is_shadow_enabled() -> bool:
    """Check GHOST_DUCKDB_SHADOW flag with cached result."""
    global _SHADOW_ENABLED
    if _SHADOW_ENABLED is None:
        _SHADOW_ENABLED = os.environ.get("GHOST_DUCKDB_SHADOW", "0") == "1"
    return _SHADOW_ENABLED


# ---------------------------------------------------------------------------
# Shadow recording queue
# ---------------------------------------------------------------------------

_MAX_QUEUE_SIZE: int = 200
_SHADOW_INGEST_FAILURES: int = 0
_QUEUE_FULL_WARNED: bool = False


class _ShadowRecorder:
    """
    Non-blocking shadow recorder using a bounded async queue.

    All public methods are fail-open:
    - Queue full → drop record, increment _SHADOW_INGEST_FAILURES, WARN once
    - DuckDB error → drop record, increment counter, WARN once
    - Not enabled → zero-op
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._store: Optional[Any] = None  # DuckDBShadowStore, lazy
        self._worker_started: bool = False
        self._worker_lock: threading.Lock = threading.Lock()
        self._closed: bool = False
        self._flush_failures: int = 0

    def _ensure_worker(self) -> None:
        """
        Start background worker if not yet started (thread-safe once).

        Prevents false-start: _worker_started is set ONLY after confirmed
        running loop and successful task creation. If no loop exists,
        the flag remains False so subsequent enqueue() retries.
        """
        if self._worker_started:
            return
        with self._worker_lock:
            if self._worker_started:
                return
            # Defensively check loop BEFORE setting _worker_started.
            # This prevents the false-start state where the flag is True
            # but no worker task exists (RuntimeError swallowed).
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running loop — do NOT set _worker_started.
                # enqueue() will retry _ensure_worker() on next call
                # when a loop is available. The enqueued item stays in queue
                # and put_nowait failure (RuntimeError) increments counter.
                return
            self._worker_started = True
            loop.create_task(self._worker())

    def enqueue(self, record: Dict[str, Any]) -> None:
        """
        Enqueue a finding record for shadow ingest.

        Non-blocking, fail-open:
        - Closed recorder → drop record, increment failure counter
        - Queue full → drop record, increment failure counter
        - No running loop → drop record, increment failure counter
        """
        global _SHADOW_INGEST_FAILURES, _QUEUE_FULL_WARNED

        if not _is_shadow_enabled():
            return

        # Guard: closed recorder does not accept new work.
        # Records enqueued after aclose() would be silently dropped
        # without this guard, since the worker exits immediately.
        if self._closed:
            _SHADOW_INGEST_FAILURES += 1
            return

        try:
            self._queue.put_nowait(record)
            if not self._worker_started:
                self._ensure_worker()
        except asyncio.QueueFull:
            _SHADOW_INGEST_FAILURES += 1
            if not _QUEUE_FULL_WARNED:
                logger.warning(
                    f"[SHADOW] queue full ({_MAX_QUEUE_SIZE}), dropping record. "
                    f"Total drops: {_SHADOW_INGEST_FAILURES}"
                )
                _QUEUE_FULL_WARNED = True
        except RuntimeError:
            # No running event loop — record stays in queue but worker
            # cannot be started without a loop. Counter tracks drops.
            _SHADOW_INGEST_FAILURES += 1

    async def _worker(self) -> None:
        """
        Background worker that drains the queue and writes batches to DuckDB.

        Runs on the duckdb_worker thread via run_in_executor for each batch.
        """
        if self._closed:
            return

        # Lazy import of DuckDBShadowStore
        if self._store is None:
            try:
                from .duckdb_store import DuckDBShadowStore
                self._store = DuckDBShadowStore()
                initialized = await self._store.async_initialize()
                if not initialized:
                    logger.warning("[SHADOW] DuckDBShadowStore async_initialize failed")
                    self._store = None
                    return
            except Exception as e:
                logger.warning(f"[SHADOW] failed to initialize store: {e}")
                self._store = None
                return

        batch: List[Dict[str, Any]] = []
        last_flush = time.monotonic()
        flush_interval = 1.0  # flush every second max

        while not self._closed:
            try:
                # Wait for next item with timeout
                item = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=flush_interval
                )
                batch.append(item)

                # Flush when batch full or timeout
                if len(batch) >= 500 or \
                   (batch and (time.monotonic() - last_flush) >= flush_interval):
                    await self._flush_batch(batch)
                    batch = []
                    last_flush = time.monotonic()

            except asyncio.TimeoutError:
                # Flush any pending batch on timeout
                if batch:
                    await self._flush_batch(batch)
                    batch = []
                    last_flush = time.monotonic()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[SHADOW] worker error: {e}")

        # Final flush on shutdown
        if batch and self._store is not None:
            try:
                await asyncio.wait_for(
                    self._store.async_record_shadow_findings_batch(batch),
                    timeout=2.0
                )
            except Exception as e:
                logger.warning(f"[SHADOW] final flush failed: {e}")

    async def _flush_batch(self, batch: List[Dict[str, Any]]) -> None:
        """Flush a batch of records to DuckDB via the store."""
        if not batch or self._store is None:
            return

        try:
            inserted = await self._store.async_record_shadow_findings_batch(
                batch,
                max_batch_size=500
            )
            if inserted < len(batch):
                logger.warning(
                    f"[SHADOW] partial insert: {inserted}/{len(batch)} records"
                )
        except Exception as e:
            global _SHADOW_INGEST_FAILURES
            _SHADOW_INGEST_FAILURES += len(batch)
            logger.warning(f"[SHADOW] batch insert failed ({len(batch)} records): {e}")

    async def aclose(self, timeout: float = 2.0) -> None:
        """
        Async shutdown — drains pending queue, attempts final flush, then gives up.

        Timeout is per-batch, not total.

        If the worker never actually started (store is None), any items sitting
        in the queue at this point are drained and counted as drops — they would
        otherwise be silently lost.
        """
        global _SHADOW_INGEST_FAILURES

        if self._closed:
            return
        self._closed = True

        # Drain any remaining items from the queue so they are not silently lost.
        # This is safe even if the worker is still running — it will exit its loop
        # because _closed is now True and its queue.get() will raise CancelledError
        # or it will drain the queue we are draining here (queue is unbounded drain).
        # Items already taken by the worker before we set _closed=True will be flushed
        # by the worker's own final-flush path.
        drained: List[Dict[str, Any]] = []
        while True:
            try:
                drained.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if drained:
            if self._store is not None:
                try:
                    await asyncio.wait_for(
                        self._store.async_record_shadow_findings_batch(drained),
                        timeout=timeout
                    )
                except Exception as e:
                    _SHADOW_INGEST_FAILURES += len(drained)
                    logger.warning(f"[SHADOW] final flush of {len(drained)} drained records failed: {e}")
            else:
                # Store never initialized — drained items are lost, count them
                _SHADOW_INGEST_FAILURES += len(drained)
                logger.warning(
                    f"[SHADOW] store was never initialized, "
                    f"{len(drained)} drained records lost"
                )

        if self._store is not None:
            try:
                await asyncio.wait_for(self._store.aclose(), timeout=timeout)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_shadow_recorder: Optional[_ShadowRecorder] = None


def _get_recorder() -> _ShadowRecorder:
    """Get or create the module-level shadow recorder."""
    global _shadow_recorder
    if _shadow_recorder is None:
        _shadow_recorder = _ShadowRecorder()
    return _shadow_recorder


# ---------------------------------------------------------------------------
# Public adapter API
# ---------------------------------------------------------------------------

def shadow_record_finding(
    finding_id: str,
    query: str,
    source_type: str,
    confidence: float,
    run_id: Optional[str] = None,
    url: Optional[str] = None,
    title: Optional[str] = None,
    source: Optional[str] = None,
    relevance_score: Optional[float] = None,
    branch_id: Optional[str] = None,
    provider_id: Optional[str] = None,
    action_id: Optional[str] = None,
) -> None:
    """
    Non-blocking shadow record for a finding.

    This is the hot-path entry point called from EvidenceLog.append().

    Adapter shape:
    {
        "id": finding_id,
        "run_id": run_id,
        "query": query,
        "url": url or None,
        "title": title or None,
        "source": source or None,
        "source_type": source_type,
        "relevance_score": relevance_score or None,
        "confidence": confidence or 0.0,
        "branch_id": branch_id or None,
        "provider_id": provider_id or None,
        "action_id": action_id or None,
    }

    Fail-open: never raises, never blocks the caller.
    """
    if not _is_shadow_enabled():
        return

    record: Dict[str, Any] = {
        "id": finding_id,
        "run_id": run_id,
        "query": query,
        "url": url,
        "title": title,
        "source": source,
        "source_type": source_type,
        "relevance_score": relevance_score,
        "confidence": confidence if confidence is not None else 0.0,
        "branch_id": branch_id,
        "provider_id": provider_id,
        "action_id": action_id,
    }

    try:
        _get_recorder().enqueue(record)
    except Exception:
        global _SHADOW_INGEST_FAILURES
        _SHADOW_INGEST_FAILURES += 1


async def shadow_aclose() -> None:
    """Async shutdown of the shadow recorder with final flush."""
    global _shadow_recorder
    if _shadow_recorder is not None:
        await _shadow_recorder.aclose(timeout=2.0)
        _shadow_recorder = None


def shadow_ingest_failures() -> int:
    """Return the count of dropped shadow records."""
    return _SHADOW_INGEST_FAILURES


def shadow_reset_failures() -> None:
    """Reset the failure counter (for tests)."""
    global _SHADOW_INGEST_FAILURES, _QUEUE_FULL_WARNED
    _SHADOW_INGEST_FAILURES = 0
    _QUEUE_FULL_WARNED = False
