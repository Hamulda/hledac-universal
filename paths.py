# hledac/universal/paths.py - Single Source of Truth for Runtime Paths
# SPRINT F500I: CANONICAL PATH AUTHORITY + BOOT HYGIENE + IMPORT TRUTH
# ZERO-DEPENDENCY: stdlib only (os, pathlib, warnings, subprocess, typing, stat, errno, tempfile, shutil)

from __future__ import annotations

__all__ = [
    # RAMdisk / fallback roots
    "RAMDISK_ROOT",
    "FALLBACK_ROOT",
    "RAMDISK_ACTIVE",
    "CACHE_ROOT",
    "LIGHTRAG_ROOT",
    # Runtime path constants
    "DB_ROOT",
    "LMDB_ROOT",
    "SPRINT_LMDB_ROOT",
    "EVIDENCE_ROOT",
    "KEYS_ROOT",
    "TOR_ROOT",
    "NYM_ROOT",
    "I2P_ROOT",
    "RUNS_ROOT",
    "SOCKETS_ROOT",
    "SPRINT_STORE_ROOT",
    "IOC_DB_PATH",
    # Sprint artifact helpers
    "get_sprint_parquet_dir",
    "get_ioc_db_path",
    "get_sprint_report_path",
    "get_sprint_json_report_path",
    "get_sprint_next_seeds_path",
    # Boot hygiene
    "assert_ramdisk_alive",
    "cleanup_fallback_artifacts",
    # LMDB
    "lmdb_map_size",
    "get_lmdb_max_size_mb",
    "open_lmdb",
    # Stale lock/socket cleanup (legacy boot hygiene helpers — prefer open_lmdb
    # for new code; these are kept for the AO.initialize() bootstrap path)
    "cleanup_stale_lmdb_locks",
    "cleanup_stale_sockets",
    # XDG Base Directory Specification (Sprint F208A)
    "CTI_EXPORT_DIR",
    "RUNTIME_STATE",
    "EMBEDDING_CACHE",
    "BENCHMARK_CACHE",
]

# Sprint 8VG A.3: Warn if 'None' file exists on disk
import pathlib as _pl
_NONE_PATH = _pl.Path("None")
if _NONE_PATH.exists():
    import warnings
    warnings.warn(
        f"[P0] Soubor 'None' existuje na disku ({_NONE_PATH.resolve()}) "
        f"— spusť: git rm --cached None",
        RuntimeWarning, stacklevel=2
    )

import os
import pathlib
import shutil
import warnings
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# OPSEC Fallback Warning (once-only)
# ---------------------------------------------------------------------------
_OPSEC_FALLBACK_WARNED: bool = False


def _warn_opsec_once(msg: str) -> None:
    global _OPSEC_FALLBACK_WARNED
    if not _OPSEC_FALLBACK_WARNED:
        _OPSEC_FALLBACK_WARNED = True
        warnings.warn(f"[GHOST OPSEC] {msg}", stacklevel=3)


# ---------------------------------------------------------------------------
# Active RAMdisk Check
# ---------------------------------------------------------------------------

def _is_active_ramdisk(path: Path) -> bool:
    """
    Check if path is an active, safe-to-use ramdisk mount.

    Returns True only if:
    1. path exists
    2. path is a mount point
    3. st_dev differs from parent (confirms it's a separate filesystem)
    """
    import os as _os

    if not path.exists():
        return False
    try:
        if path.is_symlink():
            path = path.resolve()
    except OSError:
        return False
    if not _os.path.ismount(path):
        return False
    try:
        return _os.stat(path).st_dev != _os.stat(path.parent).st_dev
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Root Selection
# ---------------------------------------------------------------------------

# Step 1: GHOST_RAMDISK env var
_ramdisk_env = os.environ.get("GHOST_RAMDISK", "")
if _ramdisk_env:
    _SELECTED_ROOT = Path(_ramdisk_env)
else:
    _SELECTED_ROOT = Path("/Volumes/ghost_tmp")

# Step 2: Validate selected root
_RAMDISK_ACTIVE: bool = False
if _is_active_ramdisk(_SELECTED_ROOT):
    _RAMDISK_ACTIVE = True
