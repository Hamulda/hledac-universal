# _prober.py — one-time Rust extension availability probe
"""
Probes hledac_rust_extensions exactly once, returns a frozen result.
Never raises — all exceptions caught and surfaced as availability=False.

ISSUE-040: ABI version checking
    - Rust extension exports __abi_version__ (u32) via lib.rs
    - Python checks at import time: if ABI mismatches, fail-fast with clear error
    - ABI version bumps on ANY backward-incompatible API change

ISSUE-2 (P0): Stale binary detection + fail-closed ABI gate
    - probe() now enforces abi_version >= _RUST_MIN_ABI_VERSION (fail-closed)
    - Capability probe validates reference symbol presence after import
    - .so mtime is tracked and logged if the binary changed since last probe
"""

import logging
import os
import sys
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

# ISSUE-2: Reference symbols — hot-path functions that MUST be present in a
# compatible binary.  Used by the capability probe to detect partial builds.
_REFERENCE_SYMBOLS: list[str] = [
    # IOC extraction (primary hot-path)
    "batch_ioc_extract_unified",
    "batch_ioc_extract_unified_python",
    # xxHash (primary hot-path, zero-copy)
    "batch_xxh3_64_bytes",
    "batch_content_hash_hex",
    # URL operations
    "batch_extract_structured_entities_py",
    "UrlClassifyCachePy",
    # Content hasher
    "batch_xxh3_64_hex",
    # IP parsing
    "parse_ip_fast",
    "batch_ip_classify",
    # Bloom filter (when feature is enabled)
    "bloom_check_batch",
    "BloomFilter",
    # Query terms
    "scan_query_context",
    "extract_payload_context",
]

# ISSUE-2: Minimum fraction of reference symbols that must be present.
# Below this threshold we mark the binary as incompatible and log a CRITICAL warning.
_CAPABILITY_THRESHOLD: float = 0.70

# Cached probe result — set once at module load
_PROBED: bool | None = None
_EXT: object | None = None
# ISSUE-2: mtime of the .so file at the time of the last successful probe.
# If the file is newer on a subsequent call, we log a warning once.
_SO_MTIME: float | None = None
# ISSUE-A: Tracks whether we have already warned about .so mtime change this session.
# Prevents repeated warnings on every cached call when binary was rebuilt.
_SO_MTIME_WARNED: bool = False
# ISSUE-2: Cached capability score from the last successful probe.
# Avoids re-running hasattr checks on every cached probe() call.
_CAP_SCORE: float = 0.0
# ISSUE-2: Cached .so mtime from the last successful probe.
_CAP_SO_MTIME: float | None = None


class ProbeResult(msgspec.Struct, frozen=True):
    """Frozen result of the Rust extension probe."""

    available: bool
    ext: object | None
    version_str: str
    version_tuple: tuple[int, int, int]
    abi_version: int  # ISSUE-040: ABI version from Rust extension
    backend: str  # "rust" | "python"
    capability_score: float = 0.0  # ISSUE-2: fraction of reference symbols present (0.0-1.0)
    so_mtime: float | None = None  # ISSUE-2: mtime of the loaded .so at probe time

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


def _check_capability(ext: object | None) -> float:
    """ISSUE-2: Score how many reference symbols are present in the extension.

    Returns a fraction 0.0-1.0.  Below _CAPABILITY_THRESHOLD the binary is
    considered broken / partial-build and should not be used.
    """
    if ext is None:
        return 0.0
    present = sum(1 for s in _REFERENCE_SYMBOLS if hasattr(ext, s))
    return present / len(_REFERENCE_SYMBOLS) if _REFERENCE_SYMBOLS else 0.0


def _so_mtime() -> float | None:
    """ISSUE-2: Return the mtime of the loaded .so file, or None."""
    try:
        ext = sys.modules.get("hledac_rust_extensions")
        if ext is not None and hasattr(ext, "__file__") and ext.__file__:
            path = ext.__file__
            # For editable installs the __file__ is the __init__.py; resolve to .so
            if path.endswith("__init__.py") or os.path.isdir(path):
                path = os.path.join(os.path.dirname(path), "hledac_rust_extensions.abi3.so")
            if os.path.isfile(path):
                return os.path.getmtime(path)
    except Exception:
        pass
    return None


