"""
Entropy Feedback Service — UNIFIED-003 Entropy Alert Bridge
===========================================================

Provides entropy-based anomaly detection for fetch operations.

Features:
- Entropy scoring for response content
- Anomaly detection via statistical analysis
- Alert callbacks for high-entropy content
- Integration with entropy alerts system

M1 8GB: Uses __slots__ for memory efficiency.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import threading
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from hledac.universal.compat.msgspec_gc_compat import Struct

logger = logging.getLogger(__name__)


class EntropyConfig(Struct, frozen=True):
    """Entropy configuration. M1 8GB: msgspec.Struct for fast init."""

    high_entropy_threshold: float = 7.5
    alert_queue_size: int = 1000
    sample_size: int = 10000
    update_interval_s: float = 60.0
    enable_streaming: bool = True
    entropy_chunk_size: int = 4096


@dataclass(slots=True)
class EntropyResult:
    """Result of entropy analysis."""

    url: str
    entropy_bits_per_byte: float
    chi_square_score: float
    is_anomaly: bool
    anomaly_type: str = "none"
    alert_level: str = "normal"
    content_hash: str = ""
    sample_size: int = 0


class StreamingEntropyCalculator:
    """
    Calculate Shannon entropy on streaming data.

    Uses byte frequency counting for efficient entropy calculation.
    M1 8GB: Streaming approach prevents memory exhaustion.

    Thread-safety: Uses threading.Lock for concurrent access.
    """

    def __init__(self, sample_size: int = 10000, thread_safe: bool = True) -> None:
        self.sample_size = sample_size
        self._byte_counts: Counter[int] = Counter()
        self._total_bytes: int = 0
        self._sample_buffer: bytearray = bytearray()
        self._hash = hashlib.sha256()
        self._lock = threading.Lock() if thread_safe else None

    def feed(self, chunk: bytes) -> None:
        """Feed a chunk of data for entropy calculation."""
        if self._lock:
            with self._lock:
                self._feed_impl(chunk)
        else:
            self._feed_impl(chunk)

    def _feed_impl(self, chunk: bytes) -> None:
        """Internal feed implementation (must be called with lock held)."""
        self._hash.update(chunk)

        # Count bytes (up to sample_size)
        for byte_val in chunk:
            if len(self._sample_buffer) < self.sample_size:
                self._sample_buffer.append(byte_val)
            self._byte_counts[byte_val] += 1
            self._total_bytes += 1

    def calculate_entropy(self) -> float:
        """Calculate Shannon entropy in bits per byte."""
        if self._lock:
            with self._lock:
                return self._calculate_entropy_impl()
        return self._calculate_entropy_impl()

    def _calculate_entropy_impl(self) -> float:
        """Internal entropy calculation (must be called with lock held)."""
        if self._total_bytes == 0:
            return 0.0

        entropy = 0.0
        for count in self._byte_counts.values():
            if count > 0:
                p = count / self._total_bytes
                entropy -= p * math.log2(p)

        return entropy

    def calculate_chi_square(self) -> float:
        """
        Calculate chi-square statistic for byte distribution.

        High chi-square = suspicious (not uniform distribution).
        """
        if self._lock:
            with self._lock:
                return self._calculate_chi_square_impl()
        return self._calculate_chi_square_impl()

    def _calculate_chi_square_impl(self) -> float:
        """Internal chi-square calculation (must be called with lock held)."""
        if self._total_bytes == 0:
            return 0.0

        expected = self._total_bytes / 256
        chi_square = 0.0

        for byte_val in range(256):
            observed = self._byte_counts.get(byte_val, 0)
            chi_square += ((observed - expected) ** 2) / expected

        return chi_square

    @property
    def content_hash(self) -> str:
        """Get SHA256 hash of content."""
        if self._lock:
            with self._lock:
                return self._hash.hexdigest()
        return self._hash.hexdigest()

    @property
    def sample_size_actual(self) -> int:
        """Actual sample size processed."""
        if self._lock:
            with self._lock:
                return len(self._sample_buffer)
        return len(self._sample_buffer)

    def reset(self) -> None:
        """Reset calculator state."""
        if self._lock:
            with self._lock:
                self._byte_counts.clear()
                self._total_bytes = 0
                self._sample_buffer.clear()
                self._hash = hashlib.sha256()
        else:
            self._byte_counts.clear()
            self._total_bytes = 0
            self._sample_buffer.clear()
            self._hash = hashlib.sha256()


@dataclass(slots=True)
class EntropyFeedbackService:
    """
    Entropy feedback service for anomaly detection.

    Implements UNIFIED-003 entropy feedback bridge:
    - Real-time entropy calculation
    - Statistical anomaly detection
    - Alert queue for high-entropy content
    - Consumer loop for alert processing

    M1 8GB: Uses __slots__ for memory efficiency.

    ISSUE-OPT-1: Uses asyncio.Event for _running flag instead of bool.
    STRESS-25 pattern: asyncio.Event provides immediate cancellation response
    (no polling delay when stop() is called).
    """

    config: EntropyConfig = field(default_factory=EntropyConfig)

    _alert_queue: asyncio.Queue[EntropyResult] = field(default_factory=lambda: asyncio.Queue(maxsize=1000))
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _running: asyncio.Event = field(default=None, init=False)  # ISSUE-OPT-1: Event-driven
    _consumer_task: asyncio.Task[None] | None = field(default=None, init=False)
    _alert_callbacks: list[Callable[[EntropyResult], Awaitable[None]]] = field(default_factory=list, init=False)
    _stats: dict[str, Any] = field(
        default_factory=lambda: {
            "samples_analyzed": 0,
            "anomalies_detected": 0,
            "alerts_queued": 0,
            "alerts_processed": 0,
        }
    )

    def __post_init__(self) -> None:
        # ISSUE-OPT-1: Initialize asyncio.Event lazily
        if self._running is None:
            object.__setattr__(self, "_running", asyncio.Event())
            self._running.set()  # Start in running state

    def register_alert_callback(self, callback: Callable[[EntropyResult], Awaitable[None]]) -> None:
        """Register callback for entropy alerts."""
        self._alert_callbacks.append(callback)

    async def analyze_content(self, url: str, content: bytes) -> EntropyResult:
        """
        Analyze content entropy.

        Args:
            url: URL of the content
            content: Content bytes

        Returns:
            EntropyResult with analysis
        """
        calc = StreamingEntropyCalculator(sample_size=self.config.sample_size)
        calc.feed(content[: self.config.sample_size])

        entropy = calc.calculate_entropy()
        chi_square = calc.calculate_chi_square()

        # Determine anomaly
        is_anomaly = entropy > self.config.high_entropy_threshold
        anomaly_type = "none"
        alert_level = "normal"

        if is_anomaly:
            if entropy > 7.8:
                anomaly_type = "high_entropy"
                alert_level = "critical"
            elif entropy > 7.5:
                anomaly_type = "elevated_entropy"
                alert_level = "warning"

        result = EntropyResult(
            url=url,
            entropy_bits_per_byte=entropy,
            chi_square_score=chi_square,
            is_anomaly=is_anomaly,
            anomaly_type=anomaly_type,
            alert_level=alert_level,
            content_hash=calc.content_hash,
            sample_size=calc.sample_size_actual,
        )

        async with self._lock:
            self._stats["samples_analyzed"] += 1
            if is_anomaly:
                self._stats["anomalies_detected"] += 1

                try:
                    self._alert_queue.put_nowait(result)
                    self._stats["alerts_queued"] += 1
                except asyncio.QueueFull:
                    logger.warning("Entropy alert queue full, dropping alert")

        return result

    async def analyze_streaming(self, url: str, chunks: list[bytes]) -> EntropyResult:
        """
        Analyze streaming content chunks.

        Args:
            url: URL of the content
            chunks: List of content chunks

        Returns:
            EntropyResult with analysis
        """
        calc = StreamingEntropyCalculator(sample_size=self.config.sample_size)

        for chunk in chunks:
            if self.config.enable_streaming:
                calc.feed(chunk[: self.config.entropy_chunk_size])

        entropy = calc.calculate_entropy()
        chi_square = calc.calculate_chi_square()

        is_anomaly = entropy > self.config.high_entropy_threshold
        anomaly_type = "none"
        alert_level = "normal"

        if is_anomaly:
            anomaly_type = "high_entropy"
            alert_level = "warning" if entropy < 7.8 else "critical"

        result = EntropyResult(
            url=url,
            entropy_bits_per_byte=entropy,
            chi_square_score=chi_square,
            is_anomaly=is_anomaly,
            anomaly_type=anomaly_type,
            alert_level=alert_level,
            content_hash=calc.content_hash,
            sample_size=calc.sample_size_actual,
        )

        async with self._lock:
            self._stats["samples_analyzed"] += 1
            if is_anomaly:
                self._stats["anomalies_detected"] += 1

        return result

    async def start_consumer(self) -> None:
        """Start alert consumer loop."""
        if self._running.is_set():
            return

        self._running.set()  # ISSUE-OPT-1: Set running state
        self._consumer_task = safe_create_task(self._consumer_loop())
        logger.info("Entropy feedback consumer started")

    async def stop_consumer(self) -> None:
        """Stop alert consumer loop. ISSUE-OPT-1: Uses asyncio.Event for immediate response."""
        self._running.clear()  # ISSUE-OPT-1: Immediately wakes up wait()
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        logger.info("Entropy feedback consumer stopped")

    async def _consumer_loop(self) -> None:
        """Alert consumer loop. ISSUE-OPT-1: Uses asyncio.Event for event-driven cancellation."""
        while not self._running.is_set():
            try:
                # Use wait_for with Event.wait() for immediate cancellation on stop()
                try:
                    result = await asyncio.wait_for(self._alert_queue.get(), timeout=self.config.update_interval_s)
                except TimeoutError:
                    # Check if we should continue or exit
                    if self._running.is_set():
                        continue
                    else:
                        break

                for callback in self._alert_callbacks:
                    try:
                        await callback(result)
                        self._stats["alerts_processed"] += 1
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"Entropy alert callback error: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.error(f"Entropy consumer loop error: {e}")

    async def get_next_alert(self) -> EntropyResult | None:
        """Get next alert from queue (non-blocking)."""
        try:
            return self._alert_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def get_stats(self) -> dict[str, Any]:
        """Get entropy statistics."""
        return {
            **self._stats,
            "queue_size": self._alert_queue.qsize(),
            "callbacks_registered": len(self._alert_callbacks),
        }

    async def aclose(self) -> None:
        """Close entropy feedback service and release resources."""
        await self.stop_consumer()
        async with self._lock:
            self._alert_callbacks.clear()
        logger.debug("EntropyFeedbackService closed")


class BlockingEntropyCalculator:
    """
    Blocking entropy calculator for synchronous contexts.

    Uses threading.Lock for thread safety.
    """

    def __init__(self, sample_size: int = 10000) -> None:
        self.sample_size = sample_size
        self._calc = StreamingEntropyCalculator(sample_size)
        self._lock = threading.Lock()

    def feed(self, chunk: bytes) -> None:
        """Feed a chunk of data (thread-safe)."""
        with self._lock:
            self._calc.feed(chunk)

    def calculate(self) -> tuple[float, float, str]:
        """Calculate entropy and chi-square (thread-safe)."""
        with self._lock:
            entropy = self._calc.calculate_entropy()
            chi_square = self._calc.calculate_chi_square()
            content_hash = self._calc.content_hash
        return entropy, chi_square, content_hash


__all__ = [
    "EntropyConfig",
    "EntropyResult",
    "StreamingEntropyCalculator",
    "EntropyFeedbackService",
    "BlockingEntropyCalculator",
]
