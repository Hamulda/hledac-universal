"""
Shadow Analytics Hook — CANONICAL FINDING → FACTS PIPELINE
==========================================================


ROLE: Non-blocking pipeline stage that forwards finding metadata from the
EvidenceLog (ledger) to DuckDBShadowStore (sprint facts).

This module is NOT a writer authority — it is a write-path adapter.
The canonical sprint facts authority is DuckDBShadowStore (Tier 1 sprint facts).
This hook is the forwarding seam (analytics path only) from EvidenceLog.

LEDGER → FACTS boundary:
    EvidenceLog.append()  →  analytics_hook.shadow_record_finding()  →  DuckDBShadowStore.async_record_shadow_findings_batch()  # noqa: E501

The EvidenceLog remains the canonical EVIDENCE LEDGER.
DuckDBShadowStore holds CANONICAL SPRINT FACTS (sprint_delta, scorecard, hit_log).
analytics_hook bridges the two without owning either.

⚠️  "Shadow" in the hook name refers to the analytics/shadow path, not to DuckDBShadowStore being a shadow.
    DuckDBShadowStore is the canonical sprint facts store, not a shadow.

FACTS HIERARCHY (3 tiers):
--------------------------
TIER 1 — SPRINT FACTS (DuckDBShadowStore):
    sprint_delta, sprint_scorecard, source_hit_log
TIER 2 — SHADOW FINDINGS (DuckDBShadowStore):
    shadow_findings, shadow_runs
TIER 3 — GRAPH (injected):
    IOCGraph (Kuzu), SemanticStore (LanceDB)

ADAPTER SHAPE (fingerprint of evidence_packet payload for DuckDB):
------------------------------------------------------------------
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
    "branch_id": branch_id or None,     # from _correlation
    "provider_id": provider_id or None,   # from _correlation
    "action_id": action_id or None,      # from _correlation
}

DESIGN:
-------
- duckdb is NOT imported on boot — deferred to first use inside _ShadowRecorder
- Feature flag is cached at module level after first check
- Bounded asyncio.Queue(maxsize=200) — put_nowait only, drop on full
- Shadow failures are logged as WARNING, never propagate
- aclose() attempts final flush with 2s timeout, then gives up cleanly

:memory: FALLBACK
----------------
Used only when:
  1. DB_ROOT is unavailable (degraded), OR
  2. Explicitly requested in tests
Session-only persistence expected — not treated as a bug.
"""
import asyncio
import logging
import os
import threading
import time
from typing import Any
logger = logging.getLogger(__name__)
_SHADOW_ENABLED: bool | None = None

def _is_shadow_enabled() -> bool:
    """Check GHOST_DUCKDB_SHADOW flag with cached result."""
    global _SHADOW_ENABLED
    if _SHADOW_ENABLED is None:
        _SHADOW_ENABLED = os.environ.get('GHOST_DUCKDB_SHADOW', '0') == '1'
    return _SHADOW_ENABLED
_MAX_QUEUE_SIZE: int = 200
_SHADOW_BATCH_SIZE: int = 500
_SHADOW_FLUSH_INTERVAL: float = 1.0
_SHADOW_INGEST_FAILURES: int = 0
_QUEUE_FULL_WARNED: bool = False
_SHADOW_FAILURES_AT_LAST_FLUSH: int = 0
_SHADOW_ALERT_THRESHOLD: int = 10

class _ShadowRecorder:
    """
    Non-blocking shadow recorder using a bounded async queue.

    All public methods are fail-open:
    - Queue full → drop record, increment _SHADOW_INGEST_FAILURES, WARN once
    - DuckDB error → drop record, increment counter, WARN once
    - Not enabled → zero-op
    """
    __slots__ = tuple(('_closed', '_flush_failures', '_queue', '_store', '_worker_lock', '_worker_started'))

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._store: Any | None = None
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
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._worker_started = True
            # F350M-R ISSUE #31: safe_create_task with eager_start=True (analytics worker is hot path)
            from hledac.universal.utils.async_helpers import safe_create_task
            safe_create_task(self._worker(), name='analytics_hook.worker', eager_start=True)

    def enqueue(self, record: dict[str, Any]) -> None:
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
                logger.warning(f'[SHADOW] queue full ({_MAX_QUEUE_SIZE}), dropping record. Total drops: {_SHADOW_INGEST_FAILURES}')
                _QUEUE_FULL_WARNED = True
        except RuntimeError:
            _SHADOW_INGEST_FAILURES += 1

    async def _worker(self) -> None:
        """
        Background worker that drains the queue and writes batches to DuckDB.

        Runs on the duckdb_worker thread via run_in_executor for each batch.
        """
        if self._closed:
            return
        if self._store is None:
            try:
                from .duckdb_store import DuckDBShadowStore
                self._store = DuckDBShadowStore()
                initialized = await self._store.async_initialize()
                if not initialized:
                    logger.warning('[SHADOW] DuckDBShadowStore async_initialize failed')
                    self._store = None
                    return
            except Exception as e:
                logger.warning(f'[SHADOW] failed to initialize store: {e}')
                self._store = None
                return
        batch: list[dict[str, Any]] = []
        last_flush = time.monotonic()
        while not self._closed:
            try:
                async with asyncio.timeout(_SHADOW_FLUSH_INTERVAL):
                    item = await self._queue.get()
                batch.append(item)
                if len(batch) >= _SHADOW_BATCH_SIZE or (batch and time.monotonic() - last_flush >= _SHADOW_FLUSH_INTERVAL):
                    await self._flush_batch(batch)
                    batch = []
                    last_flush = time.monotonic()
            except TimeoutError:
                if batch:
                    await self._flush_batch(batch)
                    batch = []
                    last_flush = time.monotonic()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f'[SHADOW] worker error: {e}')
        if batch and self._store is not None:
            try:
                async with asyncio.timeout(2.0):
                    await self._store.async_record_shadow_findings_batch(batch)
            except Exception as e:
                logger.warning(f'[SHADOW] final flush failed: {e}')

    async def _flush_batch(self, batch: list[dict[str, Any]]) -> None:
        """Flush a batch of records to DuckDB via the store."""
        if not batch or self._store is None:
            return
        try:
            inserted = await self._store.async_record_shadow_findings_batch(batch, max_batch_size=_SHADOW_BATCH_SIZE)
            if inserted < len(batch):
                logger.warning(f'[SHADOW] partial insert: {inserted}/{len(batch)} records')
        except Exception as e:
            global _SHADOW_INGEST_FAILURES
            _SHADOW_INGEST_FAILURES += len(batch)
            logger.warning(f'[SHADOW] batch insert failed ({len(batch)} records): {e}')

    async def aclose(self, timeout: float=2.0) -> None:
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
        drained: list[dict[str, Any]] = []
        while True:
            try:
                drained.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if drained:
            if self._store is not None:
                try:
                    async with asyncio.timeout(timeout):
                        await self._store.async_record_shadow_findings_batch(drained)
                except Exception as e:
                    _SHADOW_INGEST_FAILURES += len(drained)
                    logger.warning(f'[SHADOW] final flush of {len(drained)} drained records failed: {e}')
            else:
                _SHADOW_INGEST_FAILURES += len(drained)
                logger.warning(f'[SHADOW] store was never initialized, {len(drained)} drained records lost')
        if self._store is not None:
            try:
                async with asyncio.timeout(timeout):
                    await asyncio.shield(self._store.aclose())
            except Exception:
                pass
