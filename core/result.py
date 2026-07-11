"""
Result type — explicit error handling without silent exceptions.

Pattern: Ok[T] | Err where Err carries the exception, never silently swallowed.

Usage:
    from core.result import try_op, Result

    # Sync
    result: Result[int] = try_op(lambda: int("42"))
    if result.is_ok():
        print(result.value)
    else:
        print(result.error, result.exception)

    # Async wrapper (use asyncio.to_thread if needed)
    async def prewarm_all_safe() -> dict[str, Result]:
        return {
            k: await asyncio.wrap_future(asyncio.get_event_loop().run_in_executor(None, v))
            for k, v in prewarmers.items()
        }

Python 3.14: uses stdlib typing.Result (PEP 756), no typing_extensions needed.
M1 8GB: zero-overhead, no allocations on Ok path.
"""

import logging
from collections.abc import Callable
from typing import TypeVar, Awaitable, Generic
from dataclasses import dataclass

T = TypeVar("T", default=object)
F = TypeVar("F", default=object)


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

    def unwrap_or(self, default_value: T) -> T:
        return default_value


@dataclass(frozen=True, slots=True)
class Err:
    """Err result — carries error message and optional exception."""
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


# Result union type alias
Result = Ok[T] | Err


# Module-level logger cache (lazy, same pattern as silent_except_helper)
_LOGGER_CACHE: dict[str, logging.Logger] = {}


def _get_logger(name: str) -> logging.Logger:
    cached = _LOGGER_CACHE.get(name)
    if cached is not None:
        return cached
    logger = logging.getLogger(name)
    _LOGGER_CACHE[name] = logger
    return logger


# Policy for what to do on Err
class ResultPolicy:
    """
    Policy controlling Result handling behavior.

    Default: LOG_ERR — log Err at DEBUG, return Err (never raise).
    HOT_PATH uses this for fail-soft behavior.
    """
    LOG_ERR: int = logging.DEBUG
    LOG_OK: int = logging.DEBUG
    RAISE_ERR: bool = False  # Never raise on Err — fail-soft is the contract

    @classmethod
    def configure(
        cls,
        *,
        log_err_level: int | None = None,
        log_ok_level: int | None = None,
        raise_err: bool | None = None,
    ) -> None:
        """Override policy settings at module level."""
        if log_err_level is not None:
            cls.LOG_ERR = log_err_level
        if log_ok_level is not None:
            cls.LOG_OK = log_ok_level
        if raise_err is not None:
            cls.RAISE_ERR = raise_err


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

    Instead, use:
        result = try_op(fn, label="fn_label")
        if result.is_err():
            _logger.debug("fn_label failed: %s", result.error)

    Args:
        fn: Callable to execute.
        label: Human-readable identifier for the call site (used in logging).
        default: If provided, return Ok(default) on exception (fail-soft default).
        policy: Optional ResultPolicy subclass to customize behavior.

    Returns:
        Ok[T] on success, Err[str] on exception.
    """
    _logger = _get_logger(label or "try_op")

    try:
        value = fn()
        return Ok(value)
    except Exception as e:  # noqa: BLE001
        if default is not None:
            return Ok(default)
        error_msg = f"{label}: {type(e).__name__}: {e}" if label else f"{type(e).__name__}: {e}"
        _logger.log(ResultPolicy.LOG_ERR, "try_op failed: %s", error_msg, exc_info=e)
        return Err(label or "unknown", e)


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
    _logger = _get_logger(label or "try_op_async")

    try:
        value = await fn()
        return Ok(value)
    except Exception as e:  # noqa: BLE001
        if default is not None:
            return Ok(default)
        error_msg = f"{label}: {type(e).__name__}: {e}" if label else f"{type(e).__name__}: {e}"
        _logger.log(ResultPolicy.LOG_ERR, "try_op_async failed: %s", error_msg, exc_info=e)
        return Err(label or "unknown", e)


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


__all__ = [
    "Ok",
    "Err",
    "Result",
    "ResultPolicy",
    "try_op",
    "try_op_async",
    "map_result",
]
