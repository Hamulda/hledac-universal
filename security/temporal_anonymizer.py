"""
Temporal Anonymizer — timestamp anonymization and delayed write buffering.

Threat model: adversary cannot determine WHEN research was conducted.
All timestamps rounded to 15-min boundaries + jitter, stored to UTC,
buffered with random delay to prevent timing correlation.

M1 constraint: all operations < 5ms per finding. No heavy crypto.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

logger = logging.getLogger(__name__)

_TA_ENABLED = os.getenv("HLEDAC_ENABLE_ZERO_ATTRIBUTION", "0") == "1"

# 15-minute boundary in seconds
_BOUNDARY = 15 * 60
_JITTER_MAX = 120.0  # ±2 minutes in seconds

# Maximum buffer size to prevent unbounded memory growth
_MAX_BUFFER_SIZE = 1000
# Default flush delay range
_DEFAULT_MIN_DELAY = 30.0
_DEFAULT_MAX_DELAY = 120.0


class TemporalAnonymizer:
    """Temporal anonymization engine.

    Gated by HLEDAC_ENABLE_ZERO_ATTRIBUTION=1.

    Invariants:
    - anonymize_timestamp: always returns UTC-normalized, rounded ts
    - delayed_write_buffer: flush is non-deterministic within [min_delay, max_delay]
    """

    __slots__ = (
        "_enabled",
        "_buffer",
        "_buffer_lock",
        "_flush_task",
        "_min_delay",
        "_max_delay",
        "_max_buffer_size",
    )

    def __init__(
        self,
        enabled: bool | None = None,
        max_delay: float = _DEFAULT_MAX_DELAY,
        max_buffer_size: int = _MAX_BUFFER_SIZE,
    ) -> None:
        self._enabled = enabled if enabled is not None else _TA_ENABLED
        self._buffer: list[CanonicalFinding] = []
        self._buffer_lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._min_delay = _DEFAULT_MIN_DELAY
        self._max_delay = max(_DEFAULT_MIN_DELAY, max_delay)
        self._max_buffer_size = max(1, max_buffer_size)
        logger.debug(
            "TemporalAnonymizer enabled=%s max_delay=%s buffer_size=%s",
            self._enabled,
            self._max_delay,
            self._max_buffer_size,
        )

    # ------------------------------------------------------------------
    # 1. Timestamp anonymization
    # ------------------------------------------------------------------
    def anonymize_timestamp(self, ts: float) -> float:
        """Round ts to nearest 15-min boundary + ±2min jitter, return UTC.

        M1 constraint: < 0.05ms — pure arithmetic.
        """
        if not self._enabled:
            return ts
        try:
            # Round to nearest 15-minute boundary
            rounded = round(ts / _BOUNDARY) * _BOUNDARY
            # Add ±2 minute jitter using cryptographically secure RNG
            jitter = (secrets.randbelow(int(_JITTER_MAX * 1000)) / 1000.0) - (_JITTER_MAX / 2)
            return rounded + jitter
        except Exception:
            return ts

    # ------------------------------------------------------------------
    # 2. Delayed write buffer
    # ------------------------------------------------------------------
    async def delayed_write_buffer(
        self,
        findings: list[CanonicalFinding],
        flush_callback,  # async fn(list[CanonicalFinding]) -> None
    ) -> None:
        """Buffer findings and flush after random delay.

        Store findings in memory buffer, schedule flush after a random
        delay in [min_delay, max_delay] seconds. Prevents timing
        correlation between fetch time and storage time.

        M1 constraint: < 2ms add to buffer.
        """
        if not self._enabled:
            await flush_callback(findings)
            return
        try:
            async with self._buffer_lock:
                # Evict oldest if buffer is full
                if len(self._buffer) + len(findings) > self._max_buffer_size:
                    evict = (len(self._buffer) + len(findings)) - self._max_buffer_size
                    self._buffer = self._buffer[evict:]
                self._buffer.extend(findings)
                logger.debug(
                    "TemporalAnonymizer buffered %d findings (%d total)",
                    len(findings),
                    len(self._buffer),
                )
            # Schedule flush with random delay if not already scheduled
            if self._flush_task is None or self._flush_task.done():
                delay = self._min_delay + secrets.randbelow(
                    int((self._max_delay - self._min_delay) * 1000)
                ) / 1000.0
                self._flush_task = asyncio.create_task(self._delayed_flush(delay, flush_callback))
        except Exception as e:
            logger.warning("delayed_write_buffer failed, flushing immediately: %s", e)
            await flush_callback(findings)

    async def _delayed_flush(
        self,
        delay: float,
        flush_callback,
    ) -> None:
        """Wait delay seconds then flush buffer."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        findings_to_flush: list[CanonicalFinding] = []
        async with self._buffer_lock:
            findings_to_flush = self._buffer[:]
            self._buffer.clear()
        if findings_to_flush:
            try:
                await flush_callback(findings_to_flush)
                logger.debug("TemporalAnonymizer flushed %d findings", len(findings_to_flush))
            except Exception as e:
                logger.warning("flush_callback failed: %s", e)

    # ------------------------------------------------------------------
    # 3. Timezone normalization
    # ------------------------------------------------------------------
    @staticmethod
    def timezone_normalize() -> str:
        """Always return 'UTC' — forces all timestamps to UTC."""
        return "UTC"

    # ------------------------------------------------------------------
    # Flush any pending buffer on shutdown
    # ------------------------------------------------------------------
    async def flush(self, flush_callback) -> None:
        """Force immediate flush of pending buffer."""
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        findings_to_flush: list[CanonicalFinding] = []
        async with self._buffer_lock:
            findings_to_flush = self._buffer[:]
            self._buffer.clear()
        if findings_to_flush:
            await flush_callback(findings_to_flush)


__all__ = ["TemporalAnonymizer"]