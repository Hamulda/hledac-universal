# _prober.py — one-time Rust extension availability probe
"""
Probes hledac_rust_extensions exactly once, returns a frozen result.
Never raises — all exceptions caught and surfaced as availability=False.

ISSUE-040: ABI version checking (semver-like tuple)
    - Rust extension exports __abi_version__ as (major, minor, patch) tuple via lib.rs
    - Python checks at import time: if ABI major version mismatches, fail-fast with clear error
    - ABI version bumps on ANY backward-incompatible API change
    - Graceful degradation: minor/patch bumps are backward-compatible

ISSUE-2 (P0): Stale binary detection + fail-closed ABI gate
    - probe() now enforces abi_version >= _RUST_MIN_ABI_VERSION (fail-closed)
    - Capability probe validates reference symbol presence after import
    - .so mtime is tracked and logged if the binary changed since last probe

ISSUE-3.3: Graceful degradation for ABI major version mismatch
    - If __abi_version__[0] (major) > required_major: ImportError with rebuild instruction
    - Minor/patch mismatches are backward-compatible (log warning only)
    - Added __py_version__ and __apple_target__ detection
"""

import logging
import os
import platform
import sys
import msgspec

logger = logging.getLogger(__name__)

_RUST_MIN_VERSION: tuple[int, int, int] = (0, 1, 0)
# ISSUE-040: Minimum required ABI version tuple — MUST match Rust ABI_VERSION in lib.rs
# Format: (major, minor, patch)
# Bump rules:
#   - major: breaking change — old callers MUST update (fail-closed)
#   - minor: new optional API — backward-compatible (log warning)
#   - patch: bug fixes — always compatible
_RUST_MIN_ABI_VERSION: tuple[int, int, int] = (1, 0, 0)
# ISSUE-3.3: Minimum required ABI major version for graceful degradation
# If extension's major > this, it requires a rebuild
_RUST_MIN_ABI_MAJOR: int = 1

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


class ProbeResult(msgspec.Struct, frozen=True, gc=False):
    """Frozen result of the Rust extension probe."""

    available: bool
    ext: object | None
    version_str: str
    version_tuple: tuple[int, int, int]
    abi_version: tuple[int, int, int]  # ISSUE-040: ABI version tuple from Rust extension
    abi_major: int  # ISSUE-3.3: major version for graceful degradation
    backend: str  # "rust" | "python"
    capability_score: float = 0.0  # ISSUE-2: fraction of reference symbols present (0.0-1.0)
    so_mtime: float | None = None  # ISSUE-2: mtime of the loaded .so at probe time
    py_version: tuple[int, int, int] | None = None  # ISSUE-3.3: Python version compiled-for
    apple_target: str | None = None  # ISSUE-3.3: Apple target triple

    @property
    def is_compatible(self) -> bool:
        return (
            self.available
            and self.version_tuple >= _RUST_MIN_VERSION
            and self.abi_version >= _RUST_MIN_ABI_VERSION
        )

    @property
    def abi_major_mismatch(self) -> bool:
        """ISSUE-3.3: True if extension's ABI major > required major (needs rebuild)."""
        return self.abi_major > _RUST_MIN_ABI_VERSION[0]


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


def _parse_abi_version(ext: object | None) -> tuple[int, int, int]:
    """ISSUE-040: Extract ABI version tuple from extension module.

    Returns (0, 0, 0) if __abi_version__ is not available (old binary before ISSUE-040).
    A tuple of (0, 0, 0) signals unknown ABI — we require explicit >= (1, 0, 0).
    """
    if ext is None:
        return (0, 0, 0)

    try:
        abi_version_fn = getattr(ext, "__abi_version__", None)
        if callable(abi_version_fn):
            result = abi_version_fn()
            if isinstance(result, tuple) and len(result) >= 2:
                return (int(result[0]), int(result[1]), int(result[2]) if len(result) > 2 else 0)
            elif isinstance(result, int):
                # Legacy flat u32 — convert to tuple
                return (result, 0, 0)
    except Exception:
        pass

    # Also check direct attribute (constant, not function)
    try:
        direct = getattr(ext, "__abi_version__", None)
        if isinstance(direct, tuple) and len(direct) >= 2:
            return (int(direct[0]), int(direct[1]), int(direct[2]) if len(direct) > 2 else 0)
        elif isinstance(direct, int):
            # Legacy flat u32 — convert to tuple
            return (direct, 0, 0)
    except Exception:
        pass

    return (0, 0, 0)


