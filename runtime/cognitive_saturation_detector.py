"""
CognitiveSaturationDetector — Sprint-level entity discovery rate monitor.

Detects cognitive saturation by tracking unique entity discoveries over a sliding

window. When d(unique_IOC)/dt → 0 for persistence_s seconds (after minimum
active time), triggers automatic WINDUP transition.

Design rationale:
- Sliding window: 3-minute window captures discovery rate without memory bloat
- Zero threshold: Simple, robust — triggers when discovery stops entirely
- Minimum active: 5-minute cooldown prevents premature windup during warmup
- xxh3_64: Fast, non-cryptographic hash — 10-20× faster than SHA256
- __slots__: Zero dict overhead per instance — M1 8GB safe

Environment variables:
    HLEDAC_ENABLE_COGNITIVE_SATURATION: Set to 0 to disable (default: 1)
    HLEDAC_CSD_WINDOW_S: Sliding window in seconds (default: 180.0)
    HLEDAC_CSD_PERSIST_S: Persistence threshold in seconds (default: 180.0)
    HLEDAC_CSD_MIN_ACTIVE_S: Minimum active time before trigger (default: 300.0)

Example:
    detector = CognitiveSaturationDetector()
    # In fetch loop:
    detector.report_entity_discovery("evil.com", "domain")
    # In lifecycle tick:
    if detector.should_enter_windup(elapsed_active_s=600.0):
        lifecycle.transition_to(SprintPhase.WINDUP)
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from typing import TYPE_CHECKING
from _core import aclose

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_WINDOW_S: float = float(os.environ.get("HLEDAC_CSD_WINDOW_S", "180.0"))
_PERSIST_S: float = float(os.environ.get("HLEDAC_CSD_PERSIST_S", "180.0"))
_MIN_ACTIVE_S: float = float(os.environ.get("HLEDAC_CSD_MIN_ACTIVE_S", "300.0"))
_ENABLED: bool = os.environ.get("HLEDAC_ENABLE_COGNITIVE_SATURATION", "1") != "0"

# Maximum entries in deque (4 per second × window_s, capped for memory)
_MAX_ENTRIES: int = int(_WINDOW_S * 4)


# ── Fast hashing ───────────────────────────────────────────────────────────────

def _get_hash_func():
    """Return fastest available hash function.
    
    Prefers xxhash.xxh3_64 (10-20× faster than blake2b) when available.
    Falls back to hashlib.blake2b for cross-platform compatibility.
    """
    try:
        import xxhash
        return lambda s: xxhash.xxh3_64(s.encode()).intdigest()
    except ImportError:  # noqa: BLE001
        pass
    
    # Fallback: blake2b is fast and available in stdlib
    import hashlib
    return lambda s: int.from_bytes(
        hashlib.blake2b(s.encode(), digest_size=8).digest(),
        "little"
    )


_hash_entity: callable = _get_hash_func()


# ── Detector class ─────────────────────────────────────────────────────────────

class CognitiveSaturationDetector:
    """
    Sliding-window entity discovery rate monitor for sprint-level cognitive saturation.

    Tracks unique entity discoveries using a bounded deque with timestamp-indexed
    entries. Triggers when no new unique entities appear in the window for
    PERSIST_S seconds, after MIN_ACTIVE_S of ACTIVE phase.

    M1 8GB safe:
    - __slots__ — zero dict overhead per instance
    - Bounded deque — max ~720 entries (3 min × 4/sec)
    - O(1) insert and O(1) duplicate check via set
    - No allocations in hot path (pre-computed hashes)

    Thread-safety: This class is designed for single-threaded use within the
    sprint lifecycle tick loop. Concurrent access from multiple threads/coroutines
    requires external synchronization.
    """

    __slots__ = (
        "_enabled",
        "_window_s",
        "_persist_s",
        "_min_active_s",
        "_entries",           # deque[(entity_hash: int, timestamp: float)]
        "_seen_hashes",      # set[int] — hashes in current window for O(1) dedup
        "_zero_since",       # float | None — when discovery rate dropped to zero
        "_triggered",        # bool — whether detector has fired
        "_total_reports",    # int — telemetry: total reports seen
        "_unique_reports",   # int — telemetry: unique entities reported
    )

    def __init__(
        self,
        *,
        enabled: bool = _ENABLED,
        window_s: float | None = None,
        persist_s: float | None = None,
        min_active_s: float | None = None,
    ) -> None:
        """
        Initialize detector with optional parameter overrides.

        Args:
            enabled: Whether detection is active (can be disabled via env var).
            window_s: Sliding window duration in seconds (default: 180.0).
            persist_s: Seconds at zero rate before trigger (default: 180.0).
            min_active_s: Minimum ACTIVE phase before trigger (default: 300.0).

        Raises:
            ValueError: If window_s, persist_s, or min_active_s are negative.
        """
        # Validate parameters
        _window_s = window_s if window_s is not None else _WINDOW_S
        _persist_s = persist_s if persist_s is not None else _PERSIST_S
        _min_active_s = min_active_s if min_active_s is not None else _MIN_ACTIVE_S

        if _window_s <= 0:
            raise ValueError(f"window_s must be positive, got {_window_s}")
        if _persist_s < 0:
            raise ValueError(f"persist_s must be non-negative, got {_persist_s}")
        if _min_active_s < 0:
            raise ValueError(f"min_active_s must be non-negative, got {_min_active_s}")

        self._enabled: bool = enabled
        self._window_s: float = _window_s
        self._persist_s: float = _persist_s
        self._min_active_s: float = _min_active_s
        self._entries: deque[tuple[int, float]] = deque(maxlen=_MAX_ENTRIES)
        self._seen_hashes: set[int] = set()
        self._zero_since: float | None = None
        self._triggered: bool = False
        self._total_reports: int = 0
        self._unique_reports: int = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def report_entity_discovery(self, entity_value: str, ioc_type: str = "") -> None:
        """
        Report a new entity discovery for saturation tracking.

        Call this method whenever a non-duplicate entity enters the write path
        (e.g., DuckDB insert, evidence creation). The entity is hashed and
        tracked in the sliding window.

        Args:
            entity_value: The entity value (e.g., domain, IP, URL).
            ioc_type: Optional IOC type string for debugging/logging.
        """
        if not self._enabled or self._triggered:
            return

        self._total_reports += 1
        now = time.monotonic()

        # Compute hash for O(1) dedup
        entity_hash = _hash_entity(f"{ioc_type}:{entity_value}" if ioc_type else entity_value)

        # Check if this is a new unique entity (not in current window)
        if entity_hash in self._seen_hashes:
            return

        # Evict expired entries FIRST before adding new entry
        self._evict_expired(now)

        # Manual maxlen handling: if deque is full, evict oldest before adding
        # This ensures we always discard the hash of evicted entries from _seen_hashes
        if len(self._entries) >= self._entries.maxlen:
            if self._entries:
                oldest_hash, _ = self._entries[0]
                self._entries.popleft()
                self._seen_hashes.discard(oldest_hash)

        # New unique entity — add to window
        self._unique_reports += 1
        self._entries.append((entity_hash, now))
        self._seen_hashes.add(entity_hash)

        # Reset zero-rate tracking
        self._zero_since = None

        logger.debug(
            "[COGNITIVE_SATURATION] New unique entity: type=%s value=%s (unique=%d in window)",
            ioc_type or "unknown",
            entity_value[:64] if len(entity_value) > 64 else entity_value,
            len(self._seen_hashes),
        )

    def should_enter_windup(self, elapsed_active_s: float, now_monotonic: float | None = None) -> bool:
        """
        Check if sprint should transition to WINDUP due to cognitive saturation.

        Returns True when:
        1. Detector is enabled and not already triggered
        2. Minimum active time (MIN_ACTIVE_S) has elapsed
        3. No new unique entities in the sliding window for PERSIST_S seconds

        Args:
            elapsed_active_s: Seconds spent in ACTIVE phase so far.
            now_monotonic: Optional monotonic clock override for testing.

        Returns:
            True if cognitive saturation detected and WINDUP should begin.
        """
        if not self._enabled:
            return False

        if self._triggered:
            return True

        now = now_monotonic if now_monotonic is not None else time.monotonic()

        # Enforce minimum active time
        if elapsed_active_s < self._min_active_s:
            return False

        # Evict expired entries first
        self._evict_expired(now)

        # Check if window has any entries
        if len(self._entries) > 0:
            # We have recent discoveries — not saturated
            self._zero_since = None
            return False

        # Window is empty — track how long we've been at zero
        if self._zero_since is None:
            self._zero_since = now
            logger.debug(
                "[COGNITIVE_SATURATION] Zero-discovery period started at t=%.1fs "
                "(elapsed_active_s=%.1f, window_s=%.1f)",
                now, elapsed_active_s, self._window_s,
            )
            return False

        # Check if we've been at zero long enough
        zero_duration = now - self._zero_since
        if zero_duration >= self._persist_s:
            self._triggered = True
            logger.warning(
                "[COGNITIVE_SATURATION] SATURATION TRIGGERED — "
                "No new unique entities for %.1fs (%.1f min) in %.1f min window. "
                "elapsed_active_s=%.1f, total_reports=%d, unique_reports=%d. "
                "Transitioning to WINDUP to conserve resources.",
                zero_duration,
                zero_duration / 60.0,
                self._window_s / 60.0,
                elapsed_active_s,
                self._total_reports,
                self._unique_reports,
            )
            return True

        return False

    def reset(self) -> None:
        """Reset detector state for new sprint. Call at sprint start."""
        self._entries.clear()
        self._seen_hashes.clear()
        self._zero_since = None
        self._triggered = False
        self._total_reports = 0
        self._unique_reports = 0
        logger.debug("[COGNITIVE_SATURATION] Detector reset for new sprint.")

    @property
    def stats(self) -> dict:
        """Return telemetry dict for observability."""
        now = time.monotonic()
        zero_duration: float | None = None
        if self._zero_since is not None:
            zero_duration = now - self._zero_since
        return {
            "enabled": self._enabled,
            "triggered": self._triggered,
            "window_s": self._window_s,
            "persist_s": self._persist_s,
            "min_active_s": self._min_active_s,
            "entries_in_window": len(self._entries),
            "unique_in_window": self._count_unique_in_window(now),
            "total_reports": self._total_reports,
            "unique_reports": self._unique_reports,
            "zero_since": self._zero_since,
            "zero_duration_s": zero_duration,
        }

    # ── Internal ───────────────────────────────────────────────────────────────

    def _evict_expired(self, now: float) -> None:
        """Remove entries outside the sliding window and clean seen_hashes."""
        window_start = now - self._window_s

        # Remove expired entries from deque (front of deque is oldest)
        while self._entries and self._entries[0][1] < window_start:
            expired_hash, _ = self._entries.popleft()
            self._seen_hashes.discard(expired_hash)

    def _count_unique_in_window(self, now: float | None = None) -> int:
        """Return count of unique entities in the current window."""
        if now is None:
            now = time.monotonic()
        self._evict_expired(now)
        return len(self._seen_hashes)

    def __repr__(self) -> str:
        """Debug representation for logging and introspection."""
        return (
            f"CognitiveSaturationDetector("
            f"enabled={self._enabled}, "
            f"triggered={self._triggered}, "
            f"window={self._window_s}s, "
            f"persist={self._persist_s}s, "
            f"min_active={self._min_active_s}s, "
            f"in_window={len(self._entries)}, "
            f"unique_total={self._unique_reports}"
            f")"
        )


# ── Global Registry ────────────────────────────────────────────────────────────
# Allows any module to report entity discoveries without importing the detector directly.

_CSD_REGISTRY: CognitiveSaturationDetector | None = None


def set_cognitive_saturation_detector(detector: CognitiveSaturationDetector | None) -> None:
    """Register the global cognitive saturation detector instance."""
    global _CSD_REGISTRY
    _CSD_REGISTRY = detector
    logger.debug("[COGNITIVE_SATURATION] Detector registered: %s", detector)


def get_cognitive_saturation_detector() -> CognitiveSaturationDetector | None:
    """Get the registered global cognitive saturation detector instance."""
    return _CSD_REGISTRY


def report_entity_discovery(entity_value: str, ioc_type: str = "") -> None:
    """
    Global convenience function to report an entity discovery.

    This function can be called from anywhere in the codebase without
    needing a reference to the detector instance.

    Args:
        entity_value: The entity value (e.g., domain, IP, URL).
        ioc_type: Optional IOC type string for debugging/logging.

    Example:
        from hledac.universal.runtime.cognitive_saturation_detector import report_entity_discovery
        report_entity_discovery("evil.com", "domain")
    """
    detector = _CSD_REGISTRY
    if detector is not None:
        try:
            detector.report_entity_discovery(entity_value, ioc_type)
        except Exception as e:
            logger.debug("[COGNITIVE_SATURATION] report_entity_discovery failed: %s", e)
