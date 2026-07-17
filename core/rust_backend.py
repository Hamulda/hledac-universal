"""
core/rust_backend.py — DEPRECATED: redirects to core.rust_backend package.

ISSUE-001 (2026-07-15):
    Python 3.12+ has ambiguous import resolution when both a file
    core/rust_backend.py AND a directory core/rust_backend/ exist.
    The file (3,281 LOC monolith) was being loaded on some machines,
    the package (579 LOC facade) on others — silent import drift.

    The canonical implementation lives in core/rust_backend/ package.
    This file is kept only to avoid breaking existing import paths.
    It re-exports from the package with a DeprecationWarning.

Migration:
    Old (ambiguous):  from core.rust_backend import rust
    New (canonical):   from core.rust_backend import rust  # same line!
                      # — the package is now the canonical location

The package core/rust_backend/__init__.py provides:
    RustBackend, rust, AccelBackend, AccelInfo,
    get_accel(), reset_accel(), check_metal_availability()
"""
from __future__ import annotations

import os
import warnings

__all__ = [
    "RustBackend",
    "rust",
    "AccelBackend",
    "AccelInfo",
    "get_accel",
    "reset_accel",
    "check_metal_availability",
]

# Detect if this file is shadowing the package
_PKG_INIT = os.path.join(os.path.dirname(__file__), "rust_backend", "__init__.py")
if os.path.exists(_PKG_INIT):
    warnings.warn(
        "core/rust_backend is DEPRECATED. "
        "The canonical implementation is now in the core/rust_backend/ package. "
        "Your import 'from core.rust_backend import rust' will work identically — "
        "but please update to 'from core.rust_backend import rust' explicitly "
        "to silence this warning (ISSUE-001).",
        DeprecationWarning,
        stacklevel=2,
    )

# ---------------------------------------------------------------------------
# Redirect all exports to the canonical package
# ---------------------------------------------------------------------------
from core.rust_backend import (  # noqa: E402, F401
    RustBackend,
    rust,
    AccelBackend,
    AccelInfo,
    get_accel,
    reset_accel,
    check_metal_availability,
)