def _parse_py_version(ext: object | None) -> tuple[int, int, int] | None:
    """ISSUE-3.3: Extract Python version compiled-for from extension module.

    Returns None if __py_version__ is not available (extension built without py-version feature).
    """
    if ext is None:
        return None
    try:
        py_version_fn = getattr(ext, "__py_version__", None)
        if callable(py_version_fn):
            result = py_version_fn()
            if isinstance(result, tuple) and len(result) >= 2:
                return (int(result[0]), int(result[1]), int(result[2]) if len(result) > 2 else 0)
    except Exception:
        pass
    return None


def _parse_apple_target(ext: object | None) -> str | None:
    """ISSUE-3.3: Extract Apple target triple from extension module.

    Returns None if __apple_target__ is not available.
    """
    if ext is None:
        return None
    try:
        target_fn = getattr(ext, "__apple_target__", None)
        if callable(target_fn):
            result = target_fn()
            if isinstance(result, str):
                return result
    except Exception:
        pass
    return None


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


def _abi_tuple_str(abi: tuple[int, int, int]) -> str:
    """Format ABI tuple as string for logging."""
    return f"({abi[0]}, {abi[1]}, {abi[2]})"


def _check_apple_target_mismatch(ext_apple_target: str | None) -> bool:
    """ISSUE-3.3: Check if Apple target matches current platform.

    Returns True if there is a mismatch (e.g., extension was built for x86_64 but we run on M1).
    """
    if ext_apple_target is None:
        return False
    # aarch64-apple-darwin = M1/M2/M3, arm64-apple-darwin = iOS simulator, x86_64-apple-darwin = Intel
    is_m1 = platform.processor() == "arm" or "aarch64" in platform.machine()
    is_extension_intel = "x86_64" in ext_apple_target and is_m1
    is_extension_arm = ("aarch64" in ext_apple_target or "arm64" in ext_apple_target) and not is_m1
    return is_extension_intel or is_extension_arm


