# _prober.py — one-time Rust extension availability probe
"""
Probes hledac_rust_extensions exactly once, returns a frozen result.

ISSUE-11: Staleness Detection (Fail-Closed)
    - BUILD_MANIFEST.json generated at build time contains SHA256 of all source files
    - At import, verifies BUILD_MANIFEST hash against current source
    - If stale, raises RustExtensionStale (fail-closed) instead of silent degradation
    - Configuration: HLEDAC_RUST_STALE_MODE=soft|hard (default: hard)
    
ISSUE-2 (P0): Stale binary detection + fail-closed ABI gate
    - probe() now enforces abi_version >= _RUST_MIN_ABI_VERSION (fail-closed)
    - Capability probe validates reference symbol presence after import
    - .so mtime is tracked and logged if the binary changed since last probe


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

ISSUE-01 (CRITICAL): Stale Rust extension — source freshness gate
    - Compares .so mtime against source file mtimes (rust_extensions/src/**/*.rs, *.toml)
    - Uses content hash manifest (_ffi_type_manifest.json) for fast verification
    - Fail-closed: if any source file is newer than .so, extension is marked unavailable
      with clear rebuild instruction instead of silently degrading to Python
    - M1 8GB optimizations: CARGO_PROFILE_RELEASE_LTO=false thin-LTO codegen-units=16
    - Source hash stored in __source_hash__() for cross-session freshness verification
"""

import importlib
import json as _json
import logging
import os
import platform
import sys
import msgspec
from pathlib import Path as _Path

# ISSUE-11: Import Rust extension exceptions for fail-closed behavior
from ._exceptions import RustExtensionStale
from _core._util import aclose

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
# ISSUE-5.1: Cached Cargo feature flags from the last successful probe.
_CAP_FEATURES: frozenset[str] = frozenset()

# ISSUE-01: Source freshness tracking
# Path to Rust source directory (relative to project root)
# Use absolute path based on this file's location to work from any cwd
_PROJECT_ROOT = str(_Path(__file__).parent.parent.parent)
_RUST_SRC_DIR = _Path(_PROJECT_ROOT) / "rust_extensions" / "src"
# Path to Cargo.toml for manifest tracking
_RUST_MANIFEST = _Path(_PROJECT_ROOT) / "rust_extensions" / "Cargo.toml"
# Path to content hash manifest (generated at build time)
_FFI_MANIFEST_PATH = _Path(_PROJECT_ROOT) / "rust_extensions" / "_ffi_type_manifest.json"

# ISSUE-11: BUILD_MANIFEST path - generated at build time, stored next to .so
# This is the authoritative source of truth for staleness detection
_BUILD_MANIFEST_PATH = _Path(_PROJECT_ROOT) / "rust_extensions" / "BUILD_MANIFEST.json"

# ISSUE-11: Staleness enforcement mode
# - "hard": Raise RustExtensionStale exception (fail-closed, recommended for production)
# - "soft": Log error and fall back to Python (for development)
_RUST_STALE_MODE: str = os.environ.get("HLEDAC_RUST_STALE_MODE", "hard").lower()
if _RUST_STALE_MODE not in ("hard", "soft"):
    logger.warning(
        f"[RustProbe] Invalid HLEDAC_RUST_STALE_MODE={_RUST_STALE_MODE}, "
        f"using default 'hard'"
    )
    _RUST_STALE_MODE = "hard"

# Cached source hash from last successful probe
_CAP_SOURCE_HASH: str | None = None
# ISSUE-11: Cached BUILD_MANIFEST hash (set at build time)
_CAP_BUILD_MANIFEST_HASH: str | None = None
# ISSUE-01: Flag to track if we already warned about stale source this session
_SOURCE_STALE_WARNED: bool = False


# ============================================================================
# ISSUE-01: Source Freshness Gate
# ============================================================================

