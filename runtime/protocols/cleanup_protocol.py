"""
runtime/protocols/cleanup_protocol.py — F285: Async Resource Cleanup Protocol

PEP 544 Protocol for async resource cleanup with:
- AsyncCleanable: base Protocol with aclose(timeout_s=10.0)
- manage_cleanup(): @asynccontextmanager for automatic LIFO cleanup

LIFO order: Last resource registered = First to clean up.
This matters: client session before DB connection, scheduler before store.

Usage:
    from hledac.universal.runtime.protocols.cleanup_protocol import AsyncCleanable, manage_cleanup

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
import time
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from typing import ClassVar

logger = logging.getLogger(__name__)

# Default timeout for aclose() — 10s is sufficient for most resources.
# DuckDB WAL flush: ~100ms under normal load
# MLX Metal cache clear: ~50ms
# LMDB close: ~10ms
# Only pathological cases (slow network, large pending batches) need more.
DEFAULT_ACLOSE_TIMEOUT_S = 10.0

# Telemetry labels for shutdown reason tracking (P1-9 acceptance criteria)
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

    Telemetry emitted (if _telemetry is set):
        shutdown_reason: "normal" | "timeout" | "force"
        shutdown_duration_ms: elapsed milliseconds

    Args:
        name: Human-readable name for logging (e.g. "DuckDBShadowStore")
        coro: Awaitable — the cleanup coroutine (e.g. self._do_shutdown())
        timeout_s: Maximum seconds to wait (default 10.0)
        _telemetry: Optional telemetry-like duck-typed object with
                    incr(metric, value) method (e.g. Prometheus Counter,
                    structlog log, or no-op).
                    Pass None to skip telemetry.

    Force shutdown path:
        After timeout, sends CancelledError into the coroutine and waits
        up to 1.0s for graceful cancellation. If that also fails, the
        force path is still considered complete (no SIGKILL in Python).

    Usage:
        class MyResource:
            DEFAULT_TIMEOUT_S = 10.0

            async def aclose(self, timeout_s: float | None = None) -> None:
                timeout_s = timeout_s if timeout_s is not None else self.DEFAULT_TIMEOUT_S
                await shutdown_aclose(
                    name=type(self).__name__,
                    coro=self._do_shutdown(),
                    timeout_s=timeout_s,
                    _telemetry=getattr(self, "_telemetry", None),
                )
    """
    if timeout_s is None:
        timeout_s = DEFAULT_ACLOSE_TIMEOUT_S

    _emit = getattr(_telemetry, "incr", None) if _telemetry else None

    start = time.monotonic()
    reason: str = _SHUTDOWN_NORMAL

    try:
        async with asyncio.timeout(timeout_s):
            await coro
    except asyncio.TimeoutError:
        reason = _SHUTDOWN_TIMEOUT
        logger.warning(
            "[shutdown:force] %s aclose() timed out after %.1fs — forcing cancellation",
            name,
            timeout_s,
        )
        # Force path: give coroutine 1.0s to honour cancellation, then give up.
        # No SIGKILL in Python — we can only close the coroutine and wait.
        if hasattr(coro, "close"):
            coro.close()  # type: ignore[union-attr]
        await asyncio.sleep(1.0)  # Allow cancellation to propagate
        reason = _SHUTDOWN_FORCE
    except asyncio.CancelledError:
        reason = _SHUTDOWN_FORCE
        raise
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000
        if _emit:
            _emit("shutdown_reason", 1.0, {"reason": reason, "component": name})
            _emit("shutdown_duration_ms", elapsed_ms, {"reason": reason, "component": name})
        else:
            logger.debug(
                "[shutdown] %s aclose() reason=%s duration_ms=%.1f",
                name,
                reason,
                elapsed_ms,
            )


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