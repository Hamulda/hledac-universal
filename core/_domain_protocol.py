"""
Domain Protocol & Generic Delegation Framework (F285)

Eliminates boilerplate duplication between _RustXxxDomain / _PythonXxxDomain pairs
using a metaclass that generates delegation methods at class definition time.

Performance (M1): Methods are generated ONCE at class definition (not per-call
via __getattr__), so there's zero per-call overhead vs hand-written code.

Architecture:
    Protocol[T]             — type checking interface (PEP 544)
    DelegatingDomainMeta    — metaclass: injects delegation methods from _spec
    DelegatingDomain        — base class with shared __init__ and _convert

Usage:
    from hledac.universal.core._domain_protocol import (
        DelegatingDomain, MethodSpec, RustTarget, PythonTarget,
        make_spec, make_spec_with_conv,
    )

    # Rust domain — delegates to self._ext.<method>()
    class _RustUrlDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
        __slots__ = ("_ext",)
        _target = RustTarget
        _spec = make_spec("normalize", "fingerprint", "strip_tracking",
                           "is_valid_url", "filter_valid", "extract_host")
        # Special override for extract_domain (has metrics injection)
        def extract_domain(self, url: str) -> str:
            ...

    # Python domain — delegates to _python_<method>()
    class _PythonUrlDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
        __slots__ = ()
        _target = PythonTarget
        _spec = make_spec("normalize", "fingerprint", "strip_tracking",
                           "is_valid_url", "filter_valid", "extract_domain",
                           "classify_url", "batch_classify", "extract_host")
"""


import importlib
import types
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

__all__ = [
    "DelegatingDomain",
    "DelegatingDomainMeta",
    "MethodSpec",
    "RustTarget",
    "PythonTarget",
    "make_spec",
    "make_spec_with_conv",
]


# ---------------------------------------------------------------------------
# Markers / Targets
# ---------------------------------------------------------------------------

class _RustMarker:
    """Sentinel: method calls self._ext.<ext_name>(...)"""
    __slots__ = ()


class _PythonMarker:
    """Sentinel: method calls _python_<ext_name>(...)"""
    __slots__ = ()


# Module-level singletons — used as _target class attribute
RustTarget: Any = _RustMarker()
PythonTarget: Any = _PythonMarker()


# ---------------------------------------------------------------------------
# Method Specification
# ---------------------------------------------------------------------------

class MethodSpec:
    """
    Specification for one delegated method.

    Args:
        name: method name on the domain class
        ext_name: name on the Rust extension OR Python fallback function name
                  (default: same as name)
        rust_conv: Rust return type conversion ("list" | "str" | "int" | "float" | "bytes")
        no_except: if True, skips try/except overhead on hot-path Rust calls.
                   Use for batch operations where exceptions are never expected.
    """
    __slots__ = ("name", "ext_name", "rust_conv", "no_except")

    def __init__(
        self,
        name: str,
        ext_name: str | None = None,
        rust_conv: str | None = None,
        no_except: bool = False,
    ) -> None:
        self.name = name
        self.ext_name = ext_name or name
        self.rust_conv = rust_conv
        self.no_except = no_except


def make_spec(*names: str, no_except: bool = False) -> list[MethodSpec]:
    """Build MethodSpec list where ext_name == name for all entries."""
    return [MethodSpec(n, no_except=no_except) for n in names]


def make_spec_with_conv(
    specs: list[tuple[str, str, str]],
    no_except: bool = False,
) -> list[MethodSpec]:
    """Build MethodSpec list from (name, ext_name, rust_conv) tuples."""
    return [MethodSpec(n, e, c, no_except=no_except) for n, e, c in specs]


# ---------------------------------------------------------------------------
# Metaclass: generates delegation methods at class definition time
# ---------------------------------------------------------------------------

_RUST_BACKEND_MODULE: str = "core.rust_backend"


class DelegatingDomainMeta(type):
    """
    Metaclass that injects delegation methods into domain classes.

    For each MethodSpec in _spec, generates a method that delegates to:
      - RustTarget: self._ext.<ext_name>(...)
      - PythonTarget: _python_<ext_name>(...)

    Methods defined explicitly in the class body take precedence (special cases).

    M1 8GB: __slots__ keeps instances ~48 bytes. No __getattr__ overhead.
    """

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        target: Any = RustTarget,
        spec: list[MethodSpec] | None = None,
        **kwds: Any,
    ) -> DelegatingDomainMeta:
        # Extract _target and _spec from namespace (may be inherited)
        cls_target = namespace.get("_target", RustTarget)
        cls_spec = namespace.get("_spec", [])

        # Generate delegation methods for all specs
        for ms in cls_spec:
            if ms.name in namespace:
                # Explicitly defined in class body — skip (special case override)
                continue

            if cls_target is RustTarget:
                namespace[ms.name] = _make_rust_delegation(ms)
            elif cls_target is PythonTarget:
                namespace[ms.name] = _make_python_delegation(ms)
            else:
                raise TypeError(f"Unknown _target: {cls_target!r}")

        return super().__new__(mcs, name, bases, namespace, **kwds)


