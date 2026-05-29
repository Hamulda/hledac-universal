"""
Model Inference Guard — model-level circuit breaker.

Prevents repeated model load/inference crash loops on M1 8GB.
Bounded, fail-safe, no external deps.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum

# Internal imports only — no new dependencies

# Failure taxonomy
class FailureKind(str):
    LOAD_ERROR = "load_error"
    MEMORY_ADMISSION_BLOCKED = "memory_admission_blocked"
    OOM = "oom"
    METAL_ERROR = "metal_error"
    TIMEOUT = "timeout"
    INFERENCE_ERROR = "inference_error"
    UNKNOWN_ERROR = "unknown_error"


class GuardState(StrEnum):
    CLOSED = "closed"      # normal operation, failures tracked
    OPEN = "open"          # blocked, cooling down
    HALF_OPEN = "half_open"  # testing recovery after cooldown


# Config constants
_FAILURE_THRESHOLD = 3
_FAILURE_WINDOW_S = 60.0
_COOLDOWN_S = 30.0
_MAX_TRACKED_MODELS = 16


@dataclass(frozen=True)
class ModelGuardDecision:
    allowed: bool
    model_key: str
    state: str
    retry_after_s: float
    reason: str


@dataclass(frozen=True)
class ModelGuardSnapshot:
    model_key: str
    state: str
    failure_count: int
    opened_at_monotonic: float
    retry_after_s: float
    last_failure_kind: str


class _ModelBreaker:
    """Single-model breaker state. Not a dataclass — mutable internal state."""
    __slots__ = ("failure_count", "failure_timestamps", "state", "opened_at", "retry_after_s", "last_failure_kind")

    def __init__(self) -> None:
        self.failure_count: int = 0
        self.failure_timestamps: list[float] = []
        self.state: GuardState = GuardState.CLOSED
        self.opened_at: float = 0.0
        self.retry_after_s: float = 0.0
        self.last_failure_kind: str = ""


class ModelInferenceGuard:
    """
    Bounded model-level circuit breaker.

    Tracks failures per model_key within a sliding window.
    3 failures / 60s → OPEN for 30s → HALF_OPEN → CLOSED on success.
    Registry bounded to MAX_TRACKED_MODELS with LRU-style eviction.
    """

    def __init__(self) -> None:
        self._breakers: dict[str, _ModelBreaker] = {}
        self._lock = asyncio.Lock()

    def _now_monotonic(self) -> float:
        """Monotonic time for in-process timing."""
        return time.monotonic()

    def _evict_if_needed(self) -> None:
        """Evict oldest model if at capacity — simple LRU-ish eviction."""
        if len(self._breakers) >= _MAX_TRACKED_MODELS:
            oldest_key = min(
                self._breakers,
                key=lambda k: self._breakers[k].opened_at or self._now_monotonic(),
            )
            self._breakers.pop(oldest_key, None)

    def check_model_allowed(self, model_key: str) -> ModelGuardDecision:
        """
        Synchronous check — returns decision immediately.
        Does NOT acquire lock (breakers are safe for concurrent reads).
        """
        now = self._now_monotonic()
        breaker = self._breakers.get(model_key)

        if breaker is None:
            return ModelGuardDecision(
                allowed=True,
                model_key=model_key,
                state=GuardState.CLOSED.value,
                retry_after_s=0.0,
                reason="model not tracked, allowed",
            )

        # Evict stale OPEN/HALF_OPEN after cooldown passed
        if breaker.state == GuardState.OPEN:
            if now >= breaker.opened_at + _COOLDOWN_S:
                breaker.state = GuardState.HALF_OPEN
                breaker.retry_after_s = 0.0
                return ModelGuardDecision(
                    allowed=True,
                    model_key=model_key,
                    state=GuardState.HALF_OPEN.value,
                    retry_after_s=0.0,
                    reason="cooldown elapsed, testing recovery",
                )
            return ModelGuardDecision(
                allowed=False,
                model_key=model_key,
                state=GuardState.OPEN.value,
                retry_after_s=breaker.retry_after_s,
                reason=f"model inference blocked: {model_key}, retry after {breaker.retry_after_s:.1f}s",
            )

        if breaker.state == GuardState.HALF_OPEN:
            return ModelGuardDecision(
                allowed=True,
                model_key=model_key,
                state=GuardState.HALF_OPEN.value,
                retry_after_s=0.0,
                reason="half-open, allowing test inference",
            )

        return ModelGuardDecision(
            allowed=True,
            model_key=model_key,
            state=GuardState.CLOSED.value,
            retry_after_s=0.0,
            reason="closed, allowed",
        )

    def record_success(self, model_key: str) -> None:
        """Record successful load/inference — resets failure count."""
        breaker = self._breakers.get(model_key)
        if breaker is None:
            return
        breaker.failure_count = 0
        breaker.failure_timestamps.clear()
        breaker.state = GuardState.CLOSED
        breaker.opened_at = 0.0
        breaker.retry_after_s = 0.0
        breaker.last_failure_kind = ""

    def record_failure(self, model_key: str, failure_kind: str) -> None:
        """Record failure — may open the breaker."""
        now = self._now_monotonic()
        self._evict_if_needed()

        if model_key not in self._breakers:
            self._breakers[model_key] = _ModelBreaker()

        breaker = self._breakers[model_key]
        breaker.last_failure_kind = failure_kind

        # Sliding window: remove timestamps outside window
        cutoff = now - _FAILURE_WINDOW_S
        breaker.failure_timestamps = [ts for ts in breaker.failure_timestamps if ts >= cutoff]

        breaker.failure_timestamps.append(now)
        breaker.failure_count = len(breaker.failure_timestamps)

        if breaker.state == GuardState.HALF_OPEN:
            # Failure in half-open → immediate OPEN
            breaker.state = GuardState.OPEN
            breaker.opened_at = now
            breaker.retry_after_s = _COOLDOWN_S
            return

        if breaker.failure_count >= _FAILURE_THRESHOLD:
            breaker.state = GuardState.OPEN
            breaker.opened_at = now
            breaker.retry_after_s = _COOLDOWN_S

    def get_snapshot(self, model_key: str) -> ModelGuardSnapshot | None:
        """Return snapshot for one model or None."""
        breaker = self._breakers.get(model_key)
        if breaker is None:
            return None
        return ModelGuardSnapshot(
            model_key=model_key,
            state=breaker.state.value,
            failure_count=breaker.failure_count,
            opened_at_monotonic=breaker.opened_at,
            retry_after_s=breaker.retry_after_s,
            last_failure_kind=breaker.last_failure_kind,
        )

    def get_all_snapshots(self) -> list[ModelGuardSnapshot]:
        """Return snapshots for all tracked models."""
        return [s for s in (self.get_snapshot(k) for k in self._breakers) if s is not None]

    def clear_all(self) -> None:
        """Clear all breaker state — for testing."""
        self._breakers.clear()


# Module-level singleton
_GUARD: ModelInferenceGuard | None = None


def get_guard() -> ModelInferenceGuard:
    global _GUARD
    if _GUARD is None:
        _GUARD = ModelInferenceGuard()
    return _GUARD


def check_model_allowed(model_key: str) -> ModelGuardDecision:
    return get_guard().check_model_allowed(model_key)


def record_model_success(model_key: str) -> None:
    get_guard().record_success(model_key)


def record_model_failure(model_key: str, *, failure_kind: str) -> None:
    get_guard().record_failure(model_key, failure_kind)


def get_model_guard_snapshot(model_key: str) -> ModelGuardSnapshot | None:
    return get_guard().get_snapshot(model_key)


def get_all_model_guard_snapshots() -> list[ModelGuardSnapshot]:
    return get_guard().get_all_snapshots()


def clear_model_guards() -> None:
    get_guard().clear_all()


def classify_failure_kind(exc: Exception) -> str:
    """Classify exception into failure taxonomy."""
    msg = str(exc).lower()

    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"

    if "memory" in msg and ("admission" in msg or "blocked" in msg or "pressure" in msg or "uma" in msg or "8gb" in msg):
        return "memory_admission_blocked"

    if "oom" in msg or "out of memory" in msg or "cannot allocate" in msg or "allocation failed" in msg:
        return "oom"

    if "metal" in msg or "gpu" in msg:
        return "metal_error"

    if "mlx" in msg and ("lm" in msg or "generate" in msg or "inference" in msg):
        return "inference_error"

    if "timeout" in msg or "timed out" in msg:
        return "timeout"

    if "load" in msg or "initialize" in msg or "load_model" in msg:
        return "load_error"

    if "inference" in msg or "generate" in msg or "mlx_lm" in msg:
        return "inference_error"

    return "unknown_error"
