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
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CoalescerConfig:
    """Tunable config for WriteCoalescer. All values overridable via env vars."""

    max_batch_size: int = 1024
    flush_interval_s: float = 0.05
    queue_maxsize: int = 8192

    @classmethod
    def from_env(cls) -> CoalescerConfig:
        """Read env vars at init time. Missing env → defaults."""
        return cls(
            max_batch_size=int(
                os.environ.get("HLEDAC_COALESCER_MAX_BATCH", "1024")
            ),
            flush_interval_s=float(os.environ.get("HLEDAC_COALESCER_FLUSH_MS", "50"))
            / 1000.0,
            queue_maxsize=int(os.environ.get("HLEDAC_COALESCER_QUEUE_SIZE", "8192")),
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
    ) -> None:
        self._flush_fn = flush_fn
        self._config = config or CoalescerConfig.from_env()
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

    async def stop(self, timeout_s: float = 15.0) -> None:
        """
        Stop the coalescer — signals _run_loop to drain and exit.

        Args:
            timeout_s: max seconds to wait for the loop task to finish.
        """
        if not self._running:
            return
        self._running = False
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=timeout_s)
            except TimeoutError:
                logger.warning(
                    "write_coalescer: stop timeout after %.1fs", timeout_s
                )
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

        Algorithm:
          - wait_for next item with timeout = remaining to deadline
          - extend pending list
          - on timeout: check if deadline passed → flush
          - on max_batch reached → flush immediately
          - after flush: reset deadline
          - break if _running is False (stop requested)
        """
        pending: list[Any] = []
        deadline = time.monotonic() + self._config.flush_interval_s

        while True:
            # Check stop signal BEFORE waiting — ensures stop() doesn't block forever
            if not self._running:
                # Drain any remaining items before exiting
                if pending:
                    await self._flush(pending)
                break

            now = time.monotonic()
            timeout = max(0.0, deadline - now)

            try:
                item = await asyncio.wait_for(
                    self._queue.get(), timeout=timeout
                )
                pending.extend(item)
            except TimeoutError:
                # Check if deadline passed — time to flush regardless of size
                if time.monotonic() >= deadline and pending:
                    await self._flush(pending)
                    pending = []
                    deadline = time.monotonic() + self._config.flush_interval_s
                # F5.2: Removed redundant batch-size check after TimeoutError.
                # max_batch_size is checked immediately after extend() above (line 200),
                # so if we reach this point with pending >= max_batch, that means the
                # deadline check (line 186) already fired — no additional flush needed.
                continue

            # Check max batch size — flush immediately if reached
            if len(pending) >= self._config.max_batch_size:
                await self._flush(pending)
                pending = []
                deadline = time.monotonic() + self._config.flush_interval_s

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
            # Do NOT re-raise — coalescer survives single flush failure
            return []

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
        return merged_results


def _is_accepted(result: Any) -> bool:
    """Check accepted field from FindingQualityDecision or ActivationResult."""
    if isinstance(result, dict):
        return bool(result.get("accepted"))
    return bool(getattr(result, "accepted", False))
