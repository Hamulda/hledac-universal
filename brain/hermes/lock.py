"""
brain/hermes/lock.py — Metal inference lock with diagnostics (ISSUE #16, solution #3)

Thin, diagnostics-rich facade over the canonical
``hledac.universal._core.mlx_inference_lock.MLXInferenceLock``.

WHY A WRAPPER, NOT A REPLACEMENT
    M1 8GB has a single Metal command queue, so ALL MLX inference MUST be
    serialized (semaphore limit=1). The canonical lock already enforces this
    contract correctly. This module adds OBSERVABILITY on top of it:
      - acquire contention timing (how often/long callers wait)
      - a p95 wait histogram (bounded ring buffer)
      - a capability_score snapshot from ``brain.hermes.capability_gate``
    without changing the serialization contract or the failure semantics.

USAGE
    lock = get_metal_lock()
    async with lock.acquire():
        result = await engine.generate(prompt)
    print(lock.get_diagnostics())
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from hledac.universal._core.mlx_inference_lock import (
    MLXInferenceLock,
    _get_inference_lock,
)

logger = logging.getLogger(__name__)

# Bounded ring buffer for recent wait samples (p95 histogram, no unbounded growth).
_RECENT_WAITS_MAX = 64
# Contention threshold: waits longer than this count as "contended" acquires.
_CONTENTION_EPS_S = 0.001


class MetalInferenceLock:
    """Diagnostics-rich facade over the canonical MLX inference lock."""

    __slots__ = (
        "_lock",
        "_acquires",
        "_contention",
        "_total_wait_s",
        "_recent_waits",
        "_guard",
    )

    def __init__(self, lock: MLXInferenceLock | None = None) -> None:
        self._lock = lock or _get_inference_lock()
        self._acquires = 0
        self._contention = 0
        self._total_wait_s = 0.0
        self._recent_waits: list[float] = []
        self._guard = threading.Lock()

    @property
    def raw(self) -> MLXInferenceLock:
        """The underlying canonical lock (for advanced use)."""
        return self._lock

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """Acquire the single MLX inference slot, recording contention timing."""
        self._acquires += 1
        t0 = time.monotonic()
        async with self._lock.acquire():
            waited = time.monotonic() - t0
            with self._guard:
                self._total_wait_s += waited
                if waited > _CONTENTION_EPS_S:
                    self._contention += 1
                    self._recent_waits.append(waited)
                    if len(self._recent_waits) > _RECENT_WAITS_MAX:
                        self._recent_waits.pop(0)
            yield

    def get_diagnostics(self) -> dict[str, Any]:
        """Snapshot of lock contention + capability + backend stats."""
        with self._guard:
            acquires = self._acquires
            contention = self._contention
            avg_wait = (self._total_wait_s / acquires) if acquires else 0.0
            waits = sorted(self._recent_waits)
            p95 = waits[int(len(waits) * 0.95) - 1] if waits else 0.0
            recent = list(waits)
        try:
            from hledac.universal.brain.hermes.capability_gate import (
                CAPABILITY_THRESHOLD,
                rust_capability_score,
            )

            cap_score = rust_capability_score()
            cap_ok = cap_score >= CAPABILITY_THRESHOLD
        except Exception:  # noqa: BLE001
            cap_score = None
            cap_ok = None
        return {
            "acquires": acquires,
            "contention_events": contention,
            "contention_ratio": (contention / acquires) if acquires else 0.0,
            "avg_wait_s": avg_wait,
            "p95_wait_s": p95,
            "recent_waits_s": recent,
            "capability_score": cap_score,
            "capability_ok": cap_ok,
            "backend_stats": self._lock.get_stats(),
        }


_lock_singleton: MetalInferenceLock | None = None
_lock_guard = threading.Lock()


def get_metal_lock() -> MetalInferenceLock:
    """Return the process-wide MetalInferenceLock (DCLP singleton)."""
    global _lock_singleton
    if _lock_singleton is None:
        with _lock_guard:
            if _lock_singleton is None:
                _lock_singleton = MetalInferenceLock()
    return _lock_singleton