def _make_rust_delegation(ms: MethodSpec) -> Any:
    """
    Generate a Rust delegation method for a MethodSpec.

    Returns a function that calls self._ext.<ext_name>(...) with optional
    return-type conversion.

    When no_except=True (hot-path batch operations), the try/except overhead
    is skipped — Rust FFI exceptions are structural (PyO3 panics) and indicate
    bugs, not recoverable runtime errors. Python fallback is handled by the
    batch-level try/except in the calling code, not here.
    """
    ext_name = ms.ext_name
    conv = ms.rust_conv
    no_except = ms.no_except

    if no_except:
        # Hot-path: no exception wrapper — 0μs overhead per call
        if conv:
            def method(self, *args: Any, **kwargs: Any) -> Any:
                result = getattr(self._ext, ext_name)(*args, **kwargs)
                return _convert(result, conv)
        else:
            def method(self, *args: Any, **kwargs: Any) -> Any:
                return getattr(self._ext, ext_name)(*args, **kwargs)
        method.__name__ = ms.name
        return method
    elif conv:
        # Needs return value conversion
        def method(self, *args: Any, **kwargs: Any) -> Any:
            try:
                result = getattr(self._ext, ext_name)(*args, **kwargs)
                return _convert(result, conv)
            except Exception:
                return None

        method.__name__ = ms.name
        return method
    else:
        # Direct delegation with exception handling
        def method(self, *args: Any, **kwargs: Any) -> Any:
            try:
                return getattr(self._ext, ext_name)(*args, **kwargs)
            except Exception:
                return None

        method.__name__ = ms.name
        return method


def _make_python_delegation(ms: MethodSpec) -> Any:
    """
    Generate a Python fallback delegation method for a MethodSpec.

    Returns a function that calls _python_<ext_name>(...) from core.rust_backend.
    """
    func_name = f"_python_{ms.ext_name}"

    def method(self, *args: Any, **kwargs: Any) -> Any:
        # Import lazily — avoids circular import at module load time
        mod = importlib.import_module(_RUST_BACKEND_MODULE)
        func = getattr(mod, func_name, None)
        if func is None:
            raise AttributeError(
                f"Python fallback function {func_name!r} not found in {_RUST_BACKEND_MODULE}"
            )
        return func(*args, **kwargs)

    method.__name__ = ms.name
    return method


def _convert(value: Any, conv: str) -> Any:
    """Apply type conversion for Rust return values."""
    if conv == "list" and not isinstance(value, list):
        return list(value) if hasattr(value, "__iter__") else []
    if conv == "str" and not isinstance(value, str):
        return str(value) if value is not None else ""
    if conv == "int":
        return int(value) if value is not None else 0
    if conv == "float":
        return float(value) if value is not None else 0.0
    if conv == "bytes" and not isinstance(value, bytes):
        return bytes(value) if value is not None else b""
    return value


# ---------------------------------------------------------------------------
# Base class — shared init and type conversion
# ---------------------------------------------------------------------------

T = TypeVar("T", default=object)


class DelegatingDomain:
    """
    Base class for all delegation domains.

    Subclasses MUST define:
        __slots__ = ("_ext",)   [Rust variant]   or __slots__ = ()  [Python variant]
        _target   = RustTarget  or PythonTarget
        _spec     = list[MethodSpec]

    Subclasses CAN override individual methods for special behavior
    (e.g., metrics injection, complex logic). Overrides take precedence
    over auto-generated delegation methods.

    M1 8GB: __slots__ keeps instance size ~48 bytes vs ~200+ with __dict__.
    """

    # Subclasses override these
    __slots__ = ("_ext",)
    _target: Any = RustTarget
    _spec: list[MethodSpec] = []

    def __init__(self, ext: Any | None = None) -> None:
        """
        Args:
            ext: For Rust domains — the hledac_rust_extensions module.
                 For Python domains — ignored (kept for API compatibility).
        """
        # Use object.__setattr__ to bypass __setattr__ in subclasses
        object.__setattr__(self, "_ext", ext)