def _get_rust_source_mtime() -> float | None:
    """
    Get the most recent mtime of any Rust source file in rust_extensions/src/.
    Returns None if the source directory doesn't exist.
    """
    try:
        src_dir = _Path(_RUST_SRC_DIR)
        if not src_dir.exists():
            return None
        
        # Find all .rs and .toml files
        max_mtime: float = 0.0
        for pattern in ["**/*.rs", "**/*.toml"]:
            for path in src_dir.glob(pattern):
                if path.is_file():
                    try:
                        mtime = path.stat().st_mtime
                        if mtime > max_mtime:
                            max_mtime = mtime
                    except OSError:
                        continue
        
        return max_mtime if max_mtime > 0 else None
    except Exception:
        return None


def _compute_source_content_hash() -> str | None:
    """
    Compute a BLAKE2B hash of all Rust source files.

    IMPORTANT: This MUST match _compute_source_hash() in build_manifest.py.
    Uses two-level hashing:
    1. Per-file: blake2b(path + size + sample) = file_hash
    2. Overall: blake2b(concatenated file_hashes)

    Algorithm (must match build_manifest.py):
    - Collects files from rust_extensions/src/**/*.rs and rust_extensions/Cargo.toml
    - Sorts files by path for deterministic ordering
    - For each file: blake2b(relative_path + size + first_4KB + last_4KB)
    - Final hash: BLAKE2B-256 of all file_hash bytes concatenated

    Returns a hex string of the hash, or None if source not found.
    """
    try:
        import hashlib

        src_dir = _Path(_RUST_SRC_DIR)
        manifest_path = _Path(_RUST_MANIFEST)

        if not src_dir.exists():
            return None

        # Collect all source files (matches build_manifest.py)
        file_paths: list[_Path] = []
        for ext in ("*.rs", "*.toml"):
            for path in sorted(src_dir.glob(ext)):
                if path.is_file():
                    file_paths.append(path)

        # Also include Cargo.toml (same as build_manifest.py)
        if manifest_path.exists() and manifest_path.parent == src_dir.parent:
            file_paths.append(manifest_path)

        # Sort for deterministic ordering
        file_paths.sort(key=str)

        if not file_paths:
            return None

        # Two-level hashing (matches build_manifest.py exactly)
        overall_hasher = hashlib.blake2b(digest_size=32)
        repo_root = src_dir.parent  # rust_extensions/

        for path in file_paths:
            relative_path = str(path.relative_to(repo_root))
            size = path.stat().st_size

            # Per-file hash: blake2b(path + size + sample)
            file_hasher = hashlib.blake2b(digest_size=32)
            file_hasher.update(relative_path.encode())
            file_hasher.update(size.to_bytes(8, "little"))

            try:
                content = path.read_bytes()
                sample_size = min(4096, len(content))
                file_hasher.update(content[:sample_size])
                if len(content) > 8192:
                    file_hasher.update(content[-4096:])
            except OSError:
                continue

            file_hash = file_hasher.hexdigest()

            # Overall hash: blake2b of all file_hash bytes
            overall_hasher.update(file_hash.encode())

        return overall_hasher.hexdigest()
    except Exception:
        return None


def _load_ffi_manifest() -> dict | None:
    """
    Load the FFI type manifest if it exists.
    Returns the manifest dict or None.
    """
    try:
        manifest_path = _Path(_FFI_MANIFEST_PATH)
        if manifest_path.exists():
            with open(manifest_path, "r") as f:
                return _json.loads(f.read())
    except Exception:
        pass
    return None


# ============================================================================
# ISSUE-11: BUILD_MANIFEST — Build-time source hash for staleness detection
# ============================================================================

def _load_build_manifest() -> dict | None:
    """
    Load the BUILD_MANIFEST if it exists.

    BUILD_MANIFEST is generated at build time by build_manifest.py and contains:
    - SHA256 hash of all source files
    - Build timestamp
    - Build command used

    Returns the manifest dict or None if not found.
    """
    try:
        manifest_path = _Path(_BUILD_MANIFEST_PATH)
        if manifest_path.exists():
            with open(manifest_path, "r") as f:
                return _json.loads(f.read())
    except Exception:
        pass
    return None