elif _SELECTED_ROOT.exists():
    # Path exists but is NOT a ramdisk mount — reject it silently
    _SELECTED_ROOT = None
else:
    _SELECTED_ROOT = None

# Step 3: Deterministic fallback (OPSEC-degraded)
if _SELECTED_ROOT is None:
    _FALLBACK_ROOT = Path.home() / ".hledac_fallback_ramdisk"
    _warn_opsec_once(
        "No active ramdisk found at /Volumes/ghost_tmp and GHOST_RAMDISK is unset. "
        "Runtime artifacts will be written to SSD fallback location. "
        "Set GHOST_RAMDISK env var or mount /Volumes/ghost_tmp to avoid OPSEC degradation."
    )
    _SELECTED_ROOT = _FALLBACK_ROOT
    _RAMDISK_ACTIVE = False

RAMDISK_ROOT: Path = _SELECTED_ROOT
FALLBACK_ROOT: Path = _FALLBACK_ROOT if not _RAMDISK_ACTIVE else RAMDISK_ROOT
RAMDISK_ACTIVE: bool = _RAMDISK_ACTIVE

# Sprint 8AR: Cache root for model/HF caches (under RAMDISK_ROOT/FALLBACK_ROOT)
CACHE_ROOT: Path = RAMDISK_ROOT / "cache"

# Sprint 0A: LightRAG root (if needed, no side effects)
LIGHTRAG_ROOT: Path = RAMDISK_ROOT / "lightrag"

# Sprint 0A: Bootstrap tempfile.tempdir to RAMDISK (fail-open)
def _bootstrap_tempfile() -> None:
    """
    Set tempfile.tempdir to RAMDISK_ROOT for all tempfile operations.
    Fail-open: if RAMDISK is not active, use FALLBACK_ROOT.
    """
    import tempfile as _tempfile

    target = str(RAMDISK_ROOT)
    try:
        _tempfile.tempdir = target
    except Exception:
        # Fail-open: continue with system default
        pass


_bootstrap_tempfile()



# Sprint 2B: LMDB MAPSIZE PROPAGATION
def lmdb_map_size() -> int:
    """
    Get LMDB map_size in bytes from GHOST_LMDB_MAX_SIZE_MB env var.

    Returns:
        map_size in bytes (int), default 512MB.
    Bootstrap-safe: can be called before any LMDB init.
    """
    import os as _os

    try:
        mb = int(_os.environ.get("GHOST_LMDB_MAX_SIZE_MB", 512))
    except (ValueError, TypeError):
        mb = 512
    if mb <= 0:
        mb = 512
    return mb * 1024 * 1024


def get_lmdb_max_size_mb() -> int:
    """
    Get GHOST_LMDB_MAX_SIZE_MB from environment, default 512MB.
    Bootstrap-safe: can be called before any LMDB init.
    """
    import os as _os

    try:
        return int(_os.environ.get("GHOST_LMDB_MAX_SIZE_MB", 512))
    except (ValueError, TypeError):
        return 512


def open_lmdb(path: pathlib.Path, *, map_size: Optional[int] = None, **kw) -> Any:
    """
    Open an LMDB environment with consistent defaults and single-retry lock recovery.

    Args:
        path: Path to LMDB directory
        map_size: map_size in bytes. If None, uses lmdb_map_size() (env-driven).
        **kw: Additional arguments passed to lmdb.open().

    Returns:
        lmdb.Environment instance.

    Lock recovery (Sprint 8AG §1.4):
        - Pre-open: safe stale-lock check via lmdb_boot_guard.cleanup_stale_lmdb_lock
          (strict liveness verification, fail-safe, never blind delete)
        - First open attempt
        - On LockError: single retry after safe cleanup (only if holder confirmed dead)
    """
    import lmdb

    if map_size is None:
        map_size = lmdb_map_size()

    # Sprint 8AG §1.4: Pre-open safe lock cleanup before first attempt
    try:
        from hledac.universal.knowledge.lmdb_boot_guard import cleanup_stale_lmdb_lock
        cleanup_stale_lmdb_lock(path)
    except Exception:
        pass  # Defensive: never let pre-cleanup failure prevent open attempt

    # M1 UMA: writemap=False + sync=False prevent OS page cache thrashing
    defaults = {"writemap": False, "sync": False}
    merged_kw = {**defaults, **kw}

    try:
        return lmdb.open(str(path), map_size=map_size, **merged_kw)
    except lmdb.LockError:
        # Sprint 8AG §1.4: safe stale-lock recovery with strict liveness check
        try:
            from hledac.universal.knowledge.lmdb_boot_guard import cleanup_stale_lmdb_lock
            removed, reason = cleanup_stale_lmdb_lock(path)
            import logging
            _logger = logging.getLogger(__name__)
            _logger.debug(f"LMDB lock recovery: removed={removed} reason={reason}")
        except Exception:
            removed = 0
        if removed:
            # Holder was confirmed dead — safe to retry once
            try:
                return lmdb.open(str(path), map_size=map_size, **merged_kw)
            except lmdb.LockError:
                raise
        raise  # No lock removed or cleanup failed — propagate original error


