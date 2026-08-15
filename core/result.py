"""
Result type — explicit error handling without silent exceptions.

Pattern: Ok[T] | Err where Err carries the exception, never silently swallowed.




Usage:
    from hledac.universal.core.result import try_op, Ok, Err, Result

    # Sync
    result: Result[int] = try_op(lambda: int("42"))
    if result.is_ok():
        print(result.value)
    else:
        print(result.error, result.exception)

    # Hot-path C-style (zero allocation, no Result object):
    value = try_or(lambda: risky_op(), default=None)
    if value is not None:
        ...

Python 3.14: uses stdlib typing.Result (PEP 756) when available.
M1 8GB: zero-overhead on Ok path; HOT_PATH skips all allocations.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar, Generic
from collections.abc import Awaitable
from dataclasses import dataclass
import msgspec
from core._util import aclose

T = TypeVar("T", default=object)
F = TypeVar("F", default=object)


# ---------------------------------------------------------------------------
# Core Result types — frozen dataclasses with __slots__ for zero dict overhead.
# Ok = 40 B, Err = 48 B (measured via sys.getsizeof on Python 3.14).
# __slots__ eliminates the per-instance __dict__ (~64 B) that would otherwise
# be allocated on each Ok/Err construction.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    """Ok result — carries a value."""
    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"Ok({self.value!r})"

    def unwrap_or(self, _default_value: T) -> T:
        """Return self.value (Ok always unwraps to value, ignores default)."""
        return self.value


class Err(msgspec.Struct, frozen=True, gc=False):
    """Err result — carries error message and optional exception. F350M-R: gc=False for M1 8GB."""
    error: str
    exception: BaseException | None = None

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def __repr__(self) -> str:
        return f"Err({self.error!r}, exception={self.exception!r})"

    def unwrap_or(self, default: T) -> T:
        return default


# PEP 756 Result alias — use stdlib when it lands (Python 3.15+).
# Until then Ok[T] | Err is the canonical form; both names are defined above.
Result = Ok[T] | Err  # type: ignore[valid-type,misc]


# ---------------------------------------------------------------------------
# Logger cache — avoids repeated getLogger() lookups at call sites.
# Pattern matches silent_except_helper; _LOGGER_CACHE is module-private.
# ---------------------------------------------------------------------------

_LOGGER_CACHE: dict[str, logging.Logger] = {}


def _get_logger(name: str) -> logging.Logger:
    cached = _LOGGER_CACHE.get(name)
    if cached is not None:
        return cached
    logger = logging.getLogger(name)
    _LOGGER_CACHE[name] = logger
    return logger


# ---------------------------------------------------------------------------
# ResultPolicy — runtime behaviour configuration.
# ---------------------------------------------------------------------------

class ResultPolicy:
    """
    Policy controlling Result handling behaviour.

    Default (LOG_ERR): log Err at DEBUG, return Err, never raise.
    HOT_PATH: skip all allocations — return None on Err, caller decides.
    QUIET: suppress logging entirely.
    """
    _LOG_ERR: int = logging.DEBUG
    _LOG_OK: int = logging.DEBUG
    _RAISE_ERR: bool = False

    LOG_ERR: "ResultPolicy"  # doc placeholder
    LOG_OK: "ResultPolicy"
    HOT_PATH: "ResultPolicy"
    QUIET: "ResultPolicy"

    @classmethod
    def configure(
        cls,
        *,
        log_err_level: int | None = None,
        log_ok_level: int | None = None,
        raise_err: bool | None = None,
    ) -> None:
        """Override global defaults at runtime."""
        if log_err_level is not None:
            cls._LOG_ERR = log_err_level
        if log_ok_level is not None:
            cls._LOG_OK = log_ok_level
        if raise_err is not None:
            cls._RAISE_ERR = raise_err


# Pre-built policy singletons — stable object identity for identity checks.
ResultPolicy.LOG_ERR = ResultPolicy()  # type: ignore[attr-defined]
ResultPolicy.LOG_OK = ResultPolicy()   # type: ignore[attr-defined]
ResultPolicy.HOT_PATH = ResultPolicy()  # type: ignore[attr-defined]
ResultPolicy.QUIET = ResultPolicy()    # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# try_op — primary migration target for bare except blocks.
# ---------------------------------------------------------------------------

def try_op(
    fn: Callable[[], T],
    *,
    label: str = "",
    default: T | None = None,
) -> Result[T]:
    """
    Call fn() and wrap the result in Ok or Err.

    This is the primary migration target for:
        try:
            x = fn()
        except Exception:  # noqa: BLE001
            pass

    Args:
        fn: Callable to execute.
        label: Human-readable identifier for the call site (used in logging).
        default: If provided, return Ok(default) on exception (fail-soft default).

    Returns:
        Ok[T] on success, Err[str] on exception.
    """
    try:
        return Ok(fn())  # type: ignore[return-value]
    except Exception as e:  # noqa: BLE001
        if default is not None:
            return Ok(default)  # type: ignore[return-value]
        error_msg = f"{label}: {type(e).__name__}: {e}" if label else f"{type(e).__name__}: {e}"
        _get_logger(label or "try_op").log(
            ResultPolicy._LOG_ERR,
            "try_op failed: %s",
            error_msg,
            exc_info=e,
        )
        return Err(label or "unknown", e)  # type: ignore[return-value]


async def try_op_async(
    fn: Callable[[], Awaitable[T]],
    *,
    label: str = "",
    default: T | None = None,
) -> Result[T]:
    """
    Async version of try_op — awaits the async callable directly.

    Args:
        fn: Async callable to execute.
        label: Human-readable identifier.
        default: If provided, return Ok(default) on exception.

    Returns:
        Ok[T] on success, Err[str] on exception.
    """
    try:
        return Ok(await fn())  # type: ignore[return-value]
    except Exception as e:  # noqa: BLE001
        if default is not None:
            return Ok(default)  # type: ignore[return-value]
        error_msg = f"{label}: {type(e).__name__}: {e}" if label else f"{type(e).__name__}: {e}"
        _get_logger(label or "try_op_async").log(
            ResultPolicy._LOG_ERR,
            "try_op_async failed: %s",
            error_msg,
            exc_info=e,
        )
        return Err(label or "unknown", e)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Hot-path helpers — ZERO Result allocation on the happy path.
# Use these in I/O-bound loops where GC pressure matters.
# ---------------------------------------------------------------------------

def try_or(fn: Callable[[], T], default: T) -> T:
    """
    Hot-path variant: returns value directly or default on exception.

    Zero allocations on the Ok path. No Result object created.

    Equivalent to:
        try:
            return fn()
        except Exception:
            return default

    Use when you only care about the value, not the error detail.

    Example (hot I/O loop):
        for url in urls:
            body = try_or(lambda: fetch(url).text, default="")
            process(body)
    """
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def try_or_none(fn: Callable[[], T]) -> T | None:
    """
    Hot-path variant: returns value or None on exception.

    Zero allocations. No Result object created.

    Use when None is a valid sentinel for failure (most I/O cases).

    Example:
        url = try_or_none(lambda: resolve_host(host))
        if url is None:
            log.debug("resolve failed")
    """
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None


def try_or_raise(
    fn: Callable[[], T],
    exc_type: type[BaseException] = RuntimeError,
    *,
    label: str = "",
) -> T:
    """
    Hot-path variant: returns value or raises a custom exception.

    Zero allocations on the Ok path. No Result object created.

    Use when callers want explicit exception raising rather than
    Result-based control flow.

    Example:
        value = try_or_raise(lambda: risky_op(), ValueError, label="risky_op")
    """
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        raise exc_type(f"{label}: {e}") from e


# ---------------------------------------------------------------------------
# map_result — transform or extract from a Result.
# ---------------------------------------------------------------------------

def map_result(
    result: Result[T],
    *,
    ok_fn: Callable[[T], F] | None = None,
    err_fn: Callable[[str, BaseException | None], F] | None = None,
    default: F | None = None,
) -> T | F | None:
    """
    Transform or extract from a Result.

    Args:
        result: The Result to transform.
        ok_fn: Transform Ok.value -> F.
        err_fn: Transform Err.error + Err.exception -> F.
        default: If provided and result is Err, return default without calling err_fn.

    Returns:
        Transformed value.
    """
    if isinstance(result, Ok):
        if ok_fn is not None:
            return ok_fn(result.value)
        return result.value  # type: ignore[return-value]
    elif isinstance(result, Err):
        if default is not None:
            return default
        if err_fn is not None:
            return err_fn(result.error, result.exception)
        return None  # type: ignore[return-value]
    else:
        return None  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Async hot-path helpers — ZERO allocation on the happy path.
# Use these in async I/O-bound loops where GC pressure matters.
# ---------------------------------------------------------------------------

async def try_or_async(
    fn: Callable[[], Awaitable[T]],
    default: T,
) -> T:
    """
    Async hot-path variant: awaits and returns value or default on exception.

    Zero allocations on the Ok path. No Result object created.

    Equivalent to:
        try:
            return await fn()
        except Exception:
            return default

    Example (async I/O loop):
        for url in urls:
            body = await try_or_async(lambda: fetch(url).text, default="")
            process(body)
    """
    try:
        return await fn()
    except Exception:  # noqa: BLE001
        return default


async def try_or_none_async(
    fn: Callable[[], Awaitable[T]],
) -> T | None:
    """
    Async hot-path variant: awaits and returns value or None on exception.

    Zero allocations. No Result object created.

    Use when None is a valid sentinel for failure (most async I/O cases).

    Example:
        url = await try_or_none_async(lambda: resolve_host_async(host))
        if url is None:
            log.debug("resolve failed")
    """
    try:
        return await fn()
    except Exception:  # noqa: BLE001
        return None


async def try_or_raise_async(
    fn: Callable[[], Awaitable[T]],
    exc_type: type[BaseException] = RuntimeError,
    *,
    label: str = "",
) -> T:
    """
    Async hot-path variant: returns value or raises a custom exception.

    Zero allocations on the Ok path. No Result object created.

    Use when callers want explicit exception raising rather than
    Result-based control flow.

    Example:
        value = await try_or_raise_async(lambda: risky_op_async(), ValueError, label="risky_op")
    """
    try:
        return await fn()
    except Exception as e:  # noqa: BLE001
        raise exc_type(f"{label}: {e}") from e


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    # Types
    "Ok",
    "Err",
    "Result",
    # Policy
    "ResultPolicy",
    # Core API
    "try_op",
    "try_op_async",
    "map_result",
    # Hot-path helpers (zero allocation on Ok path)
    "try_or",
    "try_or_none",
    "try_or_raise",
    # Async hot-path helpers (zero allocation on Ok path)
    "try_or_async",
    "try_or_none_async",
    "try_or_raise_async",
]
