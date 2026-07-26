"""
compat/rust_backend_legacy — Legacy shim for rust_backend exports.

Deprecated: import from ``core.rust_backend`` directly.
This module provides backward compatibility for ``from core import rust_backend``.
"""
import warnings

__all__ = ["rust_backend"]

warnings.warn(
    "compat.rust_backend_legacy is deprecated. Import from core.rust_backend instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.core.rust_backend import rust as rust_backend
