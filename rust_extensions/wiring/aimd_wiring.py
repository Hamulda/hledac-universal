"""
AIMD Controller Wiring
======================

Wires rust_extensions/src/aimd_controller.rs (PyAIMDController) to:
- coordinators/performance_coordinator.py (HTTP fetch concurrency)

Purpose:
- Lock-free AIMD (Additive Increase, Multiplicative Decrease) concurrency control
- Atomic window updates via Rust std::sync::atomic
- M1 8GB safe: ~128 bytes per controller, zero allocations on hot path

Integration Points:
- PerformanceCoordinator for adaptive HTTP fetch concurrency
- FetchCoordinator for per-domain concurrency management

C13: AIMD adaptive concurrency controller for HTTP fetches.

Usage:
    from rust_extensions.wiring.aimd_wiring import aimd_wired, get_aimd_window

    # Get singleton controller
    aimd = aimd_wired()

    # Acquire slot before fetch
    window, active = aimd.acquire()

    # Record outcome after fetch
    if success:
        aimd.record_success()
    else:
        aimd.record_failure(uma_state="ok")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

logger = logging.getLogger(__name__)

# Import the integration layer
from rust_extensions.integrations import get_aimd

# Create singleton instance with M1 8GB safe defaults
# C13: initial_window=4, min_window=1, max_window=16
_aimd = get_aimd(initial_window=4.0, min_window=1.0, max_window=16.0)


def aimd_wired():
    """
    Get the wired AIMD integration singleton.

    Returns:
        AIMDIntegration instance with Rust backend (or Python fallback)
    """
    return _aimd


def get_aimd_window() -> float:
    """
    Get current AIMD window size.

    Returns:
        Current concurrency window (float)
    """
    return _aimd.window


def get_aimd_active() -> int:
    """
    Get current active slot count.

    Returns:
        Number of currently active slots
    """
    return _aimd.active


def is_aimd_available() -> bool:
    """
    Check if Rust AIMD controller is available.

    Returns:
        True if Rust backend, False if Python fallback
    """
    return _aimd.available


def acquire_fetch_slot() -> tuple[float, int]:
    """
    Acquire an AIMD slot for HTTP fetch.

    Should be called before initiating an HTTP fetch.

    Returns:
        Tuple of (window, active_count)
    """
    return _aimd.acquire()


def record_fetch_success() -> tuple[float, int]:
    """
    Record successful HTTP fetch.

    Should be called after successful fetch completion.

    Returns:
        Tuple of (new_window, active_count)
    """
    return _aimd.record_success()


def record_fetch_failure(uma_state: str = "ok") -> tuple[float, int]:
    """
    Record failed HTTP fetch.

    Should be called after fetch failure.

    Args:
        uma_state: Current UMA state for decrease factor selection
                   ("ok"=×0.75, "pressure"=×0.5, "critical"=×0.25)

    Returns:
        Tuple of (new_window, active_count)
    """
    return _aimd.record_failure(uma_state)


def release_fetch_slot() -> tuple[float, int]:
    """
    Release fetch slot without recording success/failure.

    Use for cancelled/timeout fetches.

    Returns:
        Tuple of (window, active_count)
    """
    return _aimd.record_release()


def apply_backpressure(window: float) -> None:
    """
    Apply external backpressure to clamp AIMD window.

    Args:
        window: New window ceiling
    """
    _aimd.set_window(window)


def blitz_boost(target: float) -> float:
    """
    BLITZ-13: Boost window to target for rapid scaling.

    Resets success counter so additive increase starts from zero.

    Args:
        target: Target window size

    Returns:
        Actual clamped target
    """
    return _aimd.blitz_boost(target)


def get_aimd_telemetry() -> dict[str, Any]:
    """
    Get comprehensive AIMD telemetry for monitoring.

    Returns:
        Dict with window, active, rust_available, and stats
    """
    return _aimd.get_telemetry()


def with_aimd_semaphore(
    max_concurrent: int | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator to wrap async functions with AIMD concurrency control.

    Args:
        max_concurrent: Override max concurrent (uses AIMD window if None)

    Example:
        @with_aimd_semaphore()
        async def fetch_url(url: str) -> Response:
            ...
    """
    import asyncio
    import functools

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Wait for capacity if needed (check BEFORE acquiring)
            if max_concurrent is not None:
                while True:
                    _, active = acquire_fetch_slot()
                    if active <= max_concurrent:
                        break
                    release_fetch_slot()
                    await asyncio.sleep(0.05)

            # Now acquire the slot
            window, active = acquire_fetch_slot()

            try:
                result = await func(*args, **kwargs)
                record_fetch_success()
                return result
            except Exception:
                record_fetch_failure()
                raise
            finally:
                # Always release the slot
                release_fetch_slot()

        return wrapper

    return decorator


__all__ = [
    "aimd_wired",
    "get_aimd_window",
    "get_aimd_active",
    "is_aimd_available",
    "acquire_fetch_slot",
    "record_fetch_success",
    "record_fetch_failure",
    "release_fetch_slot",
    "apply_backpressure",
    "blitz_boost",
    "get_aimd_telemetry",
    "with_aimd_semaphore",
]
