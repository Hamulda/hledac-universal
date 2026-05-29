"""
Low-overhead signpost profiler using kdebug_signpost.

Provides:
- signpost_interval context manager for timing code sections
- Deterministic code generation for consistent signposts across runs
- Safe harness for macOS API with fallback for non-Darwin
"""

import ctypes
import sys
from contextlib import contextmanager

# Try to load macOS System APIs
if sys.platform == "darwin":
    try:
        _libsys = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        _sp_start = _libsys.kdebug_signpost_start
        _sp_end = _libsys.kdebug_signpost_end
        for fn in (_sp_start, _sp_end):
            fn.restype = None
            fn.argtypes = [ctypes.c_uint32] * 5
        _SIGNPOST_AVAILABLE = True
    except Exception:
        _SIGNPOST_AVAILABLE = False
else:
    _SIGNPOST_AVAILABLE = False

# Code registry for deterministic signpost codes
_CODE_REGISTRY: dict[str, int] = {}


def _get_code(name: str) -> int:
    """Generate deterministic code for consistent signposts across runs."""
    if name not in _CODE_REGISTRY:
        import hashlib
        _CODE_REGISTRY[name] = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
    return _CODE_REGISTRY[name]


@contextmanager
def signpost_interval(category: str, name: str):
    """
    Context manager for timing code sections with Instruments signposts.

    Usage:
        with signpost_interval("Hledac", "mlx_inference"):
            # ... code to time ...
            pass

    Args:
        category: Category name (e.g., "Hledac", "MLX", "Network")
        name: Operation name (e.g., "mlx_inference", "fetch_page")
    """
    if not _SIGNPOST_AVAILABLE:
        yield
        return

    code = _get_code(f"{category}.{name}")
    _sp_start(code, 0, 0, 0, 0)
    try:
        yield
    finally:
        _sp_end(code, 0, 0, 0, 0)


def is_signpost_available() -> bool:
    """Check if signpost API is available."""
    return _SIGNPOST_AVAILABLE


def get_stats() -> dict:
    """Get signpost statistics."""
    return {
        "available": _SIGNPOST_AVAILABLE,
        "codes_registered": len(_CODE_REGISTRY),
    }
