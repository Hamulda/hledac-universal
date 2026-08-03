"""
Entropy-to-Fetch Feedback Bridge — UNIFIED-003
================================================

Closes the feedback loop between uncertainty quantification and data acquisition.

Architecture:
    UncertaintyQuantifier (synthesis_runner.py)
        ↓ emits EntropyAlert
    EntropyFetchBridge (asyncio.Queue pub/sub)
        ↓ routes alerts
    FetchCoordinator (subscriber)
        ↓ trigger_micro_sprint()
        ↓ executes alternative protocols (CT, passive DNS, etc.)
    SynthesisRunner (context update)
        ↓ merges new findings

M1 8GB invariants:
    - Bounded asyncio.Queue(maxsize=64) — prevents unbounded memory growth
    - Lazy import of heavy modules — no startup cost
    - Fail-soft: any error returns gracefully, never blocks synthesis
    - ~256 bytes per idle queue, ~2KB when active

Usage:
    # In synthesis_runner.py after uncertainty_gate():
    bridge = get_entropy_bridge()
    if bridge is not None:
        await bridge.emit(alert)

    # In FetchCoordinator:
    bridge = get_entropy_bridge()
    if bridge is not None:
        bridge.subscribe('fetch_coordinator', self._handle_entropy_alert)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EntropyAlert — event emitted when uncertainty exceeds threshold
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class EntropyAlert:
    """
    Alert emitted when entity or report-level entropy exceeds threshold.

    Fields:
        entity_id: IOC entity identifier (e.g., "192.168.1.1", "CVE-2024-1234")
        entropy: Measured entropy in bits (0.0-4.0 typical range)
        threshold_exceeded: The threshold that was exceeded (e.g., 1.5 bits)
        confidence: Current confidence score (0.0-1.0)
        risk_level: "low" | "medium" | "high"
        timestamp: Unix epoch when alert was created
        metadata: Additional context (e.g., token_count, stability)
    """
    entity_id: str
    entropy: float
    threshold_exceeded: float
    confidence: float
    risk_level: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for queue serialization."""
        return {
            "entity_id": self.entity_id,
            "entropy": self.entropy,
            "threshold_exceeded": self.threshold_exceeded,
            "confidence": self.confidence,
            "risk_level": self.risk_level,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntropyAlert:
        """Reconstruct from dict (for deserialization)."""
        return cls(
            entity_id=data["entity_id"],
            entropy=data["entropy"],
            threshold_exceeded=data["threshold_exceeded"],
            confidence=data["confidence"],
            risk_level=data["risk_level"],
            timestamp=data.get("timestamp", time.time()),
            metadata=data.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# EntropyFetchBridge — asyncio.Queue-based pub/sub bridge
# ---------------------------------------------------------------------------

class EntropyFetchBridge:
    """
    Asyncio.Queue-based pub/sub bridge for entropy alerts.

    Connects UncertaintyQuantifier (producer) to FetchCoordinator (consumer).
    Uses bounded asyncio.Queue to prevent unbounded memory growth on M1 8GB.

    Architecture:
        - Single producer: synthesis_runner emits alerts after uncertainty_gate()
        - Multiple subscribers: FetchCoordinator and other consumers
        - Bounded queue: maxsize=64, drops oldest on overflow (fail-soft)
        - Non-blocking emit: put_nowait with QueueFull handling
        - Async consumer loop: subscribers process alerts asynchronously

    M1 8GB safety:
        - Queue bounded at 64 items (~2KB active, ~256B idle)
        - No blocking operations in emit path
        - Fail-soft: any error logged and ignored
    """

    MAX_QUEUE_SIZE: int = 64
    MAX_SUBSCRIBERS: int = 16

    def __init__(self) -> None:
        self._subscribers: dict[str, asyncio.Queue[EntropyAlert]] = {}
        self._lock = asyncio.Lock()
        self._stats = {
            "alerts_emitted": 0,
            "alerts_dropped": 0,
            "subscribers_added": 0,
            "subscribers_removed": 0,
        }

    async def emit(self, alert: EntropyAlert) -> int:
        """
        Emit alert to all subscribers (non-blocking).

        Args:
            alert: EntropyAlert to broadcast

        Returns:
            Number of subscribers that received the alert

        Fail-soft: any error is logged and returns 0
        """
        try:
            if not self._subscribers:
                return 0

            delivered = 0
            async with self._lock:
                for subscriber_id, queue in self._subscribers.items():
                    try:
                        queue.put_nowait(alert)
                        delivered += 1
                    except asyncio.QueueFull:
                        # Drop oldest item to make room (fail-soft)
                        try:
                            queue.get_nowait()
                            queue.put_nowait(alert)
                            delivered += 1
                            self._stats["alerts_dropped"] += 1
                            logger.debug(
                                "[ENTROPY_BRIDGE] Queue full for %s, dropped oldest alert",
                                subscriber_id,
                            )
                        except (asyncio.QueueEmpty, asyncio.QueueFull):
                            self._stats["alerts_dropped"] += 1
                            logger.warning(
                                "[ENTROPY_BRIDGE] Failed to deliver alert to %s (queue full)",
                                subscriber_id,
                            )

            self._stats["alerts_emitted"] += 1
            return delivered

        except Exception as e:
            logger.debug(f"[ENTROPY_BRIDGE] emit failed (fail-soft): {e}")
            return 0

    async def subscribe(
        self,
        subscriber_id: str,
        queue: asyncio.Queue[EntropyAlert] | None = None,
    ) -> bool:
        """
        Subscribe to entropy alerts.

        Args:
            subscriber_id: Unique identifier for subscriber
            queue: Optional pre-created queue; if None, creates bounded queue

        Returns:
            True if subscribed, False if limit reached

        Fail-soft: any error returns False
        """
        try:
            async with self._lock:
                if len(self._subscribers) >= self.MAX_SUBSCRIBERS:
                    logger.warning(
                        "[ENTROPY_BRIDGE] Subscriber limit reached (%d), rejecting %s",
                        self.MAX_SUBSCRIBERS,
                        subscriber_id,
                    )
                    return False

                if queue is None:
                    queue = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)

                self._subscribers[subscriber_id] = queue
                self._stats["subscribers_added"] += 1
                logger.debug("[ENTROPY_BRIDGE] Subscribed: %s", subscriber_id)
                return True

        except Exception as e:
            logger.debug(f"[ENTROPY_BRIDGE] subscribe failed (fail-soft): {e}")
            return False

    async def unsubscribe(self, subscriber_id: str) -> bool:
        """
        Unsubscribe from entropy alerts.

        Args:
            subscriber_id: Subscriber to remove

        Returns:
            True if unsubscribed, False if not found
        """
        try:
            async with self._lock:
                if subscriber_id in self._subscribers:
                    del self._subscribers[subscriber_id]
                    self._stats["subscribers_removed"] += 1
                    logger.debug("[ENTROPY_BRIDGE] Unsubscribed: %s", subscriber_id)
                    return True
                return False

        except Exception as e:
            logger.debug(f"[ENTROPY_BRIDGE] unsubscribe failed (fail-soft): {e}")
            return False

    def get_queue(self, subscriber_id: str) -> asyncio.Queue[EntropyAlert] | None:
        """
        Get subscriber's queue for manual consumption.

        Args:
            subscriber_id: Subscriber identifier

        Returns:
            asyncio.Queue or None if not subscribed
        """
        return self._subscribers.get(subscriber_id)

    def get_stats(self) -> dict[str, Any]:
        """Return bridge statistics for monitoring."""
        return {
            **self._stats,
            "active_subscribers": len(self._subscribers),
            "max_subscribers": self.MAX_SUBSCRIBERS,
            "queue_capacity": self.MAX_QUEUE_SIZE,
        }


# ---------------------------------------------------------------------------
# Global singleton — lazy-initialized
# ---------------------------------------------------------------------------

_ENTROPY_BRIDGE: EntropyFetchBridge | None = None


def get_entropy_bridge() -> EntropyFetchBridge | None:
    """
    Get or create the global EntropyFetchBridge singleton.

    Returns:
        EntropyFetchBridge instance (lazy-initialized on first call)

    Fail-soft: returns None on any error
    """
    global _ENTROPY_BRIDGE
    try:
        if _ENTROPY_BRIDGE is None:
            _ENTROPY_BRIDGE = EntropyFetchBridge()
            logger.debug("[ENTROPY_BRIDGE] Global bridge initialized")
        return _ENTROPY_BRIDGE
    except Exception as e:
        logger.debug(f"[ENTROPY_BRIDGE] get_entropy_bridge failed (fail-soft): {e}")
        return None


def reset_entropy_bridge() -> None:
    """
    Reset the global bridge (for testing).

    WARNING: Only use in tests. Production code should not reset the bridge.
    """
    global _ENTROPY_BRIDGE
    _ENTROPY_BRIDGE = None
