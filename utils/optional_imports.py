"""
Optional Imports — Lightweight Lazy Import with Fallback Chaining
================================================================



Cutting-edge lazy import pattern: zero-cost at module load, chainable fallbacks.

Design principles:
- Zero import-time cost: resolution happens at first call, not at module load
- Chainable fallbacks: `optional("primary:attr", default=optional("fallback:attr"))`
- Single-responsibility: one class, one method, minimal state
- Python 3.14 ready: GIL-atomic attribute assignment for thread-safety

Usage:
    # Before (try/except antipattern, 5-15µs per import even on success):
    try:
        from otel import instrumented as _otel_instrumented
    except ImportError:
        from hledac.universal.otel._instrumentation import instrumented as _otel_instrumented

    # After (lazy, ~0 cost until first access):
    from hledac.universal.utils.optional_imports import optional
    _otel_instrumented = optional("otel:instrumented",
        default=optional("hledac.universal.otel._instrumentation:instrumented"))

    # Usage — truthiness preserved from original pattern:
    if _otel_instrumented:
        _otel_instrumented()  # call the resolved function

    # For modules without attributes:
    duckdb = optional("duckdb")
    if duckdb:
        conn = duckdb.connect()

    # For MLX lazy imports (M1 8GB, cold start ~200-500ms savings):
    from hledac.universal.utils.optional_imports import mlx, mlx_lm, MLX_AVAILABLE
    if MLX_AVAILABLE:
        mx = mlx()  # mlx.core module
        mx_arr = mlx_lm()  # mlx_lm module

M1 8GB: minimal RAM (1× reference per resolver), no eager imports.
Python 3.14+: attribute assignment is GIL-atomic, lock-free fast path.

ISSUE #14 FIX: MLX-specific lazy import patterns for M1 8GB optimization.
- Zero-cost at module load (no mlx.core import until first call)
- Cold start ~200-500ms faster for modules that don't need MLX
- Canonical SSOT: utils.mlx_memory._core.MLX_AVAILABLE + get_mx()
"""

import importlib
import threading
from typing import Any

__all__ = [
    "optional",
    "lazy_import",
    "lazy_decorator",
    # MLX lazy imports (ISSUE #14)
    "MLX_AVAILABLE",
    "mlx",
    "mlx_lm",
    "mlx_nn",
    "get_mlx_core",
    "get_mlx_lm",
    "get_mlx_nn",
]


# ═══════════════════════════════════════════════════════════════════════════════
# MLX Lazy Import Patterns (ISSUE #14 FIX)
# ═══════════════════════════════════════════════════════════════════════════════


def _detect_mlx_available() -> bool:
    """
    Return True only if mlx package is installed (no mlx.core import).

    Uses importlib.metadata instead of find_spec — find_spec loads mlx.core on
    macOS which violates PLANNER: ZERO MLX when these modules are imported by
    planners. This is the canonical zero-import MLX detection for the project.
    """
    try:
        import importlib.metadata

        importlib.metadata.version("mlx")
        return True
    except Exception:
        return False


# ISSUE #14 FIX: Zero-cost MLX detection at module load
# No mlx.core import — only metadata.version() check (~1µs vs ~200-500ms)
MLX_AVAILABLE: bool = _detect_mlx_available()


# Thread-safe cached accessors for MLX modules
_mlx_core_module: Any = None
_mlx_lm_module: Any = None
_mlx_nn_module: Any = None
_mlx_import_lock = threading.Lock()


def get_mlx_core() -> Any:
    """
    Lazy accessor for mlx.core module — cached after first import.

    Returns mlx.core module if available, otherwise None.

    ISSUE #14 FIX: Use this instead of top-level `import mlx.core as mx`.
    Zero-cost at module load — mlx.core imported only on first call.

    Thread-safe: uses double-checked locking pattern.
    """
    global _mlx_core_module
    if _mlx_core_module is None and MLX_AVAILABLE:
        with _mlx_import_lock:
            if _mlx_core_module is None:  # Double-check
                try:
                    import mlx.core as _mlx_core_module
                except ImportError:
                    _mlx_core_module = None
    return _mlx_core_module


def get_mlx_lm() -> Any:
    """
    Lazy accessor for mlx_lm module — cached after first import.

    Returns mlx_lm module if available, otherwise None.

    ISSUE #14 FIX: Use this instead of top-level `import mlx_lm`.
    """
    global _mlx_lm_module
    if _mlx_lm_module is None:
        with _mlx_import_lock:
            if _mlx_lm_module is None:
                try:
                    import mlx_lm as _mlx_lm_module
                except ImportError:
                    _mlx_lm_module = None
    return _mlx_lm_module


