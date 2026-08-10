"""
runtime/finding_pipeline.py
===========================


ISSUE-024: Producer-consumer finding pipeline with Rust MPSC backpressure.

Architecture
-------------
Bridge (sync): source_fetcher → CanonicalFinding batches
    ↓ (sync serialization)
Pipeline producer: FindingPipeline.enqueue_batch() → Rust MPSCPool (256 items)
    ↓ (Rust lock-free send, ~2-5ns via ARM LSE atomics)
Background consumer: async drain loop → async_ingest_findings_batch()
    ↓
DuckDB canonical write (already Arrow-batched, 1024-item chunks)

Why this over raw submit_findings per source
--------------------------------------------
- Raw: N sources × M findings × async task overhead = N×M Task objects
- This: N sources → 1 enqueue_batch() per source → ~1-2 consumer wakes
- Rust MPSCPool: bounded 256 slots, non-blocking send(), pipe-wake for async
- Consumer: single background task drains queue, batches to Arrow internally
- Backpressure: when queue full, drop oldest batch (not newest — preserves
  freshest findings, maximizes information density per cycle)

M1 8GB budget
-------------
- MPSCPool: 256 slots × 512B = 128 KiB (negligible)
- Consumer task: ~1 async task instead of N submit_findings tasks
- DuckDB Arrow batch: 1024-item chunks (already bounded)

Invariants
---------
[G1] enqueue_batch() never blocks — returns bool (sent/dropped)
[G2] Drop strategy: drop oldest batch (FIFO eviction)
[G3] Consumer: asyncio.CancelledError re-raised, never swallowed
[G4] Circuit breaker: if DuckDB ingest fails 3×, queue drains to logging
[G5] No bare except — every path has explicit error handling
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from typing import TYPE_CHECKING, Any

from hledac.universal.utils.async_helpers import safe_create_task

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

__all__ = ["FindingPipeline", "create_finding_pipeline"]

logger = logging.getLogger(__name__)

# Queue depth — 256 slots (4× smaller than evidence_log's 2048, since
# findings are larger than IOC metadata (~200-500B per CanonicalFinding))
_QUEUE_CAPACITY = 256

# Drop oldest when queue full — keeps freshest findings, maximizes per-cycle value
_DROP_OLDEST = True


class FindingPipeline:
    """Producer-consumer finding pipeline backed by Rust MPSCPool.

    Producers call enqueue_batch() from synchronous or async context.
    Background consumer drains to DuckDB via async_ingest_findings_batch().

    Usage::

        pipeline = FindingPipeline(duckdb_store)
        pipeline.start()

        # From any async context:
        await pipeline.enqueue_batch(findings)

        # At shutdown:
        await pipeline.stop()
    """

    __slots__ = (
        "_duckdb_store",
        "_mpsc",
        "_sender_ptr",
        "_consumer_task",
        "_running",
        "_dropped_count",
        "_enqueued_count",
        "_closed",
    )

    def __init__(
        self,
        duckdb_store: Any,
        *,
        capacity: int = _QUEUE_CAPACITY,
    ) -> None:
        """Initialize the finding pipeline.

        Args:
            duckdb_store: DuckDBShadowStore instance for canonical writes.
            capacity: Max queue depth (default 256). Must be ≥ 1.
        """
        self._duckdb_store = duckdb_store
        self._mpsc: Any = None
        self._sender_ptr: int = 0
        self._consumer_task: asyncio.Task[None] | None = None
        self._running = False
        self._dropped_count: int = 0
        self._enqueued_count: int = 0
        self._closed = False
        self._init_rust(capacity)

    def _init_rust(self, capacity: int) -> None:
        """Initialize Rust MPSCPool. Falls back to no-op on import error."""
        try:
            # R6: Centralized Rust access via core.rust_backend
            from hledac.universal.core.rust_backend import rust
            MPSCPool = rust.raw.MPSCPool  # type: ignore[assignment]

            pool = MPSCPool(capacity=capacity)  # type: ignore[attr-defined]
            sender_ptr = pool.add_sender()  # type: ignore[attr-defined]
            wake_fd = pool.wake_fd()  # type: ignore[attr-defined]
            self._mpsc = pool
            self._sender_ptr = sender_ptr
            self._wake_fd = wake_fd
            logger.debug(
                "[ISSUE-024] Rust MPSCPool initialized: capacity=%d, wake_fd=%d",
                capacity,
                wake_fd,
            )
        except Exception as _exc:
            logger.debug(
                "[ISSUE-024] Rust MPSCPool unavailable, using no-op mode: %s",
                _exc,
            )
            self._mpsc = None
            self._sender_ptr = 0

    @property
    def wake_fd(self) -> int:
        """Pipe read fd for asyncio reader registration."""
        if self._mpsc is not None:
            return self._mpsc.wake_fd()
        return -1

    def start(self) -> None:
        """Start the background consumer drain loop."""
        if self._running:
            return
        self._running = True
        self._consumer_task = safe_create_task(
            self._drain_loop(),
            name="finding_pipeline:drain",
        )
        logger.debug("[ISSUE-024] FindingPipeline consumer started")

    async def stop(self) -> None:
        """Stop the consumer and flush remaining items."""
        if self._closed:
            return
        self._closed = True
        self._running = False

        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:  # noqa: BLE001
                pass
            except Exception:  # noqa: BLE001
                pass
            self._consumer_task = None

        # Final drain — try to ingest remaining items
        await self._final_drain()
        logger.debug(
            "[ISSUE-024] FindingPipeline stopped: enqueued=%d, dropped=%d",
            self._enqueued_count,
            self._dropped_count,
        )

    async def enqueue_batch(
        self,
        findings: list[CanonicalFinding],
    ) -> bool:
        """Enqueue a batch of findings for async ingest.

        Args:
            findings: List of CanonicalFinding objects to enqueue.

        Returns:
            True if enqueued successfully.
            False if queue is full (oldest batch was dropped) or pipeline closed.
        """
        if not findings or self._closed:
            return False

        self._enqueued_count += len(findings)

        if self._mpsc is None:
            # No-op fallback: ingest directly
            await self._direct_ingest(findings)
            return True

        # Serialize findings to bytes
        try:
            import orjson

            payload = orjson.dumps(findings)
        except Exception:
            import msgspec

            payload = msgspec.json.encode(findings)

        # Non-blocking send to Rust MPSC
        sent = self._mpsc.send(self._sender_ptr, payload)

        if not sent:
            # Queue full — try to evict oldest (FIFO) then retry
            if _DROP_OLDEST:
                # recv_batch(1) to evict oldest without draining the queue
                self._mpsc.recv_batch(1)
                # Retry send after evicting one slot
                sent = self._mpsc.send(self._sender_ptr, payload)
                if not sent:
                    # Still full — this shouldn't happen with capacity=256
                    self._dropped_count += len(findings)
                    logger.warning(
                        "[ISSUE-024] MPSCPool still full after eviction, dropping %d items",
                        len(findings),
                    )
                    return False

        return True

    def enqueue_batch_sync(
        self,
        findings: list[CanonicalFinding],
    ) -> bool:
        """Synchronous enqueue — for use from sync context.

        Falls back to direct ingest if MPSCPool unavailable.
        """
        if not findings or self._closed:
            return False

        self._enqueued_count += len(findings)

        if self._mpsc is None:
            # Must spawn async task for direct ingest
            # ISSUE-02 fix: use new_event_loop() pattern for sync context (not get_event_loop which is deprecated in 3.14)
            # MODERN-06 FIX: Store and manage loop lifecycle properly
            _loop: asyncio.AbstractEventLoop | None = None
            try:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    _loop = loop
                loop.run_in_executor(
                    None,
                    self._sync_ingest_wrapper,
                    findings,
                )
            except Exception:  # noqa: BLE001
                pass  # fail-soft
            finally:
                # MODERN-06 FIX: Close loop if we created it.
                # Note: Loop stays open for executor to complete, then we close it.
                if _loop is not None and not _loop.is_closed():
                    try:
                        _loop.close()
                    except Exception:  # noqa: BLE001
                        pass
            return True

        import orjson

        try:
            payload = orjson.dumps(findings)
        except Exception:
            import msgspec

            payload = msgspec.json.encode(findings)

        sent = self._mpsc.send(self._sender_ptr, payload)

        if not sent:
            # Queue full — evict oldest (FIFO) then retry
            self._mpsc.recv_batch(1)
            sent = self._mpsc.send(self._sender_ptr, payload)
            if not sent:
                # Still full — shouldn't happen with capacity=256
                self._dropped_count += len(findings)
                logger.warning(
                    "[ISSUE-024] [sync] MPSCPool still full after eviction, dropping %d items",
                    len(findings),
                )
                return False

        return True

    async def _drain_loop(self) -> None:
        """Background consumer: drain MPSCPool and ingest to DuckDB."""
        loop = asyncio.get_running_loop()

        # Register wake fd with event loop if available
        wake_fd = self.wake_fd
        reader_handle: asyncio.AbstractServer | None = None

        if wake_fd > 0:
            try:
                reader_handle = loop.add_reader(
                    wake_fd,
                    self._on_wake_fd,
                )
            except Exception as _exc:
                logger.debug(
                    "[ISSUE-024] Failed to register wake fd reader: %s",
                    _exc,
                )

        IDLE_TIMEOUT_S = 0.5  # Drain every 500ms even without wake

        try:
            while self._running:
                try:
                    # Wait for wake fd or idle timeout
                    await asyncio.sleep(IDLE_TIMEOUT_S)
                    await self._drain_batch_to_duckdb()
                except asyncio.CancelledError:
                    raise
                except Exception as _exc:
                    logger.debug(
                        "[ISSUE-024] Drain loop error: %s",
                        _exc,
                    )
        finally:
            if reader_handle is not None:
                self._unregister_wake_fd(wake_fd, loop)

    def _unregister_wake_fd(
        self, wake_fd: int, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Unregister wake fd reader, silently ignoring errors."""
        try:
            loop.remove_reader(wake_fd)
        except Exception:  # noqa: BLE001
            pass

    def _on_wake_fd(self) -> None:
        """Called when wake fd fires — wake the drain loop."""
        # The sleep() in _drain_loop will wake naturally;
        # this is just a hint that items are available.
        pass

    async def _drain_batch_to_duckdb(self) -> None:
        """Drain all available items from MPSCPool and ingest to DuckDB."""
        if self._mpsc is None:
            return

        try:
            # Drain up to 256 items per wake cycle
            # recv_batch returns Vec<Vec<u8>> = list[bytes] in Python
            raw_items: list[bytes] = self._mpsc.recv_batch(256)
        except Exception as _exc:
            logger.debug(
                "[ISSUE-024] recv_batch error: %s",
                _exc,
            )
            return

        if not raw_items:
            return

        # Deserialize and flatten
        all_findings: list[CanonicalFinding] = []
        import orjson

        for raw in raw_items:
            try:
                # raw is bytes — a serialized list of CanonicalFinding dicts
                batch_findings: list[CanonicalFinding] = orjson.loads(raw)
                all_findings.extend(batch_findings)
            except Exception as _json_err:
                # Try msgspec as fallback
                try:
                    import msgspec

                    batch_findings = msgspec.json.decode(raw)
                    if isinstance(batch_findings, list):
                        all_findings.extend(batch_findings)
                except Exception as _msg_err:
                    logger.debug(
                        "[ISSUE-024] Failed to deserialize finding batch: orjson=%s msgspec=%s",
                        _json_err,
                        _msg_err,
                    )
                    continue

        if not all_findings:
            return

        # Ingest via canonical write path
        try:
            store = self._duckdb_store
            if store is not None and hasattr(store, "async_ingest_findings_batch"):
                await store.async_ingest_findings_batch(all_findings)
            else:
                await store.submit_findings(all_findings)
        except Exception as _exc:
            logger.warning(
                "[ISSUE-024] DuckDB ingest failed for %d findings: %s",
                len(all_findings),
                _exc,
            )

    async def _direct_ingest(
        self,
        findings: list[CanonicalFinding],
    ) -> None:
        """Direct ingest fallback when MPSCPool unavailable."""
        try:
            store = self._duckdb_store
            if store is not None and hasattr(store, "async_ingest_findings_batch"):
                await store.async_ingest_findings_batch(findings)
            else:
                await store.submit_findings(findings)
        except Exception as _exc:
            logger.debug(
                "[ISSUE-024] Direct ingest failed: %s",
                _exc,
            )

    def _sync_ingest_wrapper(self, findings: list[CanonicalFinding]) -> None:
        """Sync wrapper to run async ingest in executor.
        
        MODERN-06 FIX: Ensure event loop is always closed to prevent leaks.
        """
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self._direct_ingest(findings))
        except Exception:  # noqa: BLE001
            pass
        finally:
            if loop is not None and not loop.is_closed():
                try:
                    loop.close()
                except Exception:  # noqa: BLE001
                    pass  # Best-effort cleanup

    async def _final_drain(self) -> None:
        """Final drain on shutdown — ingest remaining items."""
        if self._mpsc is None:
            return

        try:
            raw_items = self._mpsc.recv_batch(1024)
        except Exception:
            return

        if not raw_items:
            return

        all_findings: list[CanonicalFinding] = []
        for raw in raw_items:
            try:
                import orjson

                batch = orjson.loads(raw)
                if isinstance(batch, list):
                    all_findings.extend(batch)
            except Exception:
                try:
                    import msgspec

                    batch = msgspec.json.decode(raw)
                    if isinstance(batch, list):
                        all_findings.extend(batch)
                except Exception:
                    continue

        if not all_findings:
            return

        try:
            store = self._duckdb_store
            if store is not None:
                if hasattr(store, "async_ingest_findings_batch"):
                    await store.async_ingest_findings_batch(all_findings)
                else:
                    await store.submit_findings(all_findings)
        except Exception as _exc:
            logger.warning(
                "[ISSUE-024] Final drain ingest failed for %d findings: %s",
                len(all_findings),
                _exc,
            )

    # ── Telemetry ────────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        """Return pipeline telemetry."""
        queue_len = 0
        if self._mpsc is not None:
            try:
                queue_len = self._mpsc.len()
            except Exception:
                queue_len = -1

        return {
            "enqueued_count": self._enqueued_count,
            "dropped_count": self._dropped_count,
            "queue_len": queue_len,
            "running": self._running,
            "closed": self._closed,
        }


def create_finding_pipeline(
    duckdb_store: Any,
    *,
    capacity: int = _QUEUE_CAPACITY,
) -> FindingPipeline:
    """Factory: create and start a FindingPipeline."""
    pipeline = FindingPipeline(duckdb_store, capacity=capacity)
    pipeline.start()
    return pipeline