def _check_build_manifest_staleness() -> tuple[bool, str, str | None, str | None]:
    """
    ISSUE-11: Check if the source is stale compared to BUILD_MANIFEST.

    This compares the hash stored in BUILD_MANIFEST (generated at build time)
    with the current source hash. If they differ, the source has been modified
    since the build.

    Algorithm validation:
    - Verifies BUILD_MANIFEST uses blake2b-256 (matches runtime)
    - Provides clear error if hash algorithms don't match

    Returns:
        tuple[bool, str, str | None, str | None]:
            - is_stale: True if source is newer than binary
            - reason: Human-readable explanation
            - manifest_hash: Hash from BUILD_MANIFEST
            - current_hash: Hash from current source
    """
    manifest = _load_build_manifest()

    if manifest is None:
        # No BUILD_MANIFEST - cannot verify staleness
        return False, "No BUILD_MANIFEST found (run build first)", None, None

    # ISSUE-11: Validate hash algorithm matches
    algorithm = manifest.get("source_hash_algorithm")
    if algorithm and algorithm != "blake2b-256":
        logger.warning(
            f"[RustProbe] BUILD_MANIFEST uses {algorithm}, expected blake2b-256. "
            f"Hash comparison may be unreliable. Rebuild manifest with: "
            f"python rust_extensions/build_manifest.py"
    )

    manifest_hash = manifest.get("source_hash")
    if manifest_hash is None:
        return False, "BUILD_MANIFEST missing source_hash", None, None

    # Compute current source hash (BLAKE2B-256, must match BUILD_MANIFEST)
    current_hash = _compute_source_content_hash()
    if current_hash is None:
        return False, "Could not compute current source hash", manifest_hash, None

    # Compare hashes
    if manifest_hash != current_hash:
        return True, (
            f"Source files have been modified since build. "
            f"Build hash: {manifest_hash[:16]}..., Current: {current_hash[:16]}..."
        ), manifest_hash, current_hash

    return False, "Source matches BUILD_MANIFEST", manifest_hash, current_hash


def _check_source_freshness() -> tuple[bool, str]:
    """
    ISSUE-11: Check if the Rust source is stale compared to the binary.

    Three-tier staleness detection:
    1. BUILD_MANIFEST hash (primary, generated at build time) — ISSUe-11
    2. Direct mtime comparison (fallback if no BUILD_MANIFEST)
    3. FFI manifest hash (legacy fallback)

    Returns:
        tuple[bool, str]: (is_stale, reason)
            - is_stale: True if source is newer than binary
            - reason: Human-readable explanation
    """
    global _SOURCE_STALE_WARNED

    # ISSUE-11: Primary check - BUILD_MANIFEST hash comparison
    is_stale, reason, _, _ = _check_build_manifest_staleness()
    if is_stale:
        return True, reason

    # Check 2: Direct mtime comparison (fallback)
    so_mtime = _so_mtime()
    if so_mtime is not None:
        src_mtime = _get_rust_source_mtime()
        if src_mtime is not None and src_mtime > so_mtime:
            try:
                newer_count = 0
                src_dir = _Path(_RUST_SRC_DIR)
                for pattern in ["**/*.rs", "**/*.toml"]:
                    for path in src_dir.glob(pattern):
                        if path.is_file():
                            try:
                                if path.stat().st_mtime > so_mtime:
                                    newer_count += 1
                            except OSError:
                                continue

                return True, (
                    f"Source files ({newer_count}) are newer than .so binary "
                    f"(source mtime: {src_mtime:.1f}, so mtime: {so_mtime:.1f}). "
                    f"Rebuild required."
    )
            except Exception:
                return True, (
                    f"Source mtime ({src_mtime:.1f}) > .so mtime ({so_mtime:.1f}). "
                    f"Rebuild required."
    )

    # Check 3: FFI manifest hash (legacy fallback)
    manifest = _load_ffi_manifest()
    if manifest and "__source_hash__" in manifest:
        current_hash = _compute_source_content_hash()
        manifest_hash = manifest["__source_hash__"]

        if current_hash and current_hash != manifest_hash:
            return True, (
                f"Source content hash mismatch (expected: {manifest_hash[:16]}..., "
                f"current: {current_hash[:16]}...). "
                f"Source files have been modified. Rebuild required."
    )

    return False, "Source and binary are in sync"