_shadow_recorder: _ShadowRecorder | None = None

def _get_recorder() -> _ShadowRecorder:
    """Get or create the module-level shadow recorder."""
    global _shadow_recorder
    if _shadow_recorder is None:
        _shadow_recorder = _ShadowRecorder()
    return _shadow_recorder

def shadow_record_finding(finding_id: str, query: str, source_type: str, confidence: float, run_id: str | None=None, url: str | None=None, title: str | None=None, source: str | None=None, relevance_score: float | None=None, branch_id: str | None=None, provider_id: str | None=None, action_id: str | None=None) -> None:
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
    record: dict[str, Any] = {'id': finding_id, 'run_id': run_id, 'query': query, 'url': url, 'title': title, 'source': source, 'source_type': source_type, 'relevance_score': relevance_score, 'confidence': confidence if confidence is not None else 0.0, 'branch_id': branch_id, 'provider_id': provider_id, 'action_id': action_id}
    try:
        _get_recorder().enqueue(record)
    except Exception:
        global _SHADOW_INGEST_FAILURES
        _SHADOW_INGEST_FAILURES += 1

async def shadow_aclose() -> None:
    """Async shutdown of the shadow recorder with final flush."""
    global _shadow_recorder, _SHADOW_INGEST_FAILURES, _SHADOW_FAILURES_AT_LAST_FLUSH
    _emit_shadow_telemetry(_SHADOW_INGEST_FAILURES)
    if _shadow_recorder is not None:
        await _shadow_recorder.aclose(timeout=2.0)
        _shadow_recorder = None
    _SHADOW_FAILURES_AT_LAST_FLUSH = _SHADOW_INGEST_FAILURES

def shadow_ingest_failures() -> int:
    """Return the count of dropped shadow records."""
    return _SHADOW_INGEST_FAILURES

def shadow_reset_failures() -> None:
    """Reset the failure counter (for tests)."""
    global _SHADOW_INGEST_FAILURES, _QUEUE_FULL_WARNED
    _SHADOW_INGEST_FAILURES = 0
    _QUEUE_FULL_WARNED = False

def _emit_shadow_telemetry(failure_count: int | None=None) -> None:
    """Emit shadow analytics telemetry via OTel span attrs + gauge metric.

    LP-1 fix: _SHADOW_INGEST_FAILURES tracked but never surfaced to operators.
    Now emitted as:
      1. OTel span attributes (for trace-linked telemetry)
      2. Gauge metric via MetricsRegistry (for dashboard/scrape)
      3. Alert threshold warning when failures accumulate faster than flush clears

    Fail-safe: OTel / MetricsRegistry unavailable -> no-op, never raises.

    Args:
        failure_count: Explicit failure count to emit. Defaults to current
            _SHADOW_INGEST_FAILURES.
    """
    if failure_count is None:
        failure_count = _SHADOW_INGEST_FAILURES
    try:
        from otel._instrumentation import set_attribute
        set_attribute('shadow.ingest_failures', failure_count)
        set_attribute('shadow.queue_full_warned', _QUEUE_FULL_WARNED)
    except Exception:
        pass
    try:
        from hledac.universal.metrics_registry import get_metrics_registry
        get_metrics_registry().set_gauge('shadow_ingest_failures', float(failure_count))
    except Exception:
        pass
    try:
        global _SHADOW_FAILURES_AT_LAST_FLUSH
        _new_failures = failure_count - _SHADOW_FAILURES_AT_LAST_FLUSH
        if _new_failures > _SHADOW_ALERT_THRESHOLD:
            logger.warning(f'[SHADOW ALERT] Queue saturating: {_new_failures} new failures since last flush (threshold={_SHADOW_ALERT_THRESHOLD}). Findings may be disappearing.')
    except Exception:
        pass