# ---------------------------------------------------------------------------
# Project-Local Runtime Paths (Sprint F208A)
# All runtime data lives under hledac/universal/runtime/
# GHOST_EXPORT_DIR env var overrides CTI_EXPORT_DIR (downstream compat)
# ---------------------------------------------------------------------------

_PROJECT_ROOT: Path = Path(__file__).parent  # hledac/universal/

RUNTIME_BASE: Path = _PROJECT_ROOT / "runtime"

# CTI and diagnostic export paths
CTI_EXPORT_DIR: Path = RUNTIME_BASE / "cti"
RUNS_ROOT: Path = RUNTIME_BASE / "runs"  # diagnostic/markdown/stix bundle runs

# State and cache paths
RUNTIME_STATE: Path = RUNTIME_BASE / "state"
EMBEDDING_CACHE: Path = RUNTIME_BASE / "embeddings"
BENCHMARK_CACHE: Path = RUNTIME_BASE / "benchmarks"

# Backward-compat alias (RAMDISK_ROOT was the old CTI export root)
RAMDISK_ROOT: Path = CTI_EXPORT_DIR

# Initialize runtime directories at import time
for _dir in (CTI_EXPORT_DIR, RUNS_ROOT, RUNTIME_STATE, EMBEDDING_CACHE, BENCHMARK_CACHE):
    _dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Runtime Path Constants (legacy paths, still used by other subsystems)
# These use RAMDISK_ROOT which is now CTI_EXPORT_DIR (project-local)
# Kept for backward compat with existing code that references DB_ROOT, LMDB_ROOT, etc.
# ---------------------------------------------------------------------------

DB_ROOT: Path = RAMDISK_ROOT / "db"
LMDB_ROOT: Path = DB_ROOT / "lmdb"
SPRINT_LMDB_ROOT: Path = LMDB_ROOT / "sprint"  # Sprint 3D: ephemeral sprint caches
EVIDENCE_ROOT: Path = RAMDISK_ROOT / "evidence"
KEYS_ROOT: Path = RAMDISK_ROOT / "keys"
TOR_ROOT: Path = RAMDISK_ROOT / "tor"
NYM_ROOT: Path = RAMDISK_ROOT / "nym"
I2P_ROOT: Path = RAMDISK_ROOT / "i2p"
SOCKETS_ROOT: Path = RAMDISK_ROOT / "sockets"

# Sprint 8VD: Arrow/Parquet sprint store root
SPRINT_STORE_ROOT: Path = Path(
    os.environ.get("HLEDAC_SPRINT_STORE", "~/.hledac/sprints")
).expanduser()


def get_sprint_parquet_dir(sprint_id: str) -> Path:
    """Return sprint Parquet directory, created if needed."""
    p = SPRINT_STORE_ROOT / sprint_id
    p.mkdir(parents=True, exist_ok=True)
    return p


# Sprint 8VG B.1: Persistent DuckDB IOC graph store
IOC_DB_PATH: Path = (
    SPRINT_STORE_ROOT.parent / "ioc_graph.duckdb"
)
IOC_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_ioc_db_path() -> pathlib.Path:
    """Vrátí cestu k persistentnímu DuckDB IOC store."""
    return IOC_DB_PATH


