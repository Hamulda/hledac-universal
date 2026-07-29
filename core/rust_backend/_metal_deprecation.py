"""Internal: Metal compute feature-gate shim.

ISSUE 3.6 (B-9): metal_compute.rs is feature-gated behind ``#[cfg(feature = "metal")]``
in lib.rs. The ``metal`` Cargo feature is an empty stub (no metal-framework crate
dependency) — the module provides CPU/NEON fallback implementations only.

When ``HLEDAC_BUILD=metal`` is set (or ``--features metal`` in maturin):
  ``rust.metal_compute`` IS available as a PyO3 module.

When the metal feature is not enabled:
  - ``metal_compute`` is not compiled into the .so at all
  - Python fallback via ``core.rust_backend.metal._PythonMetalDomainInner`` is used
    (registered in ``_LAZY_ATTRS`` in ``__init__.py``)

This shim provides a stable import target that:
1. Warns when the metal feature is NOT enabled (not removed — it was never there)
2. Returns None so that callers that already handle None gracefully continue to work

Usage::

    from hledac.universal.core.rust_backend._metal_deprecation import metal_compute
    # DeprecationWarning (only when HLEDAC_BUILD does NOT include "metal"):
    #   metal_compute: Rust metal_compute module not compiled (metal feature not enabled).
    #   CPU fallback via core.rust_backend.metal._PythonMetalDomainInner is used.
"""

from __future__ import annotations

import warnings

__all__ = ["metal_compute"]

_FEATURE_MSG = (
    "metal_compute: Rust metal_compute module not compiled (metal feature not enabled). "
    "CPU/NEON fallback via core.rust_backend.metal._PythonMetalDomainInner is used."
)


def __getattr__(name: str):
    if name == "metal_compute":
        warnings.warn(
            f"{name}: {_FEATURE_MSG}",
            DeprecationWarning,
            stacklevel=2,
        )
        return None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
