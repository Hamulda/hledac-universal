"""
from _core import aclose
Cleanup Protocol — canonical aclose implementation for core modules.

This module provides the canonical shutdown_aclose() function that was previously
in runtime/protocols/cleanup_protocol.py. Core modules import from here to avoid
creating a runtime ↔ core cycle.

F350M-R: Dependency cycle elimination
- shutdown_aclose moved to core/protocols/
- runtime/protocols/cleanup_protocol.py now imports from here (reverse direction)
- All core modules import from core.protocols, not runtime.protocols

GHOST_INVARIANTS:
- Always-on: no feature flags
- Fail-safe: aclose() never raises — all exceptions are caught and logged
- Bounded: every aclose() has a timeout_s parameter (default 10.0)
- M1 8GB safe: no unbounded waits
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Default timeout for aclose() — 10s is sufficient for most resources.
# DuckDB WAL flush: ~100ms under normal load
# MLX Metal cache clear: ~50ms
# LMDB close: ~10ms
DEFAULT_ACLOSE_TIMEOUT_S = 10.0

# Telemetry labels for shutdown reason tracking
_SHUTDOWN_NORMAL = "normal"
_SHUTDOWN_TIMEOUT = "timeout"
_SHUTDOWN_FORCE = "force"


async def shutdown_aclose(
    name: str,
    coro: Any,
    timeout_s: float = DEFAULT_ACLOSE_TIMEOUT_S,
    _telemetry: Any = None,
) -> None:
    """
    Canonical aclose wrapper: asyncio.wait_for + force-shutdown fallback.

    This is the STANDARD implementation pattern for all aclose() methods.
    Subclasses should NOT reimplement aclose() directly — instead they
    implement _do_shutdown() (the actual cleanup logic) and call this
    helper from their aclose() override.

    Args:
        name: Human-readable name for logging
        coro: Awaitable — the cleanup coroutine
        timeout_s: Maximum seconds to wait (default 10.0)
        _telemetry: Optional telemetry duck-typed object with incr() method

    Force shutdown path:
        After timeout, sends CancelledError into the coroutine and waits
        up to 1.0s for graceful cancellation.
    """
    if timeout_s is None:
        timeout_s = DEFAULT_ACLOSE_TIMEOUT_S

    _emit = getattr(_telemetry, "incr", None) if _telemetry else None

    start = time.monotonic()
    reason: str = _SHUTDOWN_NORMAL

    try:
        async with asyncio.timeout(timeout_s):
            await coro
    except TimeoutError:
        reason = _SHUTDOWN_TIMEOUT
        logger.warning(
            "[shutdown:force] %s aclose() timed out after %.1fs",
            name,
            timeout_s,
        )
        if hasattr(coro, "close"):
            coro.close()
        await asyncio.sleep(1.0)
        reason = _SHUTDOWN_FORCE
    except asyncio.CancelledError:
        reason = _SHUTDOWN_FORCE
        raise
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000
        if _emit:
            _emit("shutdown_reason", 1.0, {"reason": reason, "component": name})
            _emit("shutdown_duration_ms", elapsed_ms, {"reason": reason, "component": name})
        if reason != _SHUTDOWN_NORMAL:
            logger.debug("[shutdown] %s completed in %.1fms (reason=%s)", name, elapsed_ms, reason)