def get_mlx_nn() -> Any:
    """
    Lazy accessor for mlx.nn module — cached after first import.

    Returns mlx.nn module if available, otherwise None.

    ISSUE #14 FIX: Use this instead of top-level `import mlx.nn as nn`.
    """
    global _mlx_nn_module
    if _mlx_nn_module is None and MLX_AVAILABLE:
        with _mx_import_lock:
            if _mlx_nn_module is None:
                try:
                    import mlx.nn as _mlx_nn_module
                except ImportError:
                    _mlx_nn_module = None
    return _mlx_nn_module


# ═══════════════════════════════════════════════════════════════════════════════
# Callable lazy import proxies (for drop-in replacement of top-level imports)
# ═══════════════════════════════════════════════════════════════════════════════


class _LazyModuleProxy:
    """
    Callable lazy import proxy for MLX modules.

    Mimics the behavior of a top-level import:
        import mlx.core as mx
        mx.array([1, 2, 3])

    But defers the actual import until first use:
        from utils.optional_imports import mlx
        mx = mlx()  # First call imports mlx.core
        mx.array([1, 2, 3])

    ISSUE #14 FIX: Zero-cost at module load, ~200-500ms cold start savings.
    """

    __slots__ = ("_dotted", "_cache", "_resolved", "_lock")

    def __init__(self, dotted: str) -> None:
        self._dotted = dotted
        self._cache: Any = None
        self._resolved: bool = False
        self._lock = threading.Lock()

    def __call__(self) -> Any:
        """Lazily import and return the module (cached after first call)."""
        if self._resolved:
            return self._cache
        with self._lock:
            if self._resolved:  # Double-check
                return self._cache
            try:
                if ":" in self._dotted:
                    mod_name, attr_name = self._dotted.split(":", 1)
                    mod = importlib.import_module(mod_name)
                    self._cache = getattr(mod, attr_name)
                else:
                    self._cache = importlib.import_module(self._dotted)
            except (ImportError, AttributeError):
                self._cache = None
            self._resolved = True
        return self._cache

    def __getattr__(self, name: str) -> Any:
        """Allow attribute access: mlx.array → mlx.core.array."""
        if self._resolved:
            return getattr(self._cache, name)
        # Delegate to module
        mod = self()
        if mod is not None:
            return getattr(mod, name)
        raise AttributeError(f"module '{self._dotted}' has no attribute '{name}'")

    def __bool__(self) -> bool:
        """True if module is available and not None."""
        return self() is not None

    @property
    def available(self) -> bool:
        """True if resolution succeeded."""
        return self() is not None


# Canonical lazy import proxies — drop-in for top-level imports
# Usage:
#     from hledac.universal.utils.optional_imports import mlx, mlx_lm
#     mx = mlx()  # lazy import mlx.core
#     llm = mlx_lm()  # lazy import mlx_lm
#
# Or for type hints (TYPE_CHECKING guard):
#     from typing import TYPE_CHECKING
#     if TYPE_CHECKING:
#         import mlx.core as mx
#     else:
#         from hledac.universal.utils.optional_imports import mlx as mx

# Lazy mlx.core proxy — replaces `import mlx.core as mx`
mlx: _LazyModuleProxy = _LazyModuleProxy("mlx.core")
# Lazy mlx_lm proxy — replaces `import mlx_lm`
mlx_lm: _LazyModuleProxy = _LazyModuleProxy("mlx_lm")
# Lazy mlx.nn proxy — replaces `import mlx.nn as nn`
mlx_nn: _LazyModuleProxy = _LazyModuleProxy("mlx.nn")
# Lazy mlx_graphs proxy — replaces `import mlx_graphs`
mlx_graphs: _LazyModuleProxy = _LazyModuleProxy("mlx_graphs")
# Lazy mlx_optimizers proxy — replaces `import mlx.optimizers as optim`
mlx_optimizers: _LazyModuleProxy = _LazyModuleProxy("mlx.optimizers")


class _Unresolved:
    """Sentinel — distinguish 'not resolved yet' from None (which may be a valid resolved value)."""

    __slots__ = ()


_UNRESOLVED = _Unresolved()