def probe() -> ProbeResult:
    """
    One-time probe of hledac_rust_extensions.
    Stores result in module globals so repeated calls are free.

    ISSUE-040 + ISSUE-3.3: Semver-like ABI version tuple
    - ABI version is now a (major, minor, patch) tuple
    - major mismatch (extension > required) = fail-closed with ImportError hint
    - minor/patch mismatch = backward-compatible, log warning only

    ISSUE-2 P0 fixes:
    - ABI version is now enforced in the normal probe() path (fail-closed).
    - Capability probe validates ≥70 % reference symbols present.
    - .so mtime tracked and logged if binary changed since last probe.
    """
    global _PROBED, _EXT, _SO_MTIME, _SO_MTIME_WARNED, _CAP_SCORE, _CAP_SO_MTIME

    if _PROBED is not None:
        # Already probed — return cached values from the initial probe.
        ver_tuple, ver_str = _parse_version(_EXT) if _EXT else ((0, 0, 0), "unknown")
        abi_ver = _parse_abi_version(_EXT) if _EXT else (0, 0, 0)
        py_ver = _parse_py_version(_EXT) if _EXT else None
        apple_tgt = _parse_apple_target(_EXT) if _EXT else None
        backend = "rust" if _PROBED else "python"
        return ProbeResult(
            available=_PROBED, ext=_EXT, version_str=ver_str,
            version_tuple=ver_tuple, abi_version=abi_ver,
            abi_major=abi_ver[0], backend=backend,
            capability_score=_CAP_SCORE, so_mtime=_CAP_SO_MTIME,
            py_version=py_ver, apple_target=apple_tgt,
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

            # ISSUE-3.3: graceful degradation — major version mismatch = fail-closed
            if abi_ver[0] > _RUST_MIN_ABI_VERSION[0]:
                logger.error(
                    f"[RustProbe] ABI major version mismatch: extension has ABI {abi_ver[0]}.x.x "
                    f"but Python requires ABI {_RUST_MIN_ABI_VERSION[0]}.x.x. "
                    f"Extension was built with a newer ABI that requires a rebuild. "
                    f"Run: cd rust_extensions && maturin develop --release. "
                    f"Falling back to Python."
                )
                # Do NOT fall through — incompatible extension stays unavailable
            # ISSUE-2 P0: fail-closed ABI gate — ABI must be known and sufficient
            elif abi_ver < _RUST_MIN_ABI_VERSION:
                logger.warning(
                    f"[RustProbe] hledac_rust_extensions ABI version {_abi_tuple_str(abi_ver)} < "
                    f"required {_abi_tuple_str(_RUST_MIN_ABI_VERSION)}; binary is stale. "
                    f"Run: cd rust_extensions && maturin develop. "
                    f"Falling back to Python."
                )
            else:
                # ABI OK — do capability probe
                cap_score = _check_capability(ext)
                current_mtime = _so_mtime()
                py_ver = _parse_py_version(ext)
                apple_tgt = _parse_apple_target(ext)

                # ISSUE-3.3: Apple target mismatch = hard fail (M1 vs Intel is binary incompatible)
                if _check_apple_target_mismatch(apple_tgt):
                    logger.error(
                        f"[RustProbe] Apple target mismatch: extension built for "
                        f"{apple_tgt}, running on {platform.platform()}. "
                        f"Rebuild required for this architecture. "
                        f"Run: cd rust_extensions && maturin develop --release."
                    )
                    # Do NOT fall through — incompatible architecture stays unavailable
                else:
                    # ISSUE-3.3: Python version mismatch check (warning only — ABI stable)
                    if py_ver is not None:
                        py_version_info = sys.version_info[:3]
                        if py_ver != py_version_info:
                            logger.warning(
                                f"[RustProbe] Python version mismatch: extension built for "
                                f"{py_ver}, running under {py_version_info}. "
                                f"May cause ABI issues. Rebuild recommended."
                            )

                    # ISSUE-A: warn about .so mtime change only ONCE per session
                    if _SO_MTIME is not None and current_mtime is not None and current_mtime > _SO_MTIME:
                        if not _SO_MTIME_WARNED:
                            logger.warning(
                                f"[RustProbe] .so mtime changed since last probe "
                                f"(cached={_SO_MTIME}, current={current_mtime}); "
                                f"a rebuild may be needed"
                            )
                            _SO_MTIME_WARNED = True

                    if cap_score < _CAPABILITY_THRESHOLD:
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
                            f"(version {ver_str}, ABI {_abi_tuple_str(abi_ver)}, "
                            f"capability {cap_score:.0%}, apple_target={apple_tgt})"
                        )
    except Exception as e:
        # Catch ALL: ImportError, OSError, AttributeError, etc.
        logger.debug(f"[RustProbe] hledac_rust_extensions unavailable: {e}")

    ver_tuple, ver_str = _parse_version(_EXT) if _EXT else ((0, 0, 0), "unknown")
    abi_ver = _parse_abi_version(_EXT) if _EXT else (0, 0, 0)
    py_ver = _parse_py_version(_EXT) if _EXT else None
    apple_tgt = _parse_apple_target(_EXT) if _EXT else None
    cap_score = _check_capability(_EXT)
    current_mtime = _so_mtime() if _EXT else None
    backend = "rust" if _PROBED else "python"
    return ProbeResult(
        available=_PROBED, ext=_EXT, version_str=ver_str,
        version_tuple=ver_tuple, abi_version=abi_ver,
        abi_major=abi_ver[0], backend=backend,
        capability_score=cap_score, so_mtime=current_mtime,
        py_version=py_ver, apple_target=apple_tgt,
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
        version_tuple=(0, 0, 0), abi_version=(0, 0, 0),
        abi_major=0, backend="python",
        capability_score=0.0, so_mtime=None,
        py_version=None, apple_target=None,
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
            f"(version={result.version_str}, ABI={_abi_tuple_str(result.abi_version)}); "
            f"falling back to Python"
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
            abi_major=result.abi_major, backend="python",
            capability_score=0.0, so_mtime=None,
            py_version=None, apple_target=None,
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