def _get_staleness_details() -> tuple[str | None, str | None, str | None]:
    """
    ISSUE-11: Get detailed staleness information for exception messages.

    Returns:
        tuple[str | None, str | None, str | None]:
            - build_hash: Hash from BUILD_MANIFEST
            - current_hash: Current source hash
            - rebuild_cmd: Rebuild command from BUILD_MANIFEST
    """
    manifest = _load_build_manifest()
    if manifest:
        return (
            manifest.get("source_hash"),
            _compute_source_content_hash(),
            manifest.get("build_command"),
    )
    return None, _compute_source_content_hash(), None


def _get_rebuild_instruction() -> str:
    """
    Return the canonical rebuild instruction for the current platform.

    Prefers the command from BUILD_MANIFEST if available (which captures
    the exact command used for the original build).
    """
    # ISSUE-11: Try to get rebuild command from BUILD_MANIFEST first
    manifest = _load_build_manifest()
    if manifest and manifest.get("build_command"):
        cmd = manifest["build_command"]
        # Add maturin develop instruction if it was a maturin build
        if manifest.get("maturin_build", False):
            return (
                f"# Build command from BUILD_MANIFEST:\n"
                f"{cmd}\n\n"
                f"# Install into Python:\n"
                f"cd rust_extensions && maturin develop --release"
    )
        return f"# Build command:\n{cmd}"

    # Fall back to platform-specific default
    import platform

    is_m1 = platform.processor() == "arm" or "aarch64" in platform.machine()

    if is_m1:
        return (
            "# M1 8GB optimized build:\n"
            "CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 \\\n"
            "    cargo build --release --manifest-path rust_extensions/Cargo.toml\n\n"
            "# Then install into Python:\n"
            "cd rust_extensions && maturin develop --release"
    )
    else:
        return (
            "cd rust_extensions && maturin develop --release\n\n"
            "# Or for direct cargo build:\n"
            "cargo build --release --manifest-path rust_extensions/Cargo.toml"
    )


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
    features: frozenset[str] = frozenset()  # Enabled Cargo feature flags at build time
    # ISSUE-01: Source freshness fields
    source_stale: bool = False  # True if source is newer than binary
    source_stale_reason: str = ""  # Human-readable reason for staleness
    source_hash: str | None = None  # Hash of current source files
    rebuild_instruction: str = ""  # How to rebuild the extension

    @property
    def is_compatible(self) -> bool:
        return (
            self.available
            and self.version_tuple >= _RUST_MIN_VERSION
            and self.abi_version >= _RUST_MIN_ABI_VERSION
            and not self.source_stale  # ISSUE-01: source must be fresh
    )

    @property
    def abi_major_mismatch(self) -> bool:
        """ISSUE-3.3: True if extension's ABI major > required major (needs rebuild)."""
        return self.abi_major > _RUST_MIN_ABI_VERSION[0]

    @property
    def needs_rebuild(self) -> bool:
        """ISSUE-01: True if the binary needs to be rebuilt due to source changes."""
        return self.source_stale or self.abi_major_mismatch or self.abi_version < _RUST_MIN_ABI_VERSION

    def has_symbol(self, name: str) -> bool:
        """Return True if the given symbol is present in the Rust extension.

        Useful for feature-gated capability checks without exposing the raw module.
        """
        if not self.available or self.ext is None:
            return False
        return hasattr(self.ext, name)

    def has_feature(self, feature: str) -> bool:
        """Return True if the given Cargo feature flag is enabled in the build."""
        return feature in self.features


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
    except Exception as e:
        logger.warning(
            f"[RustProbe] __version_info__/__version__ raised {type(e).__name__}: {e}; "
            f"treating version as unknown"
    )

    return ver_tuple, ver_str


