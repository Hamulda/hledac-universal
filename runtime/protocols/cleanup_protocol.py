"""
runtime/protocols/cleanup_protocol.py — F285: Async Resource Cleanup Protocol

DEPRECATED — Import from core.protocols instead.

F350M-R: This module now re-exports from core.protocols to maintain
backward compatibility while breaking the core ↔ runtime cycle.

New code should import from:
    from core.protocols import shutdown_aclose, DEFAULT_ACLOSE_TIMEOUT_S

This module will be removed in a future release.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

# Re-export from core.protocols — this breaks the cycle
# runtime → core.protocols (no cycle, core doesn't import runtime.protocols)
from core.protocols.cleanup_protocol import (
    shutdown_aclose as _core_shutdown_aclose,
    DEFAULT_ACLOSE_TIMEOUT_S,
)

if TYPE_CHECKING:
    from typing import ClassVar

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = [
    "AsyncCleanable",
    "shutdown_aclose",
    "manage_cleanup",
    "DEFAULT_ACLOSE_TIMEOUT_S",
]


# Alias for local use
shutdown_aclose = _core_shutdown_aclose


# ============================================================================
# AsyncCleanable Protocol (unchanged from original)
# ============================================================================


@runtime_checkable
class AsyncCleanable(Protocol):
    """
    Protocol for async resources requiring graceful shutdown.

    GHOST_INVARIANTS:
        - aclose() must be:
          - Idempotent: safe to call multiple times
          - Fail-safe: never raises, catches all exceptions
          - Bounded: must complete within timeout_s or force-cancel
    """

    async def aclose(self, timeout_s: float = DEFAULT_ACLOSE_TIMEOUT_S) -> None:
        """
        Graceful shutdown with bounded timeout.

        Args:
            timeout_s: Maximum seconds to wait for cleanup.
                       Default: 10.0s.

        Post-condition:
            Resource is fully cleaned up or cancellation was attempted.
        """
        ...


# ============================================================================
# Composite cleanup context manager (unchanged from original)
# ============================================================================


class _ManagedResource:
    """Wrapper that holds a resource and its cleanup timeout."""

    __slots__ = ("resource", "timeout_s")

    def __init__(self, resource: Any, timeout_s: float) -> None:
        self.resource = resource
        self.timeout_s = timeout_s


async def _aclose_one(resource: Any, timeout_s: float) -> list[Exception]:
    """Call aclose() on a single resource, collecting all exceptions."""
    errors: list[Exception] = []
    try:
        await resource.aclose(timeout_s=timeout_s)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        errors.append(exc)
    return errors


async def manage_cleanup(
    *resources: Any,
    default_timeout_s: float = DEFAULT_ACLOSE_TIMEOUT_S,
) -> Any:
    """
    Async context manager for automatic LIFO cleanup of multiple resources.

    Cleans up in reverse order (LIFO): the last resource registered is
    the first to be cleaned up.

    Args:
        *resources: AsyncCleanable resources to manage.
        default_timeout_s: Timeout for each aclose() call (default 10.0s).

    Yields:
        The tuple of input resources (for convenience).

    Example:
        async with manage_cleanup(store, scheduler, coalescer) as (store, _, _):
            await store.async_ingest_findings_batch(findings)
        # scheduler cleaned first, then store (LIFO)
    """
    if not resources:
        yield
        return

    managed = [_ManagedResource(r, default_timeout_s) for r in resources]
    reversed_managed = managed[::-1]  # LIFO order

    errors: list[Exception] = []

    try:
        yield managed
    except asyncio.CancelledError:
        for mr in reversed_managed:
            try:
                await mr.resource.aclose(timeout_s=mr.timeout_s)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                errors.append(exc)
        raise
    except Exception as exc:
        errors.append(exc)
    finally:
        for mr in reversed_managed:
            try:
                await mr.resource.aclose(timeout_s=mr.timeout_s)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                errors.append(exc)

        if errors:
            logger.warning(
                "[cleanup] errors during managed cleanup: %s",
                [str(e) for e in errors],
            )
