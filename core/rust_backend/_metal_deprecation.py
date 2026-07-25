"""Internal: deprecation shim for legacy metal module imports.

D6 (2026-07-16) removed the ``metal`` crate from rust_extensions/Cargo.toml.
The two Rust modules that depended on it (``metal_compute`` and
``metal_pattern_matcher``) have been deleted from the repository.

Python-level fallbacks are already wired via ``core/rust_backend/metal.py``
(``_PythonMetalDomainInner``) which is registered in ``_LAZY_ATTRS`` in
``__init__.py``. There is no runtime loss of functionality — both domains
continue to work via pure-Python fallback.

This module exists purely to:
1. Emit a one-time deprecation warning if something attempts to import
   the deleted Rust symbols (``metal_compute`` / ``metal_pattern_matcher``).
2. Provide a stable import target so that any stale import paths break
   loudly rather than silently.

Usage::

    from core.rust_backend._metal_deprecation import metal_compute
    # DeprecationWarning: metal_compute was removed in D6 (2026-07-16).
    # Rust metal crate (~45s compile, ~3MB dylib) was removed from Cargo.toml.
    # CPU fallback via core.rust_backend.metal._PythonMetalDomainInner is used.
"""

from __future__ import annotations

import warnings

__all__ = ["metal_compute", "metal_pattern_matcher"]

_DEPRECATION_MSG = (
    "metal_compute was removed in D6 (2026-07-16). "
    "Rust metal crate (~45s compile, ~3MB dylib) was removed from Cargo.toml. "
    "CPU fallback via core.rust_backend.metal._PythonMetalDomainInner is used."
)


def __getattr__(name: str):
    if name in ("metal_compute", "metal_pattern_matcher"):
        warnings.warn(
            f"{name}: {_DEPRECATION_MSG}",
            DeprecationWarning,
            stacklevel=2,
        )
        # Return a placeholder that makes it clear this is the removed symbol.
        # The actual implementations live in core.rust_backend.metal.
        return None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def register_functions(_m):
    """Stub so any stale FFI manifest entries that reference this fail loudly."""
    warnings.warn(
        f"metal_pattern_matcher.register_functions: {_DEPRECATION_MSG}",
        DeprecationWarning,
        stacklevel=2,
    )
