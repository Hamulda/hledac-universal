# _prober.py — one-time Rust extension availability probe
"""
Probes hledac_rust_extensions exactly once, returns a frozen result.
Never raises — all exceptions caught and surfaced as availability=False.
"""

import logging
import sys
from dataclasses import dataclass
import msgspec

logger = logging.getLogger(__name__)

_RUST_MIN_VERSION: tuple[int, int, int] = (0, 1, 0)

# Cached probe result — set once at module load
_PROBED: bool | None = None
_EXT: object | None = None


class ProbeResult(msgspec.Struct, frozen=True):
    """Frozen result of the Rust extension probe."""

    available: bool
    ext: object | None
    version_str: str
    version_tuple: tuple[int, int, int]
    backend: str  # "rust" | "python"

    @property
    def is_compatible(self) -> bool:
        return self.available and self.version_tuple >= _RUST_MIN_VERSION


def _parse_version(ext: object | None) -> tuple[tuple[int, int, int], str]:
    """Extract version tuple and string from extension module."""
    ver_tuple: tuple[int, int, int] = (0, 0, 0)
    ver_str = "unknown"

    if ext is None:
        return ver_tuple, ver_str

    try:
        version_info = getattr(ext, "__version_info__", None)
        if callable(version_info):
            result = version_info()
            if isinstance(result, tuple) and len(result) >= 3:
                ver_tuple = (int(result[0]), int(result[1]), int(result[2]))
                ver_str = str(result)
        elif hasattr(ext, "__version__"):
            ver_str = str(ext.__version__)
            parts = ver_str.split(".")[:3]
            ver_tuple = (
                int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0,
                int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0,
                int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
            )
    except Exception:
        pass

    return ver_tuple, ver_str


def probe() -> ProbeResult:
    """
    One-time probe of hledac_rust_extensions.
    Stores result in module globals so repeated calls are free.
    """
    global _PROBED, _EXT

    if _PROBED is not None:
        # Already probed — return cached
        ver_tuple, ver_str = _parse_version(_EXT) if _EXT else ((0, 0, 0), "unknown")
        backend = "rust" if _PROBED else "python"
        return ProbeResult(available=_PROBED, ext=_EXT, version_str=ver_str, version_tuple=ver_tuple, backend=backend)

    # First call — do the probe
    try:
        import hledac_rust_extensions as ext

        ver_tuple, ver_str = _parse_version(ext)

        if ver_tuple < _RUST_MIN_VERSION:
            logger.debug(
                f"[RustProbe] hledac_rust_extensions {ver_tuple} < "
                f"required {_RUST_MIN_VERSION}; Python fallbacks enabled"
            )
            _PROBED = False
            _EXT = None
        else:
            _PROBED = True
            _EXT = ext
            logger.debug(
                f"[RustProbe] hledac_rust_extensions loaded (version {ver_str})"
            )
    except Exception as e:
        # Catch ALL: ImportError, OSError, AttributeError, etc.
        logger.debug(f"[RustProbe] hledac_rust_extensions unavailable: {e}")
        _PROBED = False
        _EXT = None

    ver_tuple, ver_str = _parse_version(_EXT) if _EXT else ((0, 0, 0), "unknown")
    backend = "rust" if _PROBED else "python"
    return ProbeResult(available=_PROBED, ext=_EXT, version_str=ver_str, version_tuple=ver_tuple, backend=backend)


def force_python() -> ProbeResult:
    """Force Python fallbacks (HLEDAC_FORCE_PYTHON=1)."""
    global _PROBED, _EXT
    _PROBED = False
    _EXT = None
    logger.debug("[RustProbe] Python fallback FORCED via HLEDAC_FORCE_PYTHON=1")
    return ProbeResult(available=False, ext=None, version_str="unknown", version_tuple=(0, 0, 0), backend="python")


def force_rust() -> ProbeResult:
    """Force Rust path, warn if unavailable (HLEDAC_FORCE_RUST=1)."""
    global _PROBED, _EXT
    result = probe()
    if not result.available:
        logger.warning("[RustProbe] HLEDAC_FORCE_RUST=1 but Rust extension unavailable")
    _PROBED = result.available
    _EXT = result.ext if result.available else None
    return result


def reset() -> None:
    """Reset probe cache — for testing only."""
    global _PROBED, _EXT
    _PROBED = None
    _EXT = None