def _parse_abi_version(ext: object | None) -> tuple[int, int, int]:
    """ISSUE-040: Extract ABI version tuple from extension module.

    Returns (0, 0, 0) if __abi_version__ is not available (old binary before ISSUE-040).
    A tuple of (0, 0, 0) signals unknown ABI — we require explicit >= (1, 0, 0).
    """
    if ext is None:
        return (0, 0, 0)

    # Single getattr — reuse the value, avoid double property invocation
    abi_attr = getattr(ext, "__abi_version__", None)

    # Path 1: callable (function)
    if callable(abi_attr):
        try:
            result = abi_attr()
            if isinstance(result, tuple) and len(result) >= 2:
                return (int(result[0]), int(result[1]), int(result[2]) if len(result) > 2 else 0)
            elif isinstance(result, int):
                # Legacy flat u32 — convert to tuple
                return (result, 0, 0)
            else:
                # Unexpected type — fall through to direct attribute check
                logger.warning(
                    f"[RustProbe] __abi_version__() returned {type(result).__name__} "
                    f"(expected tuple or int); checking direct attribute"
    )
        except Exception as e:
            logger.warning(
                f"[RustProbe] __abi_version__() raised {type(e).__name__}: {e}; "
                f"ABI version unknown"
    )

    # Path 2: direct attribute (constant, not function)
    try:
        if isinstance(abi_attr, tuple) and len(abi_attr) >= 2:
            return (int(abi_attr[0]), int(abi_attr[1]), int(abi_attr[2]) if len(abi_attr) > 2 else 0)
        elif isinstance(abi_attr, int):
            # Legacy flat u32 — convert to tuple
            return (abi_attr, 0, 0)
        # else: attribute is None or wrong type — return (0, 0, 0) below
    except Exception as e:
        logger.warning(
            f"[RustProbe] __abi_version__ (direct attribute) raised {type(e).__name__}: {e}; "
            f"ABI version unknown"
    )

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
    except Exception as e:
        logger.warning(
            f"[RustProbe] __py_version__() raised {type(e).__name__}: {e}; "
            f"Python version compiled-for unknown"
    )
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
    except Exception as e:
        logger.warning(
            f"[RustProbe] __apple_target__() raised {type(e).__name__}: {e}; "
            f"Apple target unknown"
    )
    return None