def get_sprint_report_path(sprint_id: str) -> Path:
    """
    Sprint 8VY §C: Canonical sprint report path computation.

    Canonical owner: paths.py — all sprint report path computation lives here.
    Shell (__main__) no longer holds path computation authority.

    Path semantics: ~/.hledac/reports/{sprint_id}.md

    Returns
    -------
    Path
        Absolute path to sprint report markdown file.
    """
    reports_dir = Path.home() / ".hledac" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir / f"{sprint_id}.md"


def get_sprint_json_report_path(sprint_id: str) -> Path:
    """
    Sprint F500A §A: Canonical JSON sprint report path computation.

    Parallels get_sprint_report_path() for the JSON sibling file.
    Consumer: export/sprint_exporter.py inline computation
    (report_dir = SPRINT_STORE_ROOT.parent / "reports").

    Path semantics: ~/.hledac/reports/{sprint_id}_report.json

    Returns
    -------
    Path
        Absolute path to sprint report JSON file.
    """
    reports_dir = Path.home() / ".hledac" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir / f"{sprint_id}_report.json"


def get_sprint_next_seeds_path(sprint_id: str) -> Path:
    """
    Sprint F500A §T004: Canonical next-seeds JSON path computation.

    Parallels get_sprint_report_path() and get_sprint_json_report_path()
    for the third export artifact — seed tasks for the next sprint.

    Consumer: export/sprint_exporter._generate_next_sprint_seeds()
    (report_dir / f"{sprint_id}_next_seeds.json" → this helper).

    Path semantics: ~/.hledac/reports/{sprint_id}_next_seeds.json

    Returns
    -------
    Path
        Absolute path to next-seeds JSON file.
    """
    reports_dir = Path.home() / ".hledac" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir / f"{sprint_id}_next_seeds.json"


# ---------------------------------------------------------------------------
# Directory Initialization (parents=True, exist_ok=True)
# Regular dirs: standard mkdir
# Security dirs: explicit chmod 0o700 after mkdir (umask can weaken)
# ---------------------------------------------------------------------------

def _ensure_dir(path: Path, mode: Optional[int] = None) -> None:
    """Ensure directory exists, optionally with specific permissions."""
    if mode is not None:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)
    else:
        path.mkdir(parents=True, exist_ok=True)


# Initialize regular runtime directories
for _dir in [DB_ROOT, LMDB_ROOT, SPRINT_LMDB_ROOT, EVIDENCE_ROOT, RUNS_ROOT, SOCKETS_ROOT, CACHE_ROOT]:
    _ensure_dir(_dir)

# Initialize security-sensitive directories with 0o700
for _dir in [KEYS_ROOT, TOR_ROOT, NYM_ROOT, I2P_ROOT]:
    _ensure_dir(_dir, mode=0o700)


# ---------------------------------------------------------------------------
# Project-Local Runtime Paths (Sprint F208A)
# All runtime data lives under hledac/universal/runtime/
# GHOST_EXPORT_DIR env var overrides CTI_EXPORT_DIR (downstream compat)
# ---------------------------------------------------------------------------

_PROJECT_ROOT: Path = Path(__file__).parent  # hledac/universal/

RUNTIME_BASE: Path = _PROJECT_ROOT / "runtime"

# CTI and diagnostic export paths
CTI_EXPORT_DIR: Path = RUNTIME_BASE / "cti"
RUNS_ROOT: Path = RUNTIME_BASE / "runs"  # diagnostic/markdown/stix bundle runs

# State and cache paths
RUNTIME_STATE: Path = RUNTIME_BASE / "state"
EMBEDDING_CACHE: Path = RUNTIME_BASE / "embeddings"
BENCHMARK_CACHE: Path = RUNTIME_BASE / "benchmarks"

# Backward-compat alias (RAMDISK_ROOT was the old CTI export root)
RAMDISK_ROOT: Path = CTI_EXPORT_DIR

