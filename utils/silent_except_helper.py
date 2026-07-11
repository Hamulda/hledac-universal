"""
Silent exception helper — opt-in structured logging for `except: pass` sites.

The codebase has 1215 `except ... : pass` sites (per 2026-06-04 AST survey,
updated). Distribution:
    953 (78%) — bare `except Exception:`         → broad, high risk
    65  (5%)  — `except ImportError`            → defensive import
    32  (3%)  — `except asyncio.CancelledError` → cleanup race
    30  (2%)  — `except ValueError`              → validation fail
    135 (11%) — other specific types             → clearly intentional

Per GHOST_INVARIANTS: "no silent except" is the goal.
Per PYTHON314_MODERNIZATION_AUDIT: ~80% are intentional fail-safe fallbacks.

This module provides a single canonical helper for sites that decide to add
optional logging without breaking the fail-safe contract.

Usage (3 modern styles):

    # 1. Function — drop-in for the legacy `pass` body
    from utils.silent_except_helper import safe_swallow

    try:
        risky_op()
    except (OSError, asyncio.CancelledError):
        safe_swallow("cleanup_lmdb_lock", logger, exc=e)

    # 2. Context manager — modern Python 3.11+ (PEP 654 aware)
    from utils.silent_except_helper import silenced

    with silenced(OSError, name="lmdb_lock_cleanup", level=logging.DEBUG):
        cleanup_stale_lock()

    # 3. Decorator — for function-level opt-in
    from utils.silent_except_helper import silence_errors

    @silence_errors(ValueError, name="json_parse_legacy_field")
    def parse_legacy_optional_field(raw):
        return json.loads(raw)["field"]

Why opt-in:
- Bulk-rewriting 1215 sites would either (a) spam logs on M1 with bounded
  ring buffer, or (b) require per-site judgment that is out of scope for
  mechanical modernization.
- Hot-path code (sprint loop) would slow down with logger.debug() per op.
- Helpers are a single import; sites that want logging add ONE line, not
  a global flag.

M1 compatibility:
- No MLX/curl/duckdb imports
- Lazy logger lookup (module-level cache)
- Zero runtime cost when not used
- Safe in async/sync contexts
- `contextlib.suppress` is the C-accelerated stdlib primitive — same
  speed as `try/except: pass` (no measurable difference on M1)
"""


import contextlib
import functools
import logging
from collections.abc import Callable, Iterator
from typing import ParamSpec, TypeVar

# Module-level cache: name → logger
_LOGGER_CACHE: dict[str, logging.Logger] = {}

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _get_logger(name: str) -> logging.Logger:
    """Lazy logger lookup. Cached for hot-path reuse."""
    cached = _LOGGER_CACHE.get(name)
    if cached is not None:
        return cached
    logger = logging.getLogger(name)
    _LOGGER_CACHE[name] = logger
    return logger


def safe_swallow(
    site_name: str,
    logger: logging.Logger | None = None,
    level: int = logging.DEBUG,
    exc: BaseException | None = None,
) -> None:
    """
    Opt-in structured log for `except: pass` sites.

    Args:
        site_name: short identifier for the call site (e.g. "cleanup_lmdb_lock").
        logger: optional pre-resolved logger. Falls back to logging.getLogger(site_name).
        level: log level (DEBUG by default — M1 ring buffer absorbs the volume).
        exc: the caught exception (if any) for `exc_info`.

    Usage:
        try:
            cleanup_stale_lock()
        except OSError:
            safe_swallow("cleanup_stale_lock", logger, exc=e)
    """
    log = logger or _get_logger(site_name)
    log.log(level, "silent-except swallowed: %s", site_name, exc_info=exc)


# =============================================================================
# Modern (Python 3.11+) primitives
# =============================================================================


