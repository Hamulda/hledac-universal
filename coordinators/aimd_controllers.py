"""
P2-3: AIMD Controllers — Enrichment a Extraction
=================================================

Role: Univerzální AIMD controllery pro enrichment a extraction fáze.
Stejný pattern jako FetchCoordinator AIMDWindow, ale scoped pro konkrétní fáze.

AIMD (Additive Increase/Multiplicative Decrease):
- Na success: +ADDITIVE_INCREMENT (až do MAX)
- Na failure: ×DECREASE_FACTOR (až do MIN)

Pro M1 8GB:
- Enrichment: CPU-bound, ceiling=16 workers
- Extraction: I/O-bound (DuckDB write), ceiling=8 workers
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Enrichment AIMD — CPU-bound workers
AIMD_ENRICH_ADDITIVE_INCREMENT = 1  # konzervativní, CPU-bound
AIMD_ENRICH_DECREASE_FACTOR = 0.75
AIMD_ENRICH_MIN = 1
AIMD_ENRICH_MAX = 16
AIMD_ENRICH_SUCCESS_THRESHOLD = 2

# Extraction AIMD — I/O-bound (DuckDB write)
AIMD_EXTRACT_ADDITIVE_INCREMENT = 2
AIMD_EXTRACT_DECREASE_FACTOR = 0.75
AIMD_EXTRACT_MIN = 1
AIMD_EXTRACT_MAX = 8
AIMD_EXTRACT_SUCCESS_THRESHOLD = 2

# Fetch AIMD — existující v FetchCoordinator, replicated pro stage parity
AIMD_FETCH_ADDITIVE_INCREMENT = 2
AIMD_FETCH_DECREASE_FACTOR = 0.75
AIMD_FETCH_MIN = 1
AIMD_FETCH_MAX = 25
AIMD_FETCH_SUCCESS_THRESHOLD = 2


@dataclass(slots=True)
class AIMDController:
    """
    Univerzální AIMD controller.

    Thread-safe: všechny mutace pod asyncio.Lock.

    Usage:
        controller = AIMDController(
            min_value=1,
            max_value=16,
            additive_increment=1,
            decrease_factor=0.75,
            success_threshold=2,
            name="enrich",
        )
        window = await controller.on_success()  # increase
        window = await controller.on_failure()  # decrease
    """
    min_value: float
    max_value: float
    additive_increment: float
    decrease_factor: float
    success_threshold: int
    name: str

    _window: float = field(init=False, default=1.0)
    _successes: int = field(init=False, default=0)
    _failures: int = field(init=False, default=0)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _window_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _stats: dict[str, int] = field(
        default_factory=lambda: {
            "increases": 0,
            "decreases": 0,
            "window_changes": 0,
            "successes": 0,
            "failures": 0,
        },
        init=False,
    )

    def __post_init__(self) -> None:
        # Set initial window to midpoint for faster ramp-up
        self._window = min(self.max_value, max(self.min_value, self.additive_increment * 2))
        self._successes = 0
        self._failures = 0
        self._lock = asyncio.Lock()
        self._window_lock = asyncio.Lock()

    @property
    def window(self) -> float:
        return self._window

    @property
    def successes(self) -> int:
        return self._successes

    @property
    def failures(self) -> int:
        return self._failures

    @property
    def stats(self) -> dict[str, int]:
        return self._stats.copy()

    async def on_success(self, multiplier: float = 1.0) -> tuple[float, int]:
        """
        Record one success, potentially increase window.

        Returns:
            (new_window, remaining_successes)
        """
        async with self._lock:
            self._successes += 1
            new_successes = self._successes
            self._stats["successes"] += 1

        if new_successes < self.success_threshold:
            return (self._window, new_successes)

        async with self._window_lock:
            if self._successes < self.success_threshold:
                return (self._window, self._successes)
            self._successes = 0
            old = self._window
            self._window = min(
                self._window + self.additive_increment * multiplier,
                self.max_value,
            )
            if self._window != old:
                self._stats["increases"] += 1
                self._stats["window_changes"] += 1
            return (self._window, 0)

    async def on_failure(self, uma_state: str = "ok") -> tuple[float, int]:
        """
        Record one failure, decrease window.

        Args:
            uma_state: UMA state string for decrease factor lookup.
               'ok' = no extra decrease
               'soft_warn' = 0.75 factor
               'warn' = 0.5 factor
               'critical' = 0.25 factor
               'emergency' = 0.0 factor (stop)

        Returns:
            (new_window, failure_count)
        """
        async with self._lock:
            self._failures += 1
            new_failures = self._failures
            self._stats["failures"] += 1

        async with self._window_lock:
            # UMA-based extra decrease on top of base factor
            uma_factors = {
                "ok": 1.0,
                "soft_warn": 0.75,
                "warn": 0.5,
                "critical": 0.25,
                "emergency": 0.0,
            }
            uma_factor = uma_factors.get(uma_state, 1.0)
            decrease_factor = self.decrease_factor * uma_factor

            old = self._window
            self._window = max(self._window * decrease_factor, self.min_value)
            if self._window != old:
                self._stats["decreases"] += 1
                self._stats["window_changes"] += 1
                self._successes = 0  # Reset on decrease

        return (self._window, new_failures)

    async def set_window(self, new_window: float) -> None:
        """Set window directly (for backpressure clamping from governor)."""
        async with self._window_lock:
            self._window = float(max(self.min_value, min(self.max_value, new_window)))
            self._stats["window_changes"] += 1

    def reset_successes(self) -> None:
        """Reset success counter (called externally after window increase)."""
        self._successes = 0


# ----------------------------------------------------------------------
# Pre-built controller instances (factory)
# ----------------------------------------------------------------------


def make_enrich_aimd() -> AIMDController:
    """Factory for enrichment AIMD controller."""
    return AIMDController(
        min_value=AIMD_ENRICH_MIN,
        max_value=AIMD_ENRICH_MAX,
        additive_increment=AIMD_ENRICH_ADDITIVE_INCREMENT,
        decrease_factor=AIMD_ENRICH_DECREASE_FACTOR,
        success_threshold=AIMD_ENRICH_SUCCESS_THRESHOLD,
        name="enrich",
    )


def make_extract_aimd() -> AIMDController:
    """Factory for extraction AIMD controller."""
    return AIMDController(
        min_value=AIMD_EXTRACT_MIN,
        max_value=AIMD_EXTRACT_MAX,
        additive_increment=AIMD_EXTRACT_ADDITIVE_INCREMENT,
        decrease_factor=AIMD_EXTRACT_DECREASE_FACTOR,
        success_threshold=AIMD_EXTRACT_SUCCESS_THRESHOLD,
        name="extract",
    )


def make_fetch_aimd() -> AIMDController:
    """Factory for fetch AIMD controller (mirrors FetchCoordinator AIMD)."""
    return AIMDController(
        min_value=AIMD_FETCH_MIN,
        max_value=AIMD_FETCH_MAX,
        additive_increment=AIMD_FETCH_ADDITIVE_INCREMENT,
        decrease_factor=AIMD_FETCH_DECREASE_FACTOR,
        success_threshold=AIMD_FETCH_SUCCESS_THRESHOLD,
        name="fetch",
    )