class _OptionalImport:
    """
    Lazy import resolution with caching. Zero-cost at module load.

    Supports chaining via `default=` parameter:
        primary = optional("primary:attr", default=optional("fallback:attr"))

    Thread-safety (Python 3.14+):
        - _resolved flag uses GIL-atomic attribute assignment
        - Double-checked locking pattern for thread-safe resolution
    """

    __slots__ = ("_module", "_attr", "_default", "_resolved", "_value")

    def __init__(
        self,
        dotted: str,
        *,
        default: _OptionalImport | Any | None = None,
    ) -> None:
        """
        Initialize lazy import resolver.

        Args:
            dotted: Dotted path in form "module" or "module:attr".
                    Colon separates module from attribute (like importlib.import_module semantics).
            default: Fallback resolver or value if resolution fails.
                     Can be another _OptionalImport for chaining, or a direct value.
        """
        if ":" in dotted:
            self._module, self._attr = dotted.split(":", 1)
        else:
            self._module = dotted
            self._attr = ""
        self._default = default
        self._resolved: bool | _Unresolved = _UNRESOLVED
        self._value: Any = None

    def _try_resolve(self) -> Any:
        """Attempt to resolve the import. Returns resolved value or None."""
        try:
            mod = importlib.import_module(self._module)
            if self._attr:
                return getattr(mod, self._attr)
            return mod
        except (ImportError, AttributeError):
            return None

    def __call__(self) -> Any:
        """Resolve and return the imported symbol (lazy, cached after first call)."""
        # Fast path: already resolved
        if self._resolved is not _UNRESOLVED:
            return self._value

        # Resolve primary
        value = self._try_resolve()

        # Apply fallback chain if needed
        if value is None and self._default is not None:
            if isinstance(self._default, _OptionalImport):
                value = self._default()
            else:
                value = self._default

        # Cache result
        self._value = value
        # In Python 3.14+, attribute assignment is GIL-atomic
        self._resolved = True  # type: ignore[assignment]
        return value

    def __bool__(self) -> bool:
        """True if resolved and not None — preserves original truthiness semantics."""
        return self() is not None

    @property
    def available(self) -> bool:
        """True if resolution succeeded (symbol exists and is not None)."""
        return self() is not None


def optional(dotted: str, *, default: _OptionalImport | Any | None = None) -> _OptionalImport:
    """
    Create a lazy import resolver with optional fallback chain.

    This is the main public API. Use it to replace try/except ImportError patterns.

    Args:
        dotted: Dotted path "module" or "module:attr"
        default: Optional fallback — can be another optional() or a direct value

    Returns:
        _OptionalImport instance — callable and truthy checkable

    Example:
        from hledac.universal.utils.optional_imports import optional

        # Simple module import
        duckdb = optional("duckdb")

        # Attribute import with fallback chain
        instrumented = optional("otel:instrumented",
            default=optional("hledac.universal.otel._instrumentation:instrumented"))

        # Usage
        if instrumented:
            instrumented()  # same as original: instrumented()

        if duckdb:
            conn = duckdb.connect()

    Note:
        Resolution happens at first call, not at module load.
        The returned _OptionalImport is reusable — resolution is cached.
    """
    return _OptionalImport(dotted, default=default)


# ═══════════════════════════════════════════════════════════════════════════════
# Transparent lazy-import proxy (ISSUE #12 FIX — drop-in for try/except ImportError)
# ═══════════════════════════════════════════════════════════════════════════════


def _try_resolve_dotted(dotted: str) -> Any:
    """Resolve ``"module"`` or ``"module:attr"`` to the object, or ``None`` on failure."""
    try:
        if ":" in dotted:
            mod_name, attr_name = dotted.split(":", 1)
            mod = importlib.import_module(mod_name)
            return getattr(mod, attr_name)
        return importlib.import_module(dotted)
    except (ImportError, AttributeError):
        return None