def _parse_features(ext: object | None) -> set[str]:
    """Extract enabled Cargo feature flags from extension.

    Returns an empty set if __features__ is not available.
    """
    if ext is None:
        return set()
    try:
        features_fn = getattr(ext, "__features__", None)
        if callable(features_fn):
            result = features_fn()
            if isinstance(result, (list, tuple, set)):
                return set(result)
    except Exception as e:
        logger.debug(f"[RustProbe] __features__() raised {type(e).__name__}: {e}")
    return set()


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
                # ISSUE-03: NON-ABI3 build — single correct filename is cpython-314-darwin.so
                # Build config: crate-type=["cdylib","rlib"] + pyo3/extension-module
                # ABI3 builds (abi3-py3XX feature) would produce hledac_rust_extensions.abi3.so
                expected_so = f"hledac_rust_extensions.cpython-{sys.version_info.major}{sys.version_info.minor}-darwin.so"
                cand_path = os.path.join(os.path.dirname(path), expected_so)
                if os.path.isfile(cand_path):
                    path = cand_path
            if os.path.isfile(path):
                return os.path.getmtime(path)
    except Exception:  # noqa: BLE001
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

    ISSUE-01 (CRITICAL): Source freshness gate
    - Compares .so mtime against source file mtimes
    - Uses content hash for cross-session verification
    - Fail-closed: if source is newer than binary, extension is unavailable
    """
    global _PROBED, _EXT, _SO_MTIME, _SO_MTIME_WARNED, _CAP_SCORE, _CAP_SO_MTIME, _CAP_FEATURES, _CAP_SOURCE_HASH, _SOURCE_STALE_WARNED

    # Compute source hash once for the session
    current_source_hash = _compute_source_content_hash()

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
            features=_CAP_FEATURES,
            source_stale=(_PROBED and _CAP_SOURCE_HASH != current_source_hash),
            source_stale_reason="Source changed since probe" if _CAP_SOURCE_HASH != current_source_hash else "",
            source_hash=current_source_hash,
            rebuild_instruction=_get_rebuild_instruction(),
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
                features = frozenset(_parse_features(ext))

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
                        # ISSUE-01: Source freshness gate — fail-closed if source is newer than binary
                        is_stale, stale_reason = _check_source_freshness()

                        if is_stale:
                            # ISSUE-11: Get detailed staleness info for exception
                            build_hash, current_hash, rebuild_cmd = _get_staleness_details()

                            # Log regardless of mode
                            if not _SOURCE_STALE_WARNED:
                                logger.error(
                                    f"[RustProbe] CRITICAL: Stale Rust extension detected!\n"
                                    f"  Reason: {stale_reason}\n"
                                    f"  The .so binary is {int(current_mtime - _get_rust_source_mtime()) if _get_rust_source_mtime() else 0} seconds OLDER than the source.\n"
                                    f"  Source files have been modified since the binary was built.\n"
                                    f"  Falling back to Python (may be slow). For full performance:\n"
                                    f"  {_get_rebuild_instruction()}"
    )
                                _SOURCE_STALE_WARNED = True

                            # ISSUE-11: FAIL-CLOSED behavior - raise exception in hard mode
                            if _RUST_STALE_MODE == "hard":
                                raise RustExtensionStale(
                                    source_hash=build_hash,
                                    current_hash=current_hash,
                                    rebuild_command=rebuild_cmd or _get_rebuild_instruction(),
                                    reason=stale_reason,
    )
                            # Do NOT fall through — stale binary stays unavailable
                        else:
                            # All checks passed — mark extension as available
                            _PROBED = True
                            _EXT = ext
                            _SO_MTIME = current_mtime
                            _CAP_SCORE = cap_score
                            _CAP_SO_MTIME = current_mtime
                            _CAP_FEATURES = features
                            _CAP_SOURCE_HASH = current_source_hash
                            logger.debug(
                                f"[RustProbe] hledac_rust_extensions loaded "
                                f"(version {ver_str}, ABI {_abi_tuple_str(abi_ver)}, "
                                f"capability {cap_score:.0%}, features={sorted(features)}, "
                                f"apple_target={apple_tgt}, source_fresh=True)"
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
    
    # ISSUE-01: Check source freshness for the final result
    is_stale, stale_reason = _check_source_freshness() if _PROBED else (False, "")
    
    return ProbeResult(
        available=_PROBED, ext=_EXT, version_str=ver_str,
        version_tuple=ver_tuple, abi_version=abi_ver,
        abi_major=abi_ver[0], backend=backend,
        capability_score=cap_score, so_mtime=current_mtime,
        py_version=py_ver, apple_target=apple_tgt,
        features=frozenset(),
        source_stale=is_stale,
        source_stale_reason=stale_reason,
        source_hash=current_source_hash,
        rebuild_instruction=_get_rebuild_instruction(),
    )


def force_python() -> ProbeResult:
    """Force Python fallbacks (HLEDAC_FORCE_PYTHON=1)."""
    global _PROBED, _EXT, _SO_MTIME, _SO_MTIME_WARNED, _CAP_SCORE, _CAP_SO_MTIME, _CAP_FEATURES, _CAP_SOURCE_HASH, _SOURCE_STALE_WARNED
    _PROBED = False
    _EXT = None
    _SO_MTIME = None
    _SO_MTIME_WARNED = False
    _CAP_SCORE = 0.0
    _CAP_SO_MTIME = None
    _CAP_FEATURES = frozenset()
    _CAP_SOURCE_HASH = None
    _SOURCE_STALE_WARNED = False
    logger.debug("[RustProbe] Python fallback FORCED via HLEDAC_FORCE_PYTHON=1")
    return ProbeResult(
        available=False, ext=None, version_str="unknown",
        version_tuple=(0, 0, 0), abi_version=(0, 0, 0),
        abi_major=0, backend="python",
        capability_score=0.0, so_mtime=None,
        py_version=None, apple_target=None,
        features=frozenset(),
        source_stale=False,
        source_stale_reason="",
        source_hash=None,
        rebuild_instruction=_get_rebuild_instruction(),
    )


def force_rust() -> ProbeResult:
    """Force Rust path, warn if unavailable or incompatible (HLEDAC_FORCE_RUST=1)."""
    global _PROBED, _EXT, _SO_MTIME, _SO_MTIME_WARNED, _CAP_SCORE, _CAP_SO_MTIME, _CAP_FEATURES, _CAP_SOURCE_HASH
    result = probe()
    if not result.available:
        logger.warning("[RustProbe] HLEDAC_FORCE_RUST=1 but Rust extension unavailable")
    elif not result.is_compatible:
        logger.warning(
            f"[RustProbe] HLEDAC_FORCE_RUST=1 but Rust extension not compatible "
            f"(version={result.version_str}, ABI={_abi_tuple_str(result.abi_version)}, "
            f"source_stale={result.source_stale}); falling back to Python"
    )
        _PROBED = False
        _EXT = None
        _SO_MTIME = None
        _SO_MTIME_WARNED = False
        _CAP_SCORE = 0.0
        _CAP_SO_MTIME = None
        _CAP_FEATURES = frozenset()
        _CAP_SOURCE_HASH = None
        return ProbeResult(
            available=False, ext=None, version_str=result.version_str,
            version_tuple=result.version_tuple, abi_version=result.abi_version,
            abi_major=result.abi_major, backend="python",
            capability_score=0.0, so_mtime=None,
            py_version=None, apple_target=None,
            features=frozenset(),
            source_stale=result.source_stale,
            source_stale_reason=result.source_stale_reason,
            source_hash=result.source_hash,
            rebuild_instruction=result.rebuild_instruction,
    )
    _PROBED = result.available
    _EXT = result.ext if result.available else None
    _SO_MTIME = result.so_mtime if result.available else None
    _SO_MTIME_WARNED = False
    _CAP_SCORE = result.capability_score
    _CAP_SO_MTIME = result.so_mtime
    _CAP_FEATURES = result.features
    _CAP_SOURCE_HASH = result.source_hash
    return result


def reset() -> None:
    """Reset probe cache — for testing only."""
    global _PROBED, _EXT, _SO_MTIME, _SO_MTIME_WARNED, _CAP_SCORE, _CAP_SO_MTIME, _CAP_FEATURES, _CAP_SOURCE_HASH, _SOURCE_STALE_WARNED
    _PROBED = None
    _EXT = None
    _SO_MTIME = None
    _SO_MTIME_WARNED = False
    _CAP_SCORE = 0.0
    _CAP_SO_MTIME = None
    _CAP_FEATURES = frozenset()
    _CAP_SOURCE_HASH = None
    _SOURCE_STALE_WARNED = False
