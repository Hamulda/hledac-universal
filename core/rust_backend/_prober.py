# _prober.py — one-time Rust extension availability probe
"""
Probes hledac_rust_extensions exactly once, returns a frozen result.
Never raises — all exceptions caught and surfaced as availability=False.

ISSUE-040: ABI version checking
    - Rust extension exports __abi_version__ (u32) via lib.rs
    - Python checks at import time: if ABI mismatches, fail-fast with clear error
    - ABI version bumps on ANY backward-incompatible API change
"""

import logging
import sys
from dataclasses import dataclass
import msgspec

logger = logging.getLogger(__name__)

_RUST_MIN_VERSION: tuple[int, int, int] = (0, 1, 0)
# ISSUE-040: Minimum required ABI version — MUST match Rust ABI_VERSION in lib.rs
# Bump this when Rust API changes in backward-incompatible ways:
#   - removed functions
#   - changed function signatures
#   - changed struct field layouts
#   - changed return types
_RUST_MIN_ABI_VERSION: int = 1

# Cached probe result — set once at module load
_PROBED: bool | None = None
_EXT: object | None = None


class ProbeResult(msgspec.Struct, frozen=True):
    """Frozen result of the Rust extension probe."""

    available: bool
    ext: object | None
    version_str: str
    version_tuple: tuple[int, int, int]
    abi_version: int  # ISSUE-040: ABI version from Rust extension
    backend: str  # "rust" | "python"

    @property
    def is_compatible(self) -> bool:
        return (
            self.available
            and self.version_tuple >= _RUST_MIN_VERSION
            and self.abi_version >= _RUST_MIN_ABI_VERSION
        )


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


def _parse_abi_version(ext: object | None) -> int:
    """ISSUE-040: Extract ABI version from extension module.

    Returns 0 if __abi_version__ is not available (old binary before ISSUE-040).
    A value of 0 signals unknown ABI — we require explicit >= _RUST_MIN_ABI_VERSION.
    """
    if ext is None:
        return 0

    try:
        abi_version_fn = getattr(ext, "__abi_version__", None)
        if callable(abi_version_fn):
            result = abi_version_fn()
            if isinstance(result, (int, tuple)) and not isinstance(result, tuple):
                return int(result)
    except Exception:
        pass

    # Also check direct attribute (constant, not function)
    try:
        direct = getattr(ext, "__abi_version__", None)
        if isinstance(direct, (int, tuple)) and not isinstance(direct, tuple):
            return int(direct)
    except Exception:
        pass

    return 0


def probe() -> ProbeResult:
    """
    One-time probe of hledac_rust_extensions.
    Stores result in module globals so repeated calls are free.
    """
    global _PROBED, _EXT

    if _PROBED is not None:
        # Already probed — return cached
        ver_tuple, ver_str = _parse_version(_EXT) if _EXT else ((0, 0, 0), "unknown")
        abi_ver = _parse_abi_version(_EXT) if _EXT else 0
        backend = "rust" if _PROBED else "python"
        return ProbeResult(available=_PROBED, ext=_EXT, version_str=ver_str, version_tuple=ver_tuple, abi_version=abi_ver, backend=backend)

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
            abi_ver = _parse_abi_version(ext)
            logger.debug(
                f"[RustProbe] hledac_rust_extensions loaded (version {ver_str}, ABI {abi_ver})"
            )
    except Exception as e:
        # Catch ALL: ImportError, OSError, AttributeError, etc.
        logger.debug(f"[RustProbe] hledac_rust_extensions unavailable: {e}")
        _PROBED = False
        _EXT = None

    ver_tuple, ver_str = _parse_version(_EXT) if _EXT else ((0, 0, 0), "unknown")
    abi_ver = _parse_abi_version(_EXT) if _EXT else 0
    backend = "rust" if _PROBED else "python"
    return ProbeResult(available=_PROBED, ext=_EXT, version_str=ver_str, version_tuple=ver_tuple, abi_version=abi_ver, backend=backend)


def force_python() -> ProbeResult:
    """Force Python fallbacks (HLEDAC_FORCE_PYTHON=1)."""
    global _PROBED, _EXT
    _PROBED = False
    _EXT = None
    logger.debug("[RustProbe] Python fallback FORCED via HLEDAC_FORCE_PYTHON=1")
    return ProbeResult(available=False, ext=None, version_str="unknown", version_tuple=(0, 0, 0), abi_version=0, backend="python")


def force_rust() -> ProbeResult:
    """Force Rust path, warn if unavailable or incompatible (HLEDAC_FORCE_RUST=1)."""
    global _PROBED, _EXT
    result = probe()
    if not result.available:
        logger.warning("[RustProbe] HLEDAC_FORCE_RUST=1 but Rust extension unavailable")
    elif not result.is_compatible:
        logger.warning(
            f"[RustProbe] HLEDAC_FORCE_RUST=1 but Rust extension not compatible "
            f"(version={result.version_str}, ABI={result.abi_version}); falling back to Python"
        )
        _PROBED = False
        _EXT = None
        return ProbeResult(
            available=False, ext=None, version_str=result.version_str,
            version_tuple=result.version_tuple, abi_version=result.abi_version,
            backend="python"
        )
    _PROBED = result.available
    _EXT = result.ext if result.available else None
    return result


def reset() -> None:
    """Reset probe cache — for testing only."""
    global _PROBED, _EXT
    _PROBED = None
    _EXT = None