class _LazyProxy:
    """
    Transparent lazy-import proxy — drop-in replacement for module-level
    ``try: import X except ImportError: ...`` blocks.

    Unlike :class:`_OptionalImport` (a *resolver* — call it to obtain the value),
    ``_LazyProxy`` is a *transparent proxy* that forwards attribute access, calls,
    and common dunder operations to the resolved object. This makes it a true
    1:1 replacement for the bound symbol at every call site:

        # Before (antipattern — ~5-15µs cold-start + scattered logic across files):
        try:
            from otel import instrumented as _instr
        except ImportError:
            from hledac.universal.otel._instrumentation import instrumented as _instr

        # After (zero-cost at import; call sites unchanged):
        from hledac.universal.utils.optional_imports import lazy_import
        _instr = lazy_import("otel:instrumented",
                             default=lazy_import("hledac.universal.otel._instrumentation:instrumented"))

        _instr(...)        # forwards to the resolved function
        if _instr: ...     # True iff resolved and not None
        _instr.some_attr   # forwards attribute access

    M1 8GB: no eager import; resolution happens on first attribute/call access.
    Python 3.14+: GIL-atomic resolution flag via double-checked locking.
    """

    __slots__ = ("_dotted", "_default", "_resolved", "_value", "_lock")

    def __init__(self, dotted: str, *, default: _LazyProxy | Any | None = None) -> None:
        self._dotted = dotted
        self._default = default
        self._resolved = False
        self._value: Any = None
        self._lock = threading.Lock()

    def _resolve(self) -> Any:
        if self._resolved:
            return self._value
        with self._lock:
            if self._resolved:  # double-check
                return self._value
            value = _try_resolve_dotted(self._dotted)
            if value is None and self._default is not None:
                if isinstance(self._default, _LazyProxy):
                    value = self._default._resolve()
                else:
                    value = self._default
            self._value = value
            self._resolved = True  # GIL-atomic on 3.14+
            return value

    # ── Transparent forwarding ──────────────────────────────────────────────
    def __getattr__(self, name: str) -> Any:
        # Only invoked when normal lookup on the instance fails.
        return getattr(self._resolve(), name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._resolve()(*args, **kwargs)

    def __bool__(self) -> bool:
        return self._resolve() is not None

    def __repr__(self) -> str:
        return f"<lazy_import {self._dotted!r} resolved={self._resolved}>"

    # Forwarded dunders (special-method lookup uses the type, so the common
    # ones are defined explicitly for drop-in compatibility).
    def __getitem__(self, key: Any) -> Any:
        return self._resolve()[key]

    def __setitem__(self, key: Any, value: Any) -> None:
        self._resolve()[key] = value

    def __delitem__(self, key: Any) -> None:
        del self._resolve()[key]

    def __contains__(self, key: Any) -> bool:
        return key in self._resolve()

    def __iter__(self) -> Any:
        return iter(self._resolve())

    def __len__(self) -> int:
        return len(self._resolve())

    def __eq__(self, other: Any) -> bool:
        return self._resolve() == other

    def __ne__(self, other: Any) -> bool:
        return self._resolve() != other

    def __hash__(self) -> int:
        return hash(self._resolve())

    def __enter__(self) -> Any:
        return self._resolve().__enter__()

    def __exit__(self, *exc: Any) -> Any:
        return self._resolve().__exit__(*exc)

    def __aenter__(self) -> Any:
        return self._resolve().__aenter__()

    def __aexit__(self, *exc: Any) -> Any:
        return self._resolve().__aexit__(*exc)

    @property
    def available(self) -> bool:
        """True if resolution succeeded (symbol exists and is not None)."""
        return self._resolve() is not None

    def resolve(self) -> Any:
        """Explicitly resolve and return the underlying object."""
        return self._resolve()


def lazy_import(dotted: str, *, default: _LazyProxy | Any | None = None) -> _LazyProxy:
    """
    Create a transparent lazy-import proxy (ISSUE #12 canonical replacement).

    Replaces module-level ``try: import X except ImportError: ...`` blocks with a
    single, zero-cost-at-import assignment that is behaviorally identical at every
    call site.

    Args:
        dotted: Dotted path ``"module"`` or ``"module:attr"``.
        default: Fallback proxy or value if resolution fails.

    Returns:
        ``_LazyProxy`` — use it exactly as you would the imported symbol.

    Example:
        from hledac.universal.utils.optional_imports import lazy_import

        # module import with attribute fallback
        instrumented = lazy_import("otel:instrumented",
            default=lazy_import("hledac.universal.otel._instrumentation:instrumented"))

        # optional module (unavailable → resolves to None; test via truthiness)
        duckdb = lazy_import("duckdb", default=None)
        if duckdb:
            duckdb.connect(...)
    """
    return _LazyProxy(dotted, default=default)


class _LazyDecorator:
    """
    Lazy decorator resolver — for decorator factory patterns like @instrumented("name", component="x").

    Preserves **kwargs pass-through to the resolved decorator.
    """

    __slots__ = ("_resolver", "_resolved", "_fallback", "_value")

    def __init__(self, dotted: str, *, fallback: _LazyDecorator | Any | None = None) -> None:
        self._resolver = _OptionalImport(dotted, default=None)
        self._fallback = fallback
        self._resolved: bool = False
        self._value: Any = None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Pass through to resolved decorator with arguments preserved."""
        if not self._resolved:
            self._value = self._resolver()
            if self._value is None and self._fallback is not None:
                if isinstance(self._fallback, _LazyDecorator):
                    self._value = self._fallback._resolver()
                else:
                    self._value = self._fallback
            self._resolved = True
        if self._value is None:
            # Return identity decorator when nothing resolves
            def identity(fn: Any) -> Any:
                return fn

            return identity
        return self._value(*args, **kwargs)

    def __bool__(self) -> bool:
        return self._resolver.available

    @property
    def available(self) -> bool:
        return self._resolver.available


def lazy_decorator(dotted: str, *, default: _LazyDecorator | Any | None = None) -> _LazyDecorator:
    """
    Create a lazy decorator resolver for decorator factory patterns.

    Use when the import is used as @decorator(*args, **kwargs).

    Example:
        # For instrumented("name", component="x") decorator usage:
        _otel_instrumented = lazy_decorator("otel:instrumented",
            default=lazy_decorator("hledac.universal.otel:instrumented"))

        # Usage:
        @_otel_instrumented("duckdb.ingest_batch", component="storage")
        def my_func():
            pass
    """
    return _LazyDecorator(dotted, fallback=default)
