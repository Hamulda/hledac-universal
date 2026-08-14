"""
brain/model_state.py — Sprint G2: Model State Observer
====================================================



Protocol + dataclass for exposing DeepHermes3Engine internal state
to ModelManager for informed unload decisions.

Problem: ModelManager creates DeepHermes3Engine via factory but has
no visibility into its internal state (idle time, KV cache memory,
batch queue depth). This leads to suboptimal unload decisions.

Solution: Event-driven observer pattern — DeepHermes3Engine publishes
state changes; ModelManager subscribes and reacts.

Usage:
    from hledac.universal.brain.model_state import ModelState, ModelStateObserver, get_state_observer

    observer = get_state_observer()
    observer.subscribe(my_model_manager.on_model_state_change)

    # Inside DeepHermes3Engine:
    observer.notify(ModelState(...))
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol
from collections.abc import Callable
from enum import Enum


# ---------------------------------------------------------------------------
# Model State Enum
# ---------------------------------------------------------------------------


class ModelLoadState(Enum):
    """Possible model load states."""
    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"
    IDLE = "idle"  # loaded but inactive
    BUSY = "busy"  # actively inferring


# ---------------------------------------------------------------------------
# Model State Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelState:
    """
    Immutable snapshot of DeepHermes3Engine internal state.

    Published to observers on every state change:
    - model load/unload
    - inference start/complete
    - batch queue depth change
    - idle detection

    Used by ModelManager for informed unload decisions.
    """
    model_id: str = "hermes"
    load_state: ModelLoadState = ModelLoadState.UNLOADED
    is_model_loaded: bool = False
    idle_seconds: float = 0.0
    last_inference_at: float | None = None
    kv_cache_memory_mb: float = 0.0
    batch_queue_depth: int = 0
    pending_futures: int = 0
    inference_active: bool = False
    timestamp: float = field(default_factory=time.monotonic)

    @property
    def is_idle(self) -> bool:
        """True if model is loaded but has been idle beyond threshold."""
        return self.load_state == ModelLoadState.IDLE

    @property
    def can_unload(self) -> bool:
        """True if unload would be safe (no active inference, queue empty)."""
        return (
            not self.inference_active
            and self.batch_queue_depth == 0
            and self.pending_futures == 0
        )

    def __repr__(self) -> str:
        return (
            f"ModelState(id={self.model_id!r}, state={self.load_state.value}, "
            f"idle={self.idle_seconds:.1f}s, kv_mb={self.kv_cache_memory_mb:.1f}, "
            f"queue={self.batch_queue_depth}, pending={self.pending_futures})"
        )


# ---------------------------------------------------------------------------
# Observer Protocol
# ---------------------------------------------------------------------------


class ModelStateObserver(Protocol):
    """
    Protocol for model state observers.

    Implement this to receive state updates from DeepHermes3Engine.
    ModelManager uses this to make informed load/unload decisions.
    """

    def on_model_state_change(self, state: ModelState) -> None:
        """
        Called when DeepHermes3Engine state changes.

        Args:
            state: Current model state snapshot
        """
        ...


# ---------------------------------------------------------------------------
# State Observer Implementation
# ---------------------------------------------------------------------------


class StateObserver:
    """
    Thread-safe state observer with subscription management.

    DeepHermes3Engine holds an instance and calls notify() on state changes.
    All subscribed observers receive the update.
    """

    __slots__ = ("_subscribers", "_last_state")

    def __init__(self) -> None:
        self._subscribers: list[Callable[[ModelState], None]] = []
        self._last_state: ModelState | None = None

    def subscribe(self, callback: Callable[[ModelState], None]) -> None:
        """
        Subscribe to state changes.

        Args:
            callback: Called with ModelState on every update.
        """
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[ModelState], None]) -> None:
        """Unsubscribe from state changes."""
        try:
            self._subscribers.remove(callback)
        except ValueError:  # noqa: BLE001
            pass

    def notify(self, state: ModelState) -> None:
        """
        Notify all subscribers of state change.

        Thread-safe: subscribers are called sequentially, errors are isolated.
        """
        self._last_state = state
        for cb in self._subscribers:
            try:
                cb(state)
            except Exception:  # noqa: BLE001
                pass  # Fail-soft: one observer error doesn't affect others

    @property
    def last_state(self) -> ModelState | None:
        """Get the most recent state, or None if never updated."""
        return self._last_state


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_state_observer: StateObserver | None = None


def get_state_observer() -> StateObserver:
    """
    Get the module-level StateObserver singleton.

    Usage:
        observer = get_state_observer()
        observer.subscribe(my_callback)
    """
    global _state_observer
    if _state_observer is None:
        _state_observer = StateObserver()
    return _state_observer


def reset_state_observer() -> None:
    """Reset the singleton (for testing)."""
    global _state_observer
    _state_observer = None
