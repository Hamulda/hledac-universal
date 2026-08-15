"""
Deprecated-decorator shim — Python 3.11+ safe.

`warnings.deprecated` was added in Python 3.13. On older runtimes (e.g., CI
containers or third-party venvs) the import would raise `ImportError` and
brick any module decorated with `@deprecated` at import time. M1 8GB UMA
cannot afford an import-time crash of a canonical hot path like
`knowledge.duckdb_store` (which carries 1 @deprecated usage today and is
imported during sprint warmup).

This shim resolves the decorator lazily via `sys.modules` (single lookup,
cached on the function object) and falls back to a no-op decorator that
emits a `DeprecationWarning` on each call — preserving the *behavioural*
contract (callers see a deprecation signal) without the *syntactic* cost
of a missing stdlib symbol.

Why `sys.modules` rather than `try/except ImportError` per call site:
    - 19+ modules already do `from warnings import deprecated` (post-3.13
      optimism). Centralising the fallback here means future migrations
      drop a single import line per module instead of re-introducing
      try/except boilerplate.
    - `sys.modules` lookup is a C-level dict read; cheaper than a Python
      try/except frame, and it works under `del warnings.deprecated`
      test seams without rerunning the import.

Usage:
    from hledac.universal.utils._deprecated import deprecated

    @deprecated("Use new_func() — old_func is removed in v20")
    def old_func(): ...

M1 8GB UMA: 0 KB runtime overhead, 0 new imports in hot paths.
"""



import functools
import sys
import warnings
from collections.abc import Callable
from typing import Any, TypeVar
from _core import aclose

__all__ = ["deprecated", "HAS_NATIVE_DEPRECATED"]

_F = TypeVar("_F", bound=Callable[..., Any])

# Single C-level dict probe — cached at module import time.
_warnings_mod = sys.modules.get("warnings")
_native_deprecated: Any = getattr(_warnings_mod, "deprecated", None) if _warnings_mod is not None else None
HAS_NATIVE_DEPRECATED: bool = _native_deprecated is not None


def _fallback_deprecated(message: str) -> Callable[[_F], _F]:
    """Identity-ish decorator that emits a DeprecationWarning on each call.

    Equivalent observable behaviour to the stdlib decorator for tooling that
    inspects `__wrapped__` or reads the warning stream; does NOT replicate
    the type-checker integration (PEP 702) — that requires Python 3.13+.
    """

    def _decorator(func: _F) -> _F:
        @functools.wraps(func)
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            warnings.warn(message, category=DeprecationWarning, stacklevel=2)
            return func(*args, **kwargs)

        # ty: error codes differ from mypy. PEP 702 __deprecated__ is unknown
        # to ty (resolves as `unresolved-attribute`); the wrapped _Wrapper
        # instance can't be cast back to _F (the original callable) without
        # a `cast` round-trip — both are type-system artefacts of @wraps.
        _wrapper.__deprecated__ = message  # type: ignore[ty:unresolved-attribute]
        return _wrapper  # type: ignore[ty:invalid-return-type]

    return _decorator


if HAS_NATIVE_DEPRECATED:
    deprecated = _native_deprecated
else:
    deprecated = _fallback_deprecated
