# hledac/universal/utils/async/_core.py
# Core async utilities - DNS resolution, timing, lifecycle helpers
#
# Provides:
# - async_getaddrinfo: Async DNS resolver with rust.dns backend
# - safe_wait_for: asyncio.timeout wrapper with correct TaskGroup composition
# - first_completed: PEP 654 asyncio.wait(FIRST_COMPLETED) replacement
# - monotonic_ms: Current monotonic time in milliseconds
# - stop_task: Shared stop() lifecycle helper
# - parallel_close, parallel_close_async: Parallel resource teardown
# - retry_backoff_async: Exponential backoff with jitter
"""
Core async utilities - DNS resolution, timing, lifecycle helpers

Provides:
- async_getaddrinfo: Async DNS resolver with rust.dns backend
- safe_wait_for: asyncio.timeout wrapper with correct TaskGroup composition
- first_completed: PEP 654 asyncio.wait(FIRST_COMPLETED) replacement
- monotonic_ms: Current monotonic time in milliseconds
- stop_task: Shared stop() lifecycle helper
- parallel_close, parallel_close_async: Parallel resource teardown
- retry_backoff_async: Exponential backoff with jitter
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import socket
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

from hledac.universal.utils.asyncx._parallel import parallel

if TYPE_CHECKING:
    pass

T = TypeVar("T", default=Any)


logger = __import__("logging").getLogger(__name__)


# ---------------------------------------------------------------------------
# DNS resolution via rust.dns (hickory-dns, DoH/DoT/DoQ)
# ---------------------------------------------------------------------------

_HAS_RUST_DNS: bool = False
_HAS_RUST_DNS_ASYNC: bool = False
try:
    import rust
    _HAS_RUST_DNS = hasattr(rust, "dns") and hasattr(rust.dns, "resolve_async")
    # MODERN-09: Check for async version
    _HAS_RUST_DNS_ASYNC = hasattr(rust.dns, "resolve_async_await")
except Exception:
    _HAS_RUST_DNS = False
    _HAS_RUST_DNS_ASYNC = False


async def async_getaddrinfo(
    host: str,
    port: int,
    *,
    family: int = 0,
    type_: int = 0,
    proto: int = 0,
    timeout: float | None = None,
) -> list[tuple[Any, ...]]:
    """Async DNS resolver with rust.dns (hickory-dns) backend for M1 8GB.

    ISSUE-6 FIX: Replaces aiodns (c-ares) with rust.dns which has automatic
    resource management via Rust Drop trait — no FD leak possible, no close()
    needed. Supports DoH/DoT/DoQ for 2-5× faster parallel resolution vs stdlib
    loop.getaddrinfo().

    RESOLUTION ORDER:
        PRIMARY: rust.dns.resolve_async_await() (DoT to Cloudflare)
        SECONDARY: rust.dns.resolve_async() with run_in_executor
        TERTIARY: loop.getaddrinfo() (stdlib fallback)

    NOTE: Requires rust.dns with dns feature enabled in Cargo.toml.
    Build: `maturin develop --features dns` or use --features full.

    Args:
        host: hostname to resolve
        port: port number
        family: address family (0 = auto, AF_INET or AF_INET6 via rust.dns)
        type_: socket type (0 = auto)
        proto: protocol (0 = auto)
        timeout: max seconds to wait (None = use loop default)

    Returns:
        List of (family, type, proto, canonname, sockaddr) tuples.
    """
    if _HAS_RUST_DNS and family in (0, socket.AF_INET, socket.AF_INET6) and type_ in (0, socket.SOCK_STREAM):
        try:
            qtype = "AAAA" if family == socket.AF_INET6 else "A"
            # MODERN-09: Use async API if available (preferred)
            if _HAS_RUST_DNS_ASYNC:
                ips = await rust.dns.resolve_async_await(host, qtype)
            else:
                # Fallback: use sync API with run_in_executor
                loop = asyncio.get_running_loop()
                ips = await loop.run_in_executor(None, lambda: rust.dns.resolve_async(host, qtype))
            if ips:
                af = socket.AF_INET if family != socket.AF_INET6 else socket.AF_INET6
                return [
                    (af, socket.SOCK_STREAM, 0, host, (addr, port))
                    for addr in ips
                ]
            return []
        except Exception:  # noqa: BLE001
            pass

    # Fallback: stdlib loop.getaddrinfo()
    loop = asyncio.get_running_loop()
    if timeout is not None and timeout > 0:
        async with asyncio.timeout(timeout):
            return await loop.getaddrinfo(host, port, family=family, type=type_, proto=proto)
    else:
        return await loop.getaddrinfo(host, port, family=family, type=type_, proto=proto)


# ---------------------------------------------------------------------------
# safe_wait_for — asyncio.timeout replacement for Python 3.14+ compatibility
# ---------------------------------------------------------------------------

async def safe_wait_for[T](
    coro: Awaitable[T],
    timeout: float | None,
    *,
    label: str = "",
    logger_instance: Any = None,
) -> T:
    """Drop-in replacement for asyncio.wait_for with correct TaskGroup composition.

    ``asyncio.wait_for`` does NOT compose correctly with ``asyncio.TaskGroup``
    cancellation: when a TaskGroup cancels its scope, the CancelledError
    propagates through the awaited coroutine, but ``wait_for`` intercepts it
    and raises ``TimeoutError`` if a timeout was specified.

    ``asyncio.timeout`` (Python 3.11+, PEP 654) solves this: it raises
    ``asyncio.TimeoutError`` which is NOT a subclass of ``CancelledError``,
    so TaskGroup cancellation propagates correctly.

    Invariants:
        - [W1] asyncio.TimeoutError on timeout (same as wait_for)
        - [W2] asyncio.CancelledError on TaskGroup cancellation (correct vs wait_for)
        - [W3] All other exceptions propagate unchanged
        - [W4] timeout=None means no deadline (same as wait_for)

    Args:
        coro: Coroutine or awaitable to run with deadline.
        timeout: Maximum seconds to wait. None = no deadline.
        label: Context label for log messages.
        logger_instance: Optional logger override.

    Returns:
        Result of the coroutine on success.

    Raises:
        asyncio.TimeoutError: if timeout expired.
        asyncio.CancelledError: if TaskGroup cancelled the scope.
    """
    _log = logger_instance or logger
    if timeout is None or timeout <= 0:
        return await coro

    try:
        async with asyncio.timeout(timeout):
            return await coro
    except asyncio.TimeoutError:
        _log.debug(f"[GHOST] safe_wait_for{'_' + label if label else ''} timeout after {timeout}s")
        raise


# ---------------------------------------------------------------------------
# first_completed — PEP 654 asyncio.wait(FIRST_COMPLETED) replacement
# ---------------------------------------------------------------------------

async def first_completed[T](
    *tasks: asyncio.Task[T],
    timeout: float | None = None,
) -> tuple[T, asyncio.Task[T]]:
    """PEP 654 asyncio.wait(FIRST_COMPLETED) replacement using shared Future.

    Python 3.14 deprecates asyncio.wait() in favor of structured concurrency.
    This function provides the FIRST_COMPLETED semantics using a completion
    Future that all tasks write to when done. The TaskGroup handles
    cancellation of children on scope exit.

    Args:
        *tasks: Task instances to race (must be pre-created)
        timeout: Optional timeout in seconds

    Returns:
        tuple of (result, completed_task)

    Raises:
        asyncio.TimeoutError: if timeout expires before any task completes
    """
    if not tasks:
        raise ValueError("first_completed requires at least one task")

    # ISSUE-10 FIX: get_running_loop() instead of deprecated get_event_loop() (Python 3.12+)
    # ISSUE-11: name= param for better async diagnostics (Python 3.14+)
    winner_future: asyncio.Future[asyncio.Task[T]] = asyncio.get_running_loop().create_future(name="asyncx:first_completed")

    def on_done(task: asyncio.Task[T]) -> None:
        if not winner_future.done():
            winner_future.get_loop().call_soon_threadsafe(winner_future.set_result, task)

    for task in tasks:
        task.add_done_callback(on_done)

    try:
        if timeout is not None and timeout > 0:
            async with asyncio.timeout(timeout):
                winner = await winner_future
        else:
            winner = await winner_future
    except asyncio.TimeoutError:
        raise
    except BaseException:
        raise

    return winner.result(), winner


# ---------------------------------------------------------------------------
# Timing utilities
# ---------------------------------------------------------------------------

def monotonic_ms() -> float:
    """Return current monotonic time in milliseconds (float)."""
    return time.monotonic() * 1000.0


# ---------------------------------------------------------------------------
# Lifecycle helpers — stop pattern (F360)
# ---------------------------------------------------------------------------

async def stop_task(coro: asyncio.Task[Any] | None) -> None:
    """Stop a background task gracefully — cancel and await CancelledError.

    Standardises the ``_running + _task`` cancellation pattern used across
    SprintScheduler, SystemResourcesSampler, ResourceGovernor and similar
    run-loops.

    Pattern::

        self._running = False
        await stop_task(self._task)
        self._task = None

    Args:
        coro: The asyncio.Task to cancel. None or already-finished tasks
              are handled silently (no-op).
    """
    if coro is None:
        return
    coro.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await coro


# ---------------------------------------------------------------------------
# ISSUE-04: parallel_close — parallel resource teardown helper
# ---------------------------------------------------------------------------

async def _safe_aclose(
    resource: Any,
    *,
    ctx: str,
    logger_instance: Any,
) -> Exception | None:
    """Close a single resource (aclose/close), returning None on success or the exception."""
    try:
        close_fn = getattr(resource, "aclose", None) or getattr(resource, "close", None)
        if close_fn is None:
            logger_instance.debug("[%s] resource %s has no close/aclose method", ctx, type(resource).__name__)
            return None
        result = close_fn()
        if asyncio.iscoroutine(result):
            await result
        return None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger_instance.debug("[%s] failed to close %s: %s", ctx, type(resource).__name__, e)
        return e


async def parallel_close(
    resources: list[Any],
    *,
    concurrency: int = 4,
    ctx: str = "parallel_close",
    logger_instance: Any = None,
) -> list[Exception | None]:
    """Close multiple resources in parallel with bounded concurrency.

    Fail-safe: exceptions are collected and returned, never propagated.
    CancelledError is re-raised (teardown must not swallow cancellation).

    Use for independent resources that can be closed concurrently (HTTP clients,
    transport layers, session pools).

    Args:
        resources: List of objects with .aclose() or .close() method.
        concurrency: Max simultaneous close operations (default 4, M1 8GB friendly).
        ctx: Context label for log messages.
        logger_instance: Optional logger override.

    Returns:
        List of Exception | None per resource (None = success, Exception = failure).
    """
    _log = logger_instance or logger
    if not resources:
        return []

    coros: list[Awaitable[Exception | None]] = [
        _safe_aclose(r, ctx=ctx, logger_instance=_log)
        for r in resources
    ]

    result = await parallel(
        coros,
        policy="collect",
        concurrency=concurrency,
        taskgroup=True,
        ctx=ctx,
        logger_instance=_log,
    )

    return result.ok


async def _safe_close_async(
    close_fn: Callable[[], Awaitable[Any]],
    name: str,
    *,
    ctx: str,
    logger_instance: Any,
) -> tuple[str, Exception | None]:
    """Close a resource via async callable, returning (name, exception)."""
    try:
        await close_fn()
        return (name, None)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger_instance.debug("[%s] failed to close %s: %s", ctx, name, e)
        return (name, e)


async def parallel_close_async(
    close_funcs: list[tuple[str, Callable[[], Awaitable[Any]]]],
    *,
    concurrency: int = 4,
    ctx: str = "parallel_close_async",
    logger_instance: Any = None,
) -> dict[str, Exception | None]:
    """Close multiple async resources in parallel via async callables.

    Unlike ``parallel_close`` which works on objects with .aclose()/.close() methods,
    this variant accepts named async callables — useful for module-level close functions
    that don't belong to an object.

    Fail-safe: exceptions are collected into the result dict, never propagated.
    CancelledError is re-raised (teardown must not swallow cancellation).

    Args:
        close_funcs: List of (name, async_close_fn) tuples.
        concurrency: Max simultaneous close operations (default 4).
        ctx: Context label for log messages.
        logger_instance: Optional logger override.

    Returns:
        Dict mapping name → None (success) or Exception (failure).
    """
    _log = logger_instance or logger
    if not close_funcs:
        return {}

    coros: list[Awaitable[tuple[str, Exception | None]]] = [
        _safe_close_async(fn, name, ctx=ctx, logger_instance=_log)
        for name, fn in close_funcs
    ]

    result = await parallel(
        coros,
        policy="collect",
        concurrency=concurrency,
        taskgroup=True,
        ctx=ctx,
        logger_instance=_log,
    )

    out: dict[str, Exception | None] = {}
    for item in result.ok:
        if isinstance(item, tuple) and len(item) == 2:
            out[item[0]] = item[1]
        else:
            out[str(item)] = None
    return out


# ---------------------------------------------------------------------------
# E1: retry_backoff_async — exponential backoff with jitter
# ---------------------------------------------------------------------------

async def retry_backoff_async(
    coro_fn: Callable[[], Awaitable[T]],
    max_retries: int = 3,
    base_delay: float = 0.5,
    *,
    max_delay: float = 30.0,
    jitter: bool = True,
    cancel_is_retriable: bool = False,
) -> T:
    """Retry with exponential backoff and optional jitter.

    Replaces manual retry patterns. Properly propagates CancelledError.

    Args:
        coro_fn: Coroutine to execute (callable, not pre-awaited).
        max_retries: Maximum retry attempts (default 3).
        base_delay: Initial delay in seconds (default 0.5).
        max_delay: Cap on delay growth (default 30s).
        jitter: Add ±25% decorrelated jitter (default True).
        cancel_is_retriable: If True, CancelledError triggers retry instead
            of propagation. Default False.

    Returns:
        The return value of coro_fn on success.

    Raises:
        CancelledError: Propagates immediately (cancel_is_retriable=False).
        Exception: Re-raised after all retries exhausted.
    """
    attempt = 0

    while True:
        try:
            return await coro_fn()
        except asyncio.CancelledError:
            if not cancel_is_retriable:
                raise
        except Exception as _exc:  # noqa: BLE001
            if attempt >= max_retries:
                raise

        attempt += 1
        delay = min(base_delay * (2 ** (attempt - 1)), max_delay)

        if jitter:
            delay *= (0.75 + random.random() * 0.5)

        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise


__all__ = [
    "async_getaddrinfo",
    "safe_wait_for",
    "first_completed",
    "monotonic_ms",
    "stop_task",
    "parallel_close",
    "parallel_close_async",
    "retry_backoff_async",
]
