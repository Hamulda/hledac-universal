"""
Sprint DuckDB Write Coalescer — coalescing write path for N concurrent lanes.

Coalesces findings from N concurrent acquisition lanes into large batches,
reducing DuckDB call frequency (10 lanes × ~100 findings → 1 × ~1000).

Pure asyncio — no threading, no locks, no multiprocessing.
asyncio.Queue handles all thread-safety automatically.

Env tunables:
  HLEDAC_COALESCER_MAX_BATCH   → max_batch_size (default 1024)
  HLEDAC_COALESCER_FLUSH_MS    → flush_interval_s as ms (default 50ms)
  HLEDAC_COALESCER_QUEUE_SIZE  → queue_maxsize (default 8192)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class FlushError(Exception):
    """
    Raised when flush_fn fails in drain_and_get_accepted().

    Carries the original exception and the findings that failed to flush
    so callers can inspect/retry/log.
    """

    def __init__(self, exc: Exception, findings: list[Any]) -> None:
        self._exc = exc
        self._findings = findings
        super().__init__(f"flush failed: {type(exc).__name__}: {exc}")

    @property
    def original_exception(self) -> Exception:
        return self._exc

    @property
    def findings(self) -> list[Any]:
        return self._findings


@dataclass
class CoalescerConfig:
    """Tunable config for WriteCoalescer. All values overridable via env vars."""

    # M1 8GB: smaller batches (50) = faster RAM release, fewer concurrent DuckDB ops.
    # Longer interval (500ms) = better batching efficiency, acceptable ~500ms latency.
    # Adaptive flush still active: fast_interval (5ms) for sparse traffic.
    max_batch_size: int = 50
    flush_interval_s: float = 0.5
    queue_maxsize: int = 16384
    # Adaptive flush - when queue depth < min_batch_ratio, use fast_interval
    min_batch_ratio: float = 0.05  # flush immediately if queue >= 5% of max_batch_size
    fast_interval_s: float = 0.005  # 5ms — near-zero latency for sparse findings

    @classmethod
    def from_env(cls) -> CoalescerConfig:
        """Read env vars at init time. Missing env → defaults."""
        return cls(
            max_batch_size=int(
                os.environ.get("HLEDAC_COALESCER_MAX_BATCH", "50")
            ),
            flush_interval_s=float(os.environ.get("HLEDAC_COALESCER_FLUSH_MS", "500"))
            / 1000.0,
            queue_maxsize=int(os.environ.get("HLEDAC_COALESCER_QUEUE_SIZE", "16384")),
            min_batch_ratio=float(os.environ.get("HLEDAC_COALESCER_MIN_BATCH_RATIO", "0.05")),
            fast_interval_s=float(os.environ.get("HLEDAC_COALESCER_FAST_MS", "5"))
            / 1000.0,
        )


class WriteCoalescer:
    """
    Async write coalescer — sits in front of async_ingest_findings_batch().

    Accepts findings from N concurrent lanes via submit().
    Merges into large batches, flushes as one call.

    Properties:
      - Pure asyncio Task — no threads, no locks
      - asyncio.Queue handles all thread-safety
      - findings list passed to submit() must not be mutated after return
      - _run_loop handles both normal shutdown and error cases cleanly
      - if flush_fn raises, coalescer continues running (degraded, not crashed)

    Stats keys: submitted, flushed_batches, flushed_findings, errors
    """

    def __init__(
        self,
        # F5.2: Accept both sync and async callables.
        # duckdb_store passes async_ingest_findings_batch (async def).
        # Typed as Any to avoid unsolvable generic variance: both paths are safe
        # because _flush is always awaited regardless of sync/async flavour.
        flush_fn: Any,
        config: CoalescerConfig | None = None,
        # P1-3: Optional error callback — called on every flush failure.
        # Receives (exc, findings, batch_num). Enables callers to be notified
        # of background flush errors rather than silent [] returns.
        on_flush_error: Callable[[Exception, list[Any], int], None] | None = None,
    ) -> None:
        self._flush_fn = flush_fn
        self._config = config or CoalescerConfig.from_env()
        self._on_flush_error = on_flush_error
        self._queue: asyncio.Queue[list[Any]] = asyncio.Queue(
            maxsize=self._config.queue_maxsize
        )
        self._task: asyncio.Task[None] | None = None
        self._running: bool = False
        self._stats: dict[str, int] = {
            "submitted": 0,
            "flushed_batches": 0,
            "flushed_findings": 0,
            "errors": 0,
        }
        self._batch_counter: int = 0
        # Track last deadline for interval-based flushing
        self._last_flush_time: float = 0.0

    async def __aenter__(self) -> "WriteCoalescer":
        """Async context manager entry — starts the coalescer loop."""
        await self.start()
        return self

    async def __aexit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        """Async context manager exit — bounded-time cleanup (10s default)."""
        await self.aclose(timeout_s=10.0)

    async def start(self) -> None:
        """Start the coalescer loop task."""
        if self._running:
            return
        self._running = True
        self._last_flush_time = time.monotonic()
        self._task = asyncio.create_task(
            self._run_loop(), name="write_coalescer"
        )
        logger.debug("write_coalescer: started (max_batch=%d, flush_interval=%.3fs)",
                     self._config.max_batch_size, self._config.flush_interval_s)

    async def aclose(self, timeout_s: float = 10.0) -> None:
        """
        Async context manager exit — signals _run_loop to drain and exit.

        Args:
            timeout_s: max seconds to wait for the loop task to finish (default 10.0).
                       G-6: on timeout, _drain_residual_queue() is called
                       so no queued items are silently dropped.
        """
        await self.stop(timeout_s=timeout_s)

    async def stop(self, timeout_s: float = 15.0) -> None:
        """Implementation: drain queue and cancel loop task."""
        if not self._running:
            # G-6: Already stopped — drain any residual queue items so they
            # are not silently dropped when stop() is called twice.
            await self._drain_residual_queue()
            return
        self._running = False
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=timeout_s)
            except TimeoutError:
                # G-6: TimeoutError does NOT cancel the task — the task keeps
                # running in the background after aclose() returns, which would
                # drain the queue AFTER the store is closed (race: submit may
                # have queued items during the flush interval).  Explicitly
                # cancel so the loop aborts immediately.
                self._task.cancel()
                try:
                    await self._task
                except (TimeoutError, asyncio.CancelledError):
                    pass
                logger.warning(
                    "write_coalescer: stop timeout after %.1fs — task cancelled", timeout_s
                )
                # G-6: Drain any items that were queued or in-flight at
                # cancellation time so they are not silently dropped.
                await self._drain_residual_queue()
        logger.info(
            "write_coalescer: stopped — "
            "total submitted=%d flushed_batches=%d flushed_findings=%d errors=%d",
            self._stats["submitted"],
            self._stats["flushed_batches"],
            self._stats["flushed_findings"],
            self._stats["errors"],
        )

    async def submit(self, findings: list[Any]) -> None:
        """
        Submit a findings list for coalescing.

        NOTE: findings list must not be mutated after submit() returns.
        Caller is responsible for ensuring this.

        Args:
            findings: list of CanonicalFinding (or dict) to coalesce.
        """
        if not findings:
            return
        await self._queue.put(findings)
        self._stats["submitted"] += len(findings)
        q_size = self._queue.qsize()
        logger.debug(
            "write_coalescer: queued %d findings, queue_depth=%d",
            len(findings),
            q_size,
        )

    async def _run_loop(self) -> None:
        """
        Main loop — collect from queue, flush on batch size or interval.

        Thread2b adaptive flush algorithm:
          - Adaptive interval: use fast_interval when queue depth is sparse
            (queue depth < min_batch_ratio × max_batch_size → fast 5ms flush)
          - Normal interval: flush_interval (20ms) when moderate traffic
          - Immediate flush: when pending >= max_batch_size
          - Always: re-check _running on every iteration for fast stop() response
        """
        pending: list[Any] = []
        deadline = time.monotonic() + self._config.flush_interval_s

        while True:
            # Check stop signal BEFORE waiting — ensures stop() doesn't block forever
            if not self._running:
                if pending:
                    await self._flush(pending)
                break

            # Thread2b: Adaptive deadline based on queue depth
            q_size = self._queue.qsize()
            min_flush_size = int(self._config.max_batch_size * self._config.min_batch_ratio)
            if q_size < min_flush_size and len(pending) < min_flush_size:
                # Sparse traffic — use fast_interval for near-zero latency
                current_interval = self._config.fast_interval_s
            else:
                # Normal traffic — use standard flush_interval
                current_interval = self._config.flush_interval_s

            now = time.monotonic()
            remaining = max(0.0, deadline - now)
            if remaining > 0:
                # Short polling interval so we can observe _running=False during wait
                poll_interval = min(remaining, 0.1)
                try:
                    item = await asyncio.wait_for(
                        self._queue.get(), timeout=poll_interval
                    )
                    pending.extend(item)
                    # Immediate flush on max_batch_size reached
                    if len(pending) >= self._config.max_batch_size:
                        await self._flush(pending)
                        pending = []
                        deadline = time.monotonic() + current_interval
                except TimeoutError:
                    pass
            else:
                # Deadline passed — drain queue without waiting, then flush
                while True:
                    try:
                        item = self._queue.get_nowait()
                        pending.extend(item)
                        if len(pending) >= self._config.max_batch_size:
                            break
                    except asyncio.QueueEmpty:
                        break
                if pending:
                    await self._flush(pending)
                    pending = []
                deadline = time.monotonic() + current_interval

            # Immediate flush if max_batch_size reached
            if len(pending) >= self._config.max_batch_size:
                await self._flush(pending)
                pending = []
                deadline = time.monotonic() + current_interval

    async def _flush(self, findings: list[Any]) -> list[Any]:
        """
        Flush findings through the provided flush_fn.

        Returns merged list of FindingQualityDecision/ActivationResult from flush_fn.
        Fail-safe: logs error and returns [] if flush_fn raises.
        Coalescer continues running in degraded mode on single flush failure.
        """
        if not findings:
            return []
        self._batch_counter += 1
        batch_num = self._batch_counter
        start = time.monotonic()
        logger.debug(
            "write_coalescer: flushing %d findings in batch #%d",
            len(findings),
            batch_num,
        )
        try:
            results: list[Any] = await self._flush_fn(findings)
            elapsed_ms = (time.monotonic() - start) * 1000
            self._stats["flushed_batches"] += 1
            self._stats["flushed_findings"] += len(findings)
            logger.debug(
                "write_coalescer: flushed %d findings in %.1fms (batch #%d)",
                len(findings),
                elapsed_ms,
                batch_num,
            )
            return results
        except Exception as exc:  # noqa: BLE001
            self._stats["errors"] += 1
            logger.warning(
                "write_coalescer: flush error batch #%d: %s: %s",
                batch_num,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            # P1-3: Notify caller of flush failure via optional callback
            if self._on_flush_error is not None:
                self._on_flush_error(exc, findings, batch_num)
            # Do NOT re-raise — coalescer survives single flush failure
            return []

    async def _drain_residual_queue(self) -> None:
        """
        G-6: Drain all items currently in the queue and flush them.

        Called from stop() when:
          (a) stop() is called on an already-stopped coalescer — no task
              is running to drain the queue, so we must do it here.
          (b) stop() timed out — the task was cancelled, leaving queued
              items unflushed; drain them now so nothing is lost.

        This is a best-effort drain — if flush_fn itself fails we log
        the error and move on rather than raising.
        """
        pending: list[Any] = []
        while True:
            try:
                item = self._queue.get_nowait()
                pending.extend(item)
            except asyncio.QueueEmpty:
                break
        if pending:
            logger.debug(
                "write_coalescer: _drain_residual_queue flushing %d residual items",
                len(pending),
            )
            await self._flush(pending)

    async def drain_and_get_accepted(
        self, findings: list[Any] | None = None
    ) -> list[Any]:
        """
        Flush any pending items AND optionally submit new findings.

        Merges all results and returns a flattened list of
        FindingQualityDecision/ActivationResult objects.

        For fire-and-forget callers that need accepted count:
          results = await coalescer.drain_and_get_accepted(findings)
          accepted = sum(1 for r in results if _is_accepted(r))

        Returns [] if coalescer not running or flush fails.
        """
        pending: list[Any] = []
        if self._running:
            try:
                item = self._queue.get_nowait()
                pending.extend(item)
                while not self._queue.empty():
                    try:
                        item = self._queue.get_nowait()
                        pending.extend(item)
                    except asyncio.QueueEmpty:
                        break
            except asyncio.QueueEmpty:
                pass

        merged_results: list[Any] = []
        if pending:
            merged_results = await self._flush(pending)
        if findings:
            result = await self._flush(findings)
            merged_results.extend(result)
        # P1-3: Check if any flush failed and raise FlushError with context
        # Note: _flush returns [] on error, so empty merged_results with
        # non-zero pending/findings indicates a silent failure — detect it.
        if not merged_results and (pending or findings):
            # At least one flush returned [] due to error (not empty input).
            # We don't know which one failed, so report the total.
            failed_findings = pending if pending else (findings or [])
            raise FlushError(
                RuntimeError("flush returned [] after error"),
                failed_findings,
            )
        return merged_results


def _is_accepted(result: Any) -> bool:
    """Check accepted field from FindingQualityDecision or ActivationResult."""
    if isinstance(result, dict):
        return bool(result.get("accepted"))
    return bool(getattr(result, "accepted", False))
