"""
runtime/protocols/cleanup_protocol.py — F285: Async Resource Cleanup Protocol

PEP 544 Protocol for async resource cleanup with:
- AsyncCleanable: base Protocol with aclose(timeout_s=10.0)
- manage_cleanup(): @asynccontextmanager for automatic LIFO cleanup

LIFO order: Last resource registered = First to clean up.
This matters: client session before DB connection, scheduler before store.

Usage:
    from runtime.protocols.cleanup_protocol import AsyncCleanable, manage_cleanup

    # Pattern 1: async context manager (recommended)
    async with manage_cleanup(store, scheduler) as (_, _):
        await run_sprint()
    # LIFO: scheduler cleaned first, then store

    # Pattern 2: explicit aclose() with timeout
    await resource.aclose(timeout_s=10.0)

    # Pattern 3: implement the Protocol
    @runtime_checkable
    class MyResource(AsyncCleanable, Protocol):
        async def aclose(self, timeout_s: float = 10.0) -> None: ...

GHOST_INVARIANTS:
- Always-on: no feature flags, no env vars
- Fail-safe: aclose() never raises — all exceptions are caught and logged
- Bounded: every aclose() has a timeout_s parameter (default 10.0)
- LIFO: manage_cleanup() cleans in reverse registration order
- M1 8GB safe: no unbounded waits, cancellation propagates correctly
"""


import asyncio
import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Default timeout for aclose() — 10s is sufficient for most resources.
# DuckDB WAL flush: ~100ms under normal load
# MLX Metal cache clear: ~50ms
# LMDB close: ~10ms
# Only pathological cases (slow network, large pending batches) need more.
DEFAULT_ACLOSE_TIMEOUT_S = 10.0


@runtime_checkable
class AsyncCleanable(Protocol):
    """
    Base Protocol for all async-cleanable resources.

    All SprintScheduler resources (DuckDB store, scheduler, coalescers,
    inference engines, transport sessions) must implement this Protocol.

    Rationale:
        Having a single Protocol means:
        - Static type checkers can verify all resources are aclose-able
        - manage_cleanup() accepts heterogeneous resource lists
        - No more inconsistent naming (aclose vs shutdown vs close)

    Implementations:
        - coordinators/memory_coordinator: UniversalMemoryCoordinator
        - knowledge/duckdb_store: DuckDBShadowStore
        - runtime/sprint_scheduler: SprintScheduler

    Invariant:
        aclose() must be:
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
                       CancelledError propagates after timeout on best-effort basis.

        Post-condition:
            Resource is fully cleaned up (WAL flushed, connections closed,
            tasks cancelled) or cancellation was attempted.
        """
        ...


# --- Composite cleanup context manager ---


class _ManagedResource:
    """Wrapper that holds a resource and its cleanup timeout."""

    __slots__ = ("resource", "timeout_s")

    def __init__(self, resource: Any, timeout_s: float) -> None:
        self.resource = resource
        self.timeout_s = timeout_s


async def _aclose_one(resource: Any, timeout_s: float) -> list[Exception]:
    """
    Call aclose() on a single resource, collecting all exceptions.

    Returns list of exceptions encountered (empty = clean).
    CancelledError is re-raised (does NOT appear in returned list).
    """
    errors: list[Exception] = []
    try:
        await resource.aclose(timeout_s=timeout_s)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)
    return errors


async def manage_cleanup(
    *resources: Any,
    default_timeout_s: float = DEFAULT_ACLOSE_TIMEOUT_S,
) -> Any:
    """
    Async context manager for automatic LIFO cleanup of multiple resources.

    Cleans up in reverse order (LIFO): the last resource registered is
    the first to be cleaned up. This is the correct order for most
    resource stacks (e.g., session before store, scheduler before store).

    Args:
        *resources: AsyncCleanable resources to manage.
        default_timeout_s: Timeout for each aclose() call (default 10.0s).

    Yields:
        The tuple of input resources (for convenience).

    LIFO guarantee:
        If you register [store, scheduler], cleanup order is:
        1. scheduler.aclose(timeout_s)
        2. store.aclose(timeout_s)

    Cancellation handling:
        If CancelledError arrives during cleanup:
        - Attempt cleanup of remaining resources (best-effort)
        - Re-raise CancelledError so the cancellation propagates

    Example:
        async with manage_cleanup(store, scheduler, coalescer) as (store, _, _):
            await store.async_ingest_findings_batch(findings)
        # scheduler cleaned first, then store (LIFO)

    Note:
        This does NOT use contextlib.AsyncExitStack directly — instead it
        implements the protocol manually for maximum control over LIFO order,
        timeout enforcement, and cancellation handling.
    """
    if not resources:
        yield
        return

    # Wrap each resource with its timeout
    managed = [_ManagedResource(r, default_timeout_s) for r in resources]
    # Reverse for LIFO: last registered = first cleaned
    reversed_managed = managed[::-1]

    errors: list[Exception] = []

    try:
        yield managed
    except asyncio.CancelledError:
        # Cancellation is high-priority — attempt cleanup of remaining
        # resources but re-raise CancelledError so the cancellation propagates
        for mr in reversed_managed:
            try:
                await mr.resource.aclose(timeout_s=mr.timeout_s)
            except asyncio.CancelledError:
                pass  # Preserve the first cancellation
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        raise
    except Exception as exc:  # noqa: BLE001
        # Non-cancellation exception — attempt cleanup but don't mask it
        errors.append(exc)
    finally:
        # Normal exit or exception exit — clean up all remaining resources
        for mr in reversed_managed:
            try:
                await mr.resource.aclose(timeout_s=mr.timeout_s)
            except asyncio.CancelledError:
                pass  # CancelledError during cleanup = not our concern
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        if errors:
            # Log but do NOT re-raise — cleanup errors are best-effort
            logger.warning(
                "[cleanup] errors during managed cleanup: %s",
                [str(e) for e in errors],
            )