def probe() -> ProbeResult:
    """
    One-time probe of hledac_rust_extensions.
    Stores result in module globals so repeated calls are free.

    ISSUE-2 P0 fixes:
    - ABI version is now enforced in the normal probe() path (fail-closed).
      Before: only force_rust() checked ABI; probe() returned available=True
      with abi_version=0.  Now: if ABI < _RUST_MIN_ABI_VERSION the extension
      is marked unavailable even in the normal (non-forced) path.
    - Capability probe: validates that ≥70 % of reference symbols are present.
      Missing symbols > 30 % → CRITICAL log, available=False.
    - .so mtime is captured on successful probe; warning fires only once per
      session when the .so changes between probes (not on every cached call).
    """
    global _PROBED, _EXT, _SO_MTIME, _SO_MTIME_WARNED, _CAP_SCORE, _CAP_SO_MTIME

    if _PROBED is not None:
        # Already probed — return cached values from the initial probe.
        # Re-computing cap_score/so_mtime would be functionally correct but
        # wasteful; we store them at probe time instead.
        ver_tuple, ver_str = _parse_version(_EXT) if _EXT else ((0, 0, 0), "unknown")
        abi_ver = _parse_abi_version(_EXT) if _EXT else 0
        backend = "rust" if _PROBED else "python"
        return ProbeResult(
            available=_PROBED, ext=_EXT, version_str=ver_str,
            version_tuple=ver_tuple, abi_version=abi_ver, backend=backend,
            capability_score=_CAP_SCORE, so_mtime=_CAP_SO_MTIME,
        )

    # First call — do the probe
    _PROBED = False
    _EXT = None
    try:
        import hledac_rust_extensions as ext  # type: ignore[no-redef]

        ver_tuple, ver_str = _parse_version(ext)

        if ver_tuple < _RUST_MIN_VERSION:
            logger.debug(
                f"[RustProbe] hledac_rust_extensions {ver_tuple} < "
                f"required {_RUST_MIN_VERSION}; Python fallbacks enabled"
            )
        else:
            abi_ver = _parse_abi_version(ext)

            # ISSUE-2 P0: fail-closed ABI gate — ABI must be known and sufficient
            if abi_ver < _RUST_MIN_ABI_VERSION:
                logger.warning(
                    f"[RustProbe] hledac_rust_extensions ABI version {abi_ver} < "
                    f"required {_RUST_MIN_ABI_VERSION}; binary is stale (built before "
                    f"__abi_version__ was added). Run: cd rust_extensions && maturin develop. "
                    f"Falling back to Python."
                )
            else:
                # ABI OK — do capability probe
                cap_score = _check_capability(ext)
                current_mtime = _so_mtime()

                # ISSUE-A: warn about .so mtime change only ONCE per session
                # (not on every cached probe() call after a rebuild)
                if _SO_MTIME is not None and current_mtime is not None and current_mtime > _SO_MTIME:
                    if not _SO_MTIME_WARNED:
                        logger.warning(
                            f"[RustProbe] .so mtime changed since last probe "
                            f"(cached={_SO_MTIME}, current={current_mtime}); "
                            f"a rebuild may be needed"
                        )
                        _SO_MTIME_WARNED = True

                if cap_score < _CAPABILITY_THRESHOLD:
                    # capability_score is low — binary is broken / partial build
                    logger.critical(
                        f"[RustProbe] capability score {cap_score:.0%} < {_CAPABILITY_THRESHOLD:.0%} "
                        f"threshold; {len(_REFERENCE_SYMBOLS)} reference symbols checked, "
                        f"{int(cap_score * len(_REFERENCE_SYMBOLS))} present. "
                        f"Binary is broken or partial build. Falling back to Python."
                    )
                else:
                    _PROBED = True
                    _EXT = ext
                    _SO_MTIME = current_mtime
                    _CAP_SCORE = cap_score
                    _CAP_SO_MTIME = current_mtime
                    logger.debug(
                        f"[RustProbe] hledac_rust_extensions loaded "
                        f"(version {ver_str}, ABI {abi_ver}, capability {cap_score:.0%})"
                    )
    except Exception as e:
        # Catch ALL: ImportError, OSError, AttributeError, etc.
        logger.debug(f"[RustProbe] hledac_rust_extensions unavailable: {e}")

    ver_tuple, ver_str = _parse_version(_EXT) if _EXT else ((0, 0, 0), "unknown")
    abi_ver = _parse_abi_version(_EXT) if _EXT else 0
    cap_score = _check_capability(_EXT)
    current_mtime = _so_mtime() if _EXT else None
    backend = "rust" if _PROBED else "python"
    return ProbeResult(
        available=_PROBED, ext=_EXT, version_str=ver_str,
        version_tuple=ver_tuple, abi_version=abi_ver, backend=backend,
        capability_score=cap_score, so_mtime=current_mtime,
    )


def force_python() -> ProbeResult:
    """Force Python fallbacks (HLEDAC_FORCE_PYTHON=1)."""
    global _PROBED, _EXT, _SO_MTIME, _SO_MTIME_WARNED, _CAP_SCORE, _CAP_SO_MTIME
    _PROBED = False
    _EXT = None
    _SO_MTIME = None
    _SO_MTIME_WARNED = False
    _CAP_SCORE = 0.0
    _CAP_SO_MTIME = None
    logger.debug("[RustProbe] Python fallback FORCED via HLEDAC_FORCE_PYTHON=1")
    return ProbeResult(
        available=False, ext=None, version_str="unknown",
        version_tuple=(0, 0, 0), abi_version=0, backend="python",
        capability_score=0.0, so_mtime=None,
    )


def force_rust() -> ProbeResult:
    """Force Rust path, warn if unavailable or incompatible (HLEDAC_FORCE_RUST=1)."""
    global _PROBED, _EXT, _SO_MTIME, _SO_MTIME_WARNED, _CAP_SCORE, _CAP_SO_MTIME
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
        _SO_MTIME = None
        _SO_MTIME_WARNED = False
        _CAP_SCORE = 0.0
        _CAP_SO_MTIME = None
        return ProbeResult(
            available=False, ext=None, version_str=result.version_str,
            version_tuple=result.version_tuple, abi_version=result.abi_version,
            backend="python", capability_score=0.0, so_mtime=None,
        )
    _PROBED = result.available
    _EXT = result.ext if result.available else None
    _SO_MTIME = result.so_mtime if result.available else None
    _SO_MTIME_WARNED = False
    _CAP_SCORE = result.capability_score
    _CAP_SO_MTIME = result.so_mtime
    return result


def reset() -> None:
    """Reset probe cache — for testing only."""
    global _PROBED, _EXT, _SO_MTIME, _SO_MTIME_WARNED, _CAP_SCORE, _CAP_SO_MTIME
    _PROBED = None
    _EXT = None
    _SO_MTIME = None
    _SO_MTIME_WARNED = False
    _CAP_SCORE = 0.0
    _CAP_SO_MTIME = None