# Initialize runtime directories at import time
for _dir in (CTI_EXPORT_DIR, RUNS_ROOT, RUNTIME_STATE, EMBEDDING_CACHE, BENCHMARK_CACHE):
    _dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# assert_ramdisk_alive: Raise if RAMDISK was active at import but disappeared
# ---------------------------------------------------------------------------

def assert_ramdisk_alive() -> None:
    """
    Verify RAMDISK_ROOT is still available.

    Raises RuntimeError if RAMDISK_ACTIVE was True at import-time but
    RAMDISK_ROOT is no longer a valid mount point.
    """
    if RAMDISK_ACTIVE and not _is_active_ramdisk(RAMDISK_ROOT):
        raise RuntimeError(
            f"[GHOST OPSEC] RAMDISK at {RAMDISK_ROOT} is no longer available. "
            "Cannot continue with OPSEC-degraded storage. "
            "Set GHOST_RAMDISK env var or mount /Volumes/ghost_tmp."
        )


# ---------------------------------------------------------------------------
# cleanup_fallback_artifacts: Clean up deterministic fallback on shutdown
# ---------------------------------------------------------------------------

def cleanup_fallback_artifacts() -> None:
    """
    Remove deterministic fallback ramdisk artifacts on clean shutdown.

    Only removes the FALLBACK_ROOT directory if:
    1. We are using fallback (not active ramdisk)
    2. The directory is empty
    3. It was created by this process (is beneath Path.home())

    This is a no-op when using a real ramdisk.
    """
    if RAMDISK_ACTIVE:
        return
    fallback = FALLBACK_ROOT
    if not fallback.exists():
        return
    # Only clean up if it's our deterministic fallback (beneath home)
    try:
        fallback.relative_to(Path.home())
    except ValueError:
        # Not beneath home — don't touch
        return
    try:
        # Remove if empty
        if not any(fallback.iterdir()):
            shutil.rmtree(fallback, ignore_errors=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# LMDB / Socket Cleanup Helpers (for use at boot time)
# ---------------------------------------------------------------------------

def cleanup_stale_lmdb_locks(lmdb_root: Path) -> int:
    """
    Remove stale lock.mdb files from LMDB directories.

    Only deletes files named exactly 'lock.mdb'.
    Does NOT delete data.mdb, *.sqlite, or directories.

    Scan depth:
    - lmdb_root/lock.mdb
    - lmdb_root/*/lock.mdb

    Returns count of lock files removed.
    """
    import os as _os

    removed = 0
    if not lmdb_root.exists():
        return 0

    # Direct lock.mdb
    direct_lock = lmdb_root / "lock.mdb"
    if direct_lock.is_file():
        try:
            direct_lock.unlink()
            removed += 1
        except OSError:
            pass

    # One level deep: lmdb_root/*/lock.mdb
    try:
        for entry in lmdb_root.iterdir():
            if entry.is_dir():
                lock_file = entry / "lock.mdb"
                if lock_file.is_file():
                    try:
                        lock_file.unlink()
                        removed += 1
                    except OSError:
                        pass
    except OSError:
        pass

    return removed


def _is_socket_orphaned(sock_path: Path) -> bool:
    """
    Check if a Unix socket file is orphaned (no process listening).

    Returns True if connect() is refused or socket file not found,
    indicating the socket is stale and safe to remove.
    """
    import socket as _socket

    probe = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        probe.settimeout(0.5)
        probe.connect(str(sock_path))
        return False
    except ConnectionRefusedError:
        return True
    except (OSError, FileNotFoundError):
        return True
    finally:
        try:
            probe.close()
        except Exception:
            pass


def cleanup_stale_sockets(sockets_root: Path) -> int:
    """
    Remove stale Unix socket files from sockets directory.

    A socket is removed only if it is orphaned (no listener).
    Uses _is_socket_orphaned() for connection probe.

    Returns count of socket files removed.
    """
    removed = 0
    if not sockets_root.exists():
        return 0

    try:
        for entry in sockets_root.iterdir():
            if entry.suffix == ".sock" and entry.is_socket():
                if _is_socket_orphaned(entry):
                    try:
                        entry.unlink()
                        removed += 1
                    except OSError:
                        pass
    except OSError:
        pass

    return removed