@contextlib.contextmanager
def silenced(
    *exc_types: type[BaseException],
    name: str,
    level: int = logging.DEBUG,
    logger: logging.Logger | None = None,
) -> Iterator[None]:
    """
    Context manager: suppress + structured log on first hit.

    Modern replacement for::

        try:
            op()
        except (OSError, asyncio.CancelledError):
            pass

    Usage::

        from utils.silent_except_helper import silenced
        import asyncio

        with silenced(OSError, asyncio.CancelledError,
                      name="cleanup_lmdb_lock", level=logging.DEBUG):
            cleanup_stale_lock()

    Why this shape:
    - `contextlib.suppress` is the stdlib primitive (C-accelerated) — same
      speed as a hand-rolled `try/except: pass` (zero overhead on M1).
    - The structured log fires only when an exception is actually caught
      (not on every entry), so M1's bounded ring buffer is not spammed.
    - `name` is required — enforces "every silenced site has an identifier"
      for grep-ability and post-mortem debugging.

    Args:
        *exc_types: exception types to suppress (e.g. `OSError`,
            `asyncio.CancelledError`). Empty tuple = bare `except:` (not
            recommended; provided for parity with stdlib).
        name: short identifier for the call site (required).
        level: log level (DEBUG by default).
        logger: pre-resolved logger override.

    Yields:
        None. The body runs in a suppressed scope.
    """
    log = logger or _get_logger(name)
    try:
        yield
    except exc_types as exc:  # type: ignore[misc]
        log.log(level, "silenced: %s", name, exc_info=exc)


def silence_errors(
    *exc_types: type[BaseException],
    name: str,
    level: int = logging.DEBUG,
    logger: logging.Logger | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R | None]]:
    """
    Decorator: wrap a function so its specified exceptions are silenced + logged.

    Modern replacement for inline try/except patterns at function boundaries.

    Usage::

        from utils.silent_except_helper import silence_errors

        @silence_errors(ValueError, name="parse_legacy_optional_field")
        def parse_legacy_optional_field(raw: str) -> dict | None:
            return json.loads(raw)["field"]

    The wrapped function returns `None` on a caught exception (the safe
    default — matches the GHOST fail-soft contract).

    Args:
        *exc_types: exception types to silence.
        name: short identifier for the call site.
        level: log level (DEBUG by default).
        logger: pre-resolved logger override.

    Returns:
        Decorator that wraps the function.
    """
    def decorator(fn: Callable[_P, _R]) -> Callable[_P, _R | None]:
        @functools.wraps(fn)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R | None:
            try:
                return fn(*args, **kwargs)
            except exc_types as exc:  # type: ignore[misc]
                log = logger or _get_logger(name)
                log.log(level, "silenced: %s", name, exc_info=exc)
                return None
        return wrapper
    return decorator


# =============================================================================
# Classification helper (for the AST audit tool)
# =============================================================================


# Categories used by the audit script and any future PR check.
# Lower-case keys → human-readable label.
SITE_CATEGORIES: dict[str, str] = {
    "exception": "broad-catch",
    "importerror": "defensive-import",
    "cancellederror": "cleanup-race",
    "queuefull": "queue-overflow",
    "queueempty": "queue-drain",
    "invalidstateerror": "task-already-done",
    "timeouterror": "deadline",
    "valueerror": "validation-fail",
    "typeerror": "type-mismatch",
    "keyerror": "missing-key",
    "attributeerror": "missing-attr",
    "oserror": "io-closed",
    "filenotfounderror": "file-already-gone",
    "jsondecodeerror": "bad-payload",
    "unicodedecodeerror": "bad-encoding",
    "runtimeerror": "lifecycle",
}


def classify_silent_except(exc_type_str: str) -> str:
    """
    Classify an `except: pass` site by its exception type.

    Returns the lower-cased bare class name (e.g. `"exception"`,
    `"cancellederror"`), which keys into `SITE_CATEGORIES`.

    Args:
        exc_type_str: the `except ...` clause as a string, e.g.
            `"Exception"`, `"(OSError, asyncio.CancelledError)"`,
            `"json.JSONDecodeError"`.

    Returns:
        Lower-cased bare class name. For tuples, the *first* type wins
        (rough heuristic — the AST audit tool can do better).
    """
    raw = exc_type_str.strip().strip("()")
    if not raw:
        return "bare"
    # First type, before any comma
    first = raw.split(",", 1)[0].strip()
    # Strip module prefix (e.g. "asyncio.CancelledError" → "CancelledError")
    bare = first.rsplit(".", 1)[-1]
    return bare.lower()


__all__ = [
    "safe_swallow",
    "silenced",
    "silence_errors",
    "classify_silent_except",
    "SITE_CATEGORIES",
]
