import asyncio
import pathlib as _pl
import atexit
import contextvars

from dataclasses import dataclass
import msgspec
from typing import cast
__all__ = ['RAMDISK_ROOT', 'FALLBACK_ROOT', 'RAMDISK_ACTIVE', 'CACHE_ROOT', 'LIGHTRAG_ROOT', 'DB_ROOT', 'LMDB_ROOT', 'SPRINT_LMDB_ROOT', 'EVIDENCE_ROOT', 'KEYS_ROOT', 'TOR_ROOT', 'NYM_ROOT', 'I2P_ROOT', 'RUNS_ROOT', 'SOCKETS_ROOT', 'SPRINT_STORE_ROOT', 'IOC_DB_PATH', 'PATHS', 'get_current_paths', 'set_current_paths', 'reset_current_paths', 'get_sprint_parquet_dir', 'get_dedup_paths', 'get_ioc_db_path', 'get_sprint_report_path', 'get_sprint_json_report_path', 'get_sprint_next_seeds_path', 'get_sprint_bundle_path', 'assert_ramdisk_alive', 'cleanup_fallback_artifacts', 'is_auto_ramdisk', 'lmdb_map_size', 'get_lmdb_max_size_mb', 'open_lmdb', 'cleanup_stale_lmdb_locks', 'compact_sprint_lmdb', 'cleanup_stale_sockets', 'CTI_EXPORT_DIR', 'RUNTIME_STATE', 'EMBEDDING_CACHE', 'BENCHMARK_CACHE', '_ensure_ramdisk_active_async']
_paths_context_var: contextvars.ContextVar[_Paths | None] = contextvars.ContextVar('_paths_context', default=None)

def get_current_paths() -> _Paths:
    """Get the current task's path bundle, or module defaults if not set.

    Returns:
        _Paths instance for the current asyncio task context.
    """
    val = _paths_context_var.get()
    return val if val is not None else PATHS

def set_current_paths(paths: _Paths) -> None:
    """Set the current task's path bundle (task-local override).

    Args:
        paths: _Paths instance to use for this task context.
    """
    _paths_context_var.set(paths)

def reset_current_paths() -> None:
    """Reset current task's paths to module defaults (clear ContextVar override)."""
    _paths_context_var.set(None)
_NONE_PATH = _pl.Path('None')
if _NONE_PATH.exists():
    import warnings
    warnings.warn(f"[P0] Soubor 'None' existuje na disku ({_NONE_PATH.resolve()}) — spusť: git rm --cached None", RuntimeWarning, stacklevel=2)
import atexit
import logging
import os
import pathlib
import shutil
from hledac.universal.core.locks import make_lock, LockCategory
import warnings
from pathlib import Path
from typing import Any
from core import aclose
_logger = logging.getLogger(__name__)
_OPSEC_FALLBACK_WARNED: bool = False

def _warn_opsec_once(msg: str) -> None:
    global _OPSEC_FALLBACK_WARNED
    if not _OPSEC_FALLBACK_WARNED:
        _OPSEC_FALLBACK_WARNED = True
        warnings.warn(f'[GHOST OPSEC] {msg}', stacklevel=3)
_AUTO_CREATED_DEVICE: str | None = None
_AUTO_CREATED_LOCK = make_lock(LockCategory.CACHE, "paths._AUTO_CREATED_LOCK", prefer_unfair=True)

def _cleanup_auto_ramdisk() -> None:
    """
    Cleanup auto-created RAM disk on process exit.

    Uses hdiutil detach -force to ensure clean removal.
    Registered via atexit at module import time.
    Thread-safe: uses lock to guard _AUTO_CREATED_DEVICE access.
    """
    global _AUTO_CREATED_DEVICE
    with _AUTO_CREATED_LOCK:
        if _AUTO_CREATED_DEVICE is None:
            return
        device = _AUTO_CREATED_DEVICE
        _AUTO_CREATED_DEVICE = None
    import subprocess as _subprocess
    try:
        _subprocess.run(['hdiutil', 'detach', device, '-force'], capture_output=True, timeout=10)
        _logger.debug(f'Auto RAM disk cleaned up: {device}')
    except Exception as e:
        _logger.error(f'Failed to cleanup auto RAM disk {device}: {e}')
atexit.register(_cleanup_auto_ramdisk)

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
_ramdisk_env = os.environ.get('HLEDAC_RAMDISK', '') or os.environ.get('GHOST_RAMDISK', '')
# ISSUE-033: LIBC_PERF_OPT — SSD optimization hint
# When True: /tmp is treated as a viable fast-path on SSD (APFS-backed on macOS).
# This eliminates RAM disk overhead for testing on SSD-only machines.
_LIBC_PERF_OPT: bool = os.environ.get('LIBC_PERF_OPT', '0').lower() in ('1', 'true', 'yes')
# ISSUE-033: Deferred creation — mkdir happens at runtime, not on import (F500I spirit).
# This avoids side-effects during module load and keeps import latency low.
_TMP_OPT_ROOT: Path | None = Path('/tmp/hledac_tmp') if _LIBC_PERF_OPT else None
if _ramdisk_env:
    _SELECTED_ROOT = Path(_ramdisk_env)
else:
    _SELECTED_ROOT = Path('/Volumes/ghost_tmp')
_RAMDISK_ACTIVE: bool = False
if _is_active_ramdisk(_SELECTED_ROOT):
    _RAMDISK_ACTIVE = True
elif _ramdisk_env and _SELECTED_ROOT.exists():
    pass  # User-specified path that exists but isn't active ramdisk — use as-is (SSD fallback)
elif not _SELECTED_ROOT.exists():
    _SELECTED_ROOT = None  # Only None if default path doesn't exist AND no env var override

def _try_create_ramdisk() -> tuple[Path | None, bool]:
    """
    Attempt to create a RAM disk using hdiutil.

    Returns:
        (path, is_active) tuple. path is None if creation failed.
    Stores device in _AUTO_CREATED_DEVICE for atexit cleanup.
    """
    import subprocess as _subprocess
    import time as _time
    global _AUTO_CREATED_DEVICE
    RAMDISK_SIZE_SECTORS = 2097152
    RAMDISK_MOUNT_POINT = '/tmp/hledac_ramdisk'
    try:
        if _is_active_ramdisk(Path(RAMDISK_MOUNT_POINT)):
            os.environ['HLEDAC_RAMDISK'] = RAMDISK_MOUNT_POINT
            os.environ['HLEDAC_RAMDISK_AUTO_CREATED'] = '0'
            return (Path(RAMDISK_MOUNT_POINT), True)
        device_result = _subprocess.run(['hdiutil', 'attach', '-nomount', f'ram://{RAMDISK_SIZE_SECTORS}'], capture_output=True, text=True, timeout=10)
        device = device_result.stdout.strip()
        if not device:
            return (None, False)
        _AUTO_CREATED_DEVICE = device
        try:
            _subprocess.run(['diskutil', 'erasevolume', 'HFS+', 'RAMDisk', device], capture_output=True, timeout=10)
        except Exception:  # noqa: BLE001
            pass
        # B2-FIX: This 500ms sleep waits for HFS+ volume to settle — macOS requires
        # this before the mount becomes visible. _try_create_ramdisk is ALWAYS called
        # via asyncio.to_thread() from async paths, so this never blocks the event loop.
        # For truly sync callers (none currently exist), wrap with asyncio.to_thread().
        try:
            _time.sleep(0.5)
        except Exception:  # noqa: BLE001
            pass
        actual_mount = None
        for line in _subprocess.run(['mount'], capture_output=True, text=True, timeout=5).stdout.splitlines():
            if 'RAMDisk' in line and '/dev/disk' in line:
                parts = line.split()
                if len(parts) >= 3:
                    actual_mount = parts[2]
                    break
        if actual_mount:
            ramdisk_path = Path(actual_mount)
            for subdir in ['duckdb_tmp', 'sockets', 'warc', 'arrow']:
                (ramdisk_path / subdir).mkdir(exist_ok=True)
            os.environ['HLEDAC_RAMDISK'] = actual_mount
            os.environ['HLEDAC_RAMDISK_AUTO_CREATED'] = '1'
            return (ramdisk_path, True)
    except Exception:  # noqa: BLE001
        pass
    return (None, False)
# Sprint F500I: Deferred RAM disk creation — do NOT call hdiutil on import.
# RAM disk creation is deferred to first actual use (assert_ramdisk_alive() or open_lmdb()).
# This eliminates ~2.8s import bottleneck from subprocess hdiutil calls.
# Fallback to SSD is safe and correct; RAM disk is a performance optimization only.
_FALLBACK_ROOT: Path = Path.home() / '.hledac_fallback_ramdisk'
if _SELECTED_ROOT is None:
    _FALLBACK_ROOT = Path.home() / '.hledac_fallback_ramdisk'
    _warn_opsec_once('No active ramdisk found at import time. Using SSD fallback for runtime artifacts. Set GHOST_RAMDISK env var or mount /Volumes/ghost_tmp for optimal M1 performance.')
    _SELECTED_ROOT = _FALLBACK_ROOT
    _RAMDISK_ACTIVE = False


def _ensure_ramdisk_active() -> None:
    """
    Ensure RAM disk is active for runtime that requires it.

    Called lazily before LMDB/open or when RAMDISK_ROOT is first accessed.
    Attempts RAM disk creation if not already active.
    Thread-safe: idempotent (only runs once).

    B2-FIX: This sync variant is for thread-pool callers (duckdb_store).
    For async callers, use _ensure_ramdisk_active_async().
    """
    global _SELECTED_ROOT, _RAMDISK_ACTIVE, _FALLBACK_ROOT
    if _RAMDISK_ACTIVE:
        return
    if _SELECTED_ROOT is not None and _SELECTED_ROOT != _FALLBACK_ROOT:
        return  # Already have a working non-fallback root
    # Try to create RAM disk now (at runtime, not import time)
    auto_path, auto_active = _try_create_ramdisk()
    if auto_path is not None:
        _SELECTED_ROOT = auto_path
        _RAMDISK_ACTIVE = auto_active
        import warnings as _w
        _w.warn(f'[GHOST OPSEC] RAM disk activated at runtime: {_SELECTED_ROOT}', stacklevel=2)
        if auto_active and os.environ.get('HLEDAC_RAMDISK_AUTO_CREATED') == '1':
            duckdb_temp_path = str(_SELECTED_ROOT / 'duckdb_tmp')
            os.environ.setdefault('HLEDAC_DUCKDB_RAMDISK_TEMP', duckdb_temp_path)


async def _ensure_ramdisk_active_async() -> None:
    """
    B2-FIX: Async variant of _ensure_ramdisk_active().

    Non-blocking for async callers. Uses asyncio.to_thread() to run
    the blocking _try_create_ramdisk() in a thread pool, then yields
    to the event loop.
    """
    global _SELECTED_ROOT, _RAMDISK_ACTIVE, _FALLBACK_ROOT
    if _RAMDISK_ACTIVE:
        return
    if _SELECTED_ROOT is not None and _SELECTED_ROOT != _FALLBACK_ROOT:
        return  # Already have a working non-fallback root
    # Run blocking RAM disk creation in thread pool (avoids event loop blocking)
    auto_path, auto_active = await asyncio.to_thread(_try_create_ramdisk)
    if auto_path is not None:
        _SELECTED_ROOT = auto_path
        _RAMDISK_ACTIVE = auto_active
        import warnings as _w
        _w.warn(f'[GHOST OPSEC] RAM disk activated at runtime: {_SELECTED_ROOT}', stacklevel=2)
        if auto_active and os.environ.get('HLEDAC_RAMDISK_AUTO_CREATED') == '1':
            duckdb_temp_path = str(_SELECTED_ROOT / 'duckdb_tmp')
            os.environ.setdefault('HLEDAC_DUCKDB_RAMDISK_TEMP', duckdb_temp_path)


RAMDISK_ROOT: Path = _SELECTED_ROOT
FALLBACK_ROOT: Path = _FALLBACK_ROOT if not _RAMDISK_ACTIVE else RAMDISK_ROOT
RAMDISK_ACTIVE: bool = _RAMDISK_ACTIVE
if _RAMDISK_ACTIVE and os.environ.get('HLEDAC_RAMDISK_AUTO_CREATED') == '1':
    duckdb_temp_path = str(RAMDISK_ROOT / 'duckdb_tmp')
    os.environ.setdefault('HLEDAC_DUCKDB_RAMDISK_TEMP', duckdb_temp_path)

def is_auto_ramdisk() -> bool:
    """
    Return True if RAM disk was auto-created by this process.

    Allows other modules to detect auto-created RAM disk vs mounted one.
    """
    return _RAMDISK_ACTIVE and os.environ.get('HLEDAC_RAMDISK_AUTO_CREATED') == '1'
CACHE_ROOT: Path = RAMDISK_ROOT / 'cache'
LIGHTRAG_ROOT: Path = RAMDISK_ROOT / 'lightrag'

def _bootstrap_tempfile() -> None:
    """
    Set tempfile.tempdir to RAMDISK_ROOT for all tempfile operations.

    ISSUE-033: When LIBC_PERF_OPT=1, uses /tmp/hledac_tmp instead of RAMDISK_ROOT.
    On macOS /tmp is APFS/SSD-backed — suitable for testing without RAM disk overhead.
    Fail-open: if RAMDISK is not active, use FALLBACK_ROOT.

    Deferred creation (F500I spirit): /tmp/hledac_tmp is created on first use,
    not at module import time.
    """
    import tempfile as _tempfile
    # ISSUE-033: LIBC_PERF_OPT uses /tmp/hledac_tmp for SSD optimization
    if _LIBC_PERF_OPT and _TMP_OPT_ROOT is not None:
        # Deferred creation: create directory on first use (F500I)
        try:
            _TMP_OPT_ROOT.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
        target = str(_TMP_OPT_ROOT)
    else:
        target = str(RAMDISK_ROOT)
    try:
        _tempfile.tempdir = target
    except Exception:  # noqa: BLE001
        pass
_bootstrap_tempfile()

def lmdb_map_size() -> int:
    """
    Get LMDB map_size in bytes from StorageConfig (GHOST_LMDB_MAX_SIZE_MB env var).

    Returns:
        map_size in bytes (int), default 256MB (Phase4: M1 8GB ceiling).
    Bootstrap-safe: can be called before any LMDB init.

    ISSUE-033: Delegates to StorageConfig to avoid duplicate GHOST_LMDB_MAX_SIZE_MB parsing.
    """
    try:
        from hledac.universal.core.config import get_storage_config
        return get_storage_config().lmdb_map_size_mb * 1024 * 1024
    except Exception:
        # Fallback: bootstrap-safe (StorageConfig may not be importable at earliest init)
        import os as _os
        try:
            mb = int(_os.environ.get('GHOST_LMDB_MAX_SIZE_MB', 256))
        except (ValueError, TypeError):
            mb = 256
        return max(mb, 1) * 1024 * 1024

def get_lmdb_max_size_mb() -> int:
    """
    Get LMDB max_size in MB from StorageConfig (GHOST_LMDB_MAX_SIZE_MB env var).

    Returns:
        Size in MB, default 256MB (Phase4: M1 8GB ceiling).
    Bootstrap-safe: can be called before any LMDB init.

    ISSUE-033: Delegates to StorageConfig to avoid duplicate GHOST_LMDB_MAX_SIZE_MB parsing.
    """
    try:
        from hledac.universal.core.config import get_storage_config
        return get_storage_config().lmdb_map_size_mb
    except Exception:
        # Fallback: bootstrap-safe
        import os as _os
        try:
            return int(_os.environ.get('GHOST_LMDB_MAX_SIZE_MB', 256))
        except (ValueError, TypeError):
            return 256

def _chmod_lmdb_path(path: pathlib.Path) -> None:
    """
    SEC-02: Harden LMDB directory and data file permissions.

    Ensures the LMDB directory is 0700 and all .mdb data files are 0600.
    Covers the lock file (.lock) as well. Fails silently on platforms
    where chmod is not supported (e.g. some network filesystems).
    """
    import os
    import stat as _stat

    # Harden directory
    try:
        os.chmod(path, _stat.S_IRUSR | _stat.S_IWUSR | _stat.S_IXUSR)  # 0o700
    except OSError:  # noqa: BLE001
        pass

    # Harden LMDB data files and lock file
    for suffix in ("*.mdb", "*.lock"):
        for file_path in path.glob(suffix):
            try:
                os.chmod(file_path, _stat.S_IRUSR | _stat.S_IWUSR)  # 0o600
            except OSError:  # noqa: BLE001
                pass


def open_lmdb(path: pathlib.Path, *, map_size: int | None=None, **kw) -> Any:
    """
    Open an LMDB environment with consistent defaults and single-retry lock recovery.

    SEC-02: Enforces 0o600 on LMDB data files and 0o700 on the directory.
    Uses os.umask + explicit chmod to guarantee permissions regardless of
    the process umask inherited from the parent shell.

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
    import os
    import stat as _stat

    if map_size is None:
        map_size = lmdb_map_size()

    # SEC-02: Create directory with 0700 (must happen before umask scope)
    path.mkdir(parents=True, exist_ok=True)

    # SEC-02: Set umask to 0077 for the duration of lmdb.open so that
    # any files created by lmdb (data.mdb, lock.lck) inherit 0o600.
    _old_umask = os.umask(0o077)
    try:
        try:
            from hledac.universal.knowledge.lmdb_boot_guard import cleanup_stale_lmdb_lock
            cleanup_stale_lmdb_lock(path)
        except Exception:  # noqa: BLE001
            pass
        _effective_map_size: int = map_size
        # SEC-02: explicit mode=0o600 in defaults so lmdb respects our umask intent
        defaults = {'writemap': False, 'sync': False, 'mode': 0o600}
        merged_kw = {**defaults, **kw}
        _instrument_lmdb_env = None
        try:
            from hledac.universal.runtime._telemetry_setup import instrument_lmdb_env as _instrument_lmdb_env
        except ImportError:  # noqa: BLE001
            pass
        try:
            env = lmdb.open(str(path), map_size=map_size, **merged_kw)
            # SEC-02: Double-enforce permissions after open to cover all files
            _chmod_lmdb_path(path)
            if _instrument_lmdb_env is not None:
                env = _instrument_lmdb_env(env)
            return env
        except lmdb.LockError:
            try:
                from hledac.universal.knowledge.lmdb_boot_guard import cleanup_stale_lmdb_lock
                removed, reason = cleanup_stale_lmdb_lock(path)
                import logging
                _logger = logging.getLogger(__name__)
                _logger.debug(f'LMDB lock recovery: removed={removed} reason={reason}')
            except Exception:
                removed = 0
            if removed:
                try:
                    env = lmdb.open(str(path), map_size=_effective_map_size, **merged_kw)
                    _chmod_lmdb_path(path)
                    if _instrument_lmdb_env is not None:
                        env = _instrument_lmdb_env(env)
                    return env
                except lmdb.LockError:
                    raise
            raise
    finally:
        os.umask(_old_umask)
_PROJECT_ROOT: Path = Path(__file__).parent
RUNTIME_BASE: Path = _PROJECT_ROOT / 'runtime'
CTI_EXPORT_DIR: Path = RUNTIME_BASE / 'cti'
RUNS_ROOT: Path = RUNTIME_BASE / 'runs'
RUNTIME_STATE: Path = RUNTIME_BASE / 'state'
EMBEDDING_CACHE: Path = RUNTIME_BASE / 'embeddings'
BENCHMARK_CACHE: Path = RUNTIME_BASE / 'benchmarks'
for _dir in (CTI_EXPORT_DIR, RUNS_ROOT, RUNTIME_STATE, EMBEDDING_CACHE, BENCHMARK_CACHE):
    _dir.mkdir(parents=True, exist_ok=True)
DB_ROOT: Path = RAMDISK_ROOT / 'db'
LMDB_ROOT: Path = DB_ROOT / 'lmdb'
SPRINT_LMDB_ROOT: Path = LMDB_ROOT / 'sprint'
EVIDENCE_ROOT: Path = RAMDISK_ROOT / 'evidence'
KEYS_ROOT: Path = RAMDISK_ROOT / 'keys'
TOR_ROOT: Path = RAMDISK_ROOT / 'tor'
NYM_ROOT: Path = RAMDISK_ROOT / 'nym'
I2P_ROOT: Path = RAMDISK_ROOT / 'i2p'
SOCKETS_ROOT: Path = RAMDISK_ROOT / 'sockets'
_SPRINT_STORE_DEFAULT = '~/.hledac/sprints' if not RAMDISK_ACTIVE else str(RAMDISK_ROOT / 'sprints')
SPRINT_STORE_ROOT: Path = Path(os.environ.get('HLEDAC_SPRINT_STORE', _SPRINT_STORE_DEFAULT)).expanduser()
_DUCKDB_STORE_DEFAULT = str(RAMDISK_ROOT / 'duckdb_store') if RAMDISK_ACTIVE else '~/.hledac/duckdb_store'
DUCKDB_STORE_ROOT: Path = Path(os.environ['HLEDAC_DUCKDB_STORE']) if 'HLEDAC_DUCKDB_STORE' in os.environ else Path(_DUCKDB_STORE_DEFAULT)
_LMDB_STORE_DEFAULT = str(RAMDISK_ROOT / 'lmdb_store') if RAMDISK_ACTIVE else '~/.hledac/lmdb_store'
LMDB_STORE_ROOT: Path = Path(os.environ['HLEDAC_LMDB_STORE']) if 'HLEDAC_LMDB_STORE' in os.environ else Path(_LMDB_STORE_DEFAULT)
_LANCEDB_STORE_DEFAULT = str(RAMDISK_ROOT / 'lancedb_store') if RAMDISK_ACTIVE else '~/.hledac/lancedb_store'
LANCEDB_STORE_ROOT: Path = Path(os.environ['HLEDAC_LANCEDB_STORE']) if 'HLEDAC_LANCEDB_STORE' in os.environ else Path(_LANCEDB_STORE_DEFAULT)
_UNRESOLVED: object = object()
_DEDUP_PATHS_CACHE: dict[str, Path] | object = _UNRESOLVED
_DEDUP_PATHS_LOCK = make_lock(LockCategory.CONFIG, "paths._DEDUP_PATHS_LOCK", prefer_unfair=True)

def resolve_dedup_paths(env_prefix: str='HLEDAC_DEDUP') -> dict[str, Path]:
    """
    Resolve all dedup storage paths.
    Env precedence for LMDB root:
      1. HLEDAC_DEDUP_LMDB_PATH (full path override)
      2. HLEDAC_LMDB_STORE (LMDB_STORE_ROOT env)
      3. ~/.hledac/lmdb_store (default)
    Env precedence for Bloom directory:
      1. HLEDAC_DEDUP_BLOOM_DIR
      2. <lmdb_root>/bloom (co-located)
    Returns dict with keys: lmdb_root, dedup_lmdb, bloom_dir,
                            bloom_active, bloom_previous, bloom_lock
    """
    env_lmdb_override = os.environ.get(f'{env_prefix}_LMDB_PATH')
    env_store_root = os.environ.get('HLEDAC_LMDB_STORE')
    if env_lmdb_override:
        lmdb_root = Path(env_lmdb_override)
    elif env_store_root:
        lmdb_root = Path(env_store_root)
    else:
        lmdb_root = Path(_LMDB_STORE_DEFAULT).expanduser()
    bloom_dir = Path(os.environ.get(f'{env_prefix}_BLOOM_DIR', str(lmdb_root / 'bloom')))
    try:
        lmdb_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        lmdb_root = Path('~/.hledac/lmdb_store').expanduser()
        lmdb_root.mkdir(parents=True, exist_ok=True)
    try:
        bloom_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        bloom_dir = lmdb_root / 'bloom'
        bloom_dir.mkdir(parents=True, exist_ok=True)
    # SEC-02: Enforce permissions on dedup LMDB root and bloom directory
    _chmod_lmdb_path(lmdb_root)
    _chmod_lmdb_path(bloom_dir)
    return {'lmdb_root': lmdb_root, 'dedup_lmdb': lmdb_root / 'dedup.lmdb', 'bloom_dir': bloom_dir, 'bloom_active': bloom_dir / 'bloom_active.mmap', 'bloom_previous': bloom_dir / 'bloom_previous.mmap', 'bloom_lock': bloom_dir / 'bloom.lock'}

def get_dedup_paths() -> dict[str, Path]:
    """
    Thread-safe singleton accessor for default dedup paths.
    Resolves once on first call; subsequent calls return cached dict.
    """
    global _DEDUP_PATHS_CACHE
    if _DEDUP_PATHS_CACHE is _UNRESOLVED:
        with _DEDUP_PATHS_LOCK:
            if _DEDUP_PATHS_CACHE is _UNRESOLVED:
                _DEDUP_PATHS_CACHE = resolve_dedup_paths()
    return cast(dict[str, Path], _DEDUP_PATHS_CACHE)

def reset_dedup_paths() -> None:
    """Reset singleton — testing only."""
    global _DEDUP_PATHS_CACHE
    with _DEDUP_PATHS_LOCK:
        _DEDUP_PATHS_CACHE = _UNRESOLVED

def get_sprint_parquet_dir(sprint_id: str) -> Path:
    """Return sprint Parquet directory, created if needed."""
    p = SPRINT_STORE_ROOT / sprint_id
    p.mkdir(parents=True, exist_ok=True)
    return p
IOC_DB_PATH: Path = SPRINT_STORE_ROOT.parent / 'ioc_graph.duckdb'
IOC_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
# SEC-02: chmod IOC_DB_PATH to 0o600 — DuckDB creates file on first write,
# we chmod immediately after so the file never exists with world-readable perms.
try:
    import os as _os_chmod
    import stat as _stat_chmod
    _os_chmod.chmod(IOC_DB_PATH, _stat_chmod.S_IRUSR | _stat_chmod.S_IWUSR)  # 0o600
    _os_chmod.chmod(IOC_DB_PATH.parent, _stat_chmod.S_IRUSR | _stat_chmod.S_IWUSR | _stat_chmod.S_IXUSR)  # 0o700
except Exception as _e:  # noqa: BLE001
    pass  # chmod may fail if user is not owner

class _Paths(msgspec.Struct, frozen=True, gc=False):
    """Immutable bundle of canonical runtime paths.

    `hledac_home` is the XDG-style user-data root (`~/.hledac`); the other
    fields mirror the module-level constants of the same name.
    """
    hledac_home: Path
    ramdisk_root: Path
    fallback_root: Path
    cache_root: Path
    db_root: Path
    lmdb_root: Path
    sprint_lmdb_root: Path
    evidence_root: Path
    keys_root: Path
    sprint_store_root: Path
    duckdb_store_root: Path
    lmdb_store_root: Path
    lancedb_store_root: Path
    ioc_db_path: Path
PATHS: _Paths = _Paths(hledac_home=Path.home() / '.hledac', ramdisk_root=RAMDISK_ROOT, fallback_root=FALLBACK_ROOT, cache_root=CACHE_ROOT, db_root=DB_ROOT, lmdb_root=LMDB_ROOT, sprint_lmdb_root=SPRINT_LMDB_ROOT, evidence_root=EVIDENCE_ROOT, keys_root=KEYS_ROOT, sprint_store_root=SPRINT_STORE_ROOT, duckdb_store_root=DUCKDB_STORE_ROOT, lmdb_store_root=LMDB_STORE_ROOT, lancedb_store_root=LANCEDB_STORE_ROOT, ioc_db_path=IOC_DB_PATH)

def get_ioc_db_path() -> pathlib.Path:
    """Vrátí cestu k persistentnímu DuckDB IOC store."""
    return IOC_DB_PATH
_SPRINT_LOCK_DIR: Path = Path.home() / '.hledac' / 'locks'

def get_sprint_lock_path(query: str) -> Path:
    """
    Return path to sprint-level file lock for a given query.

    Path semantics: ~/.hledac/locks/<query_hash>.lock
    where query_hash = MD5_hex(query)[:16]

    The lock file is acquired at sprint start and released at sprint end.
    If lock cannot be acquired within 5s, sys.exit(2) (config error).
    """
    import hashlib
    query_hash = hashlib.md5(query.encode()).hexdigest()[:16]
    lock_dir = _SPRINT_LOCK_DIR
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f'sprint_{query_hash}.lock'

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
    reports_dir = Path.home() / '.hledac' / 'reports'
    if reports_dir.exists() and (not reports_dir.is_dir()):
        reports_dir.rename(reports_dir.with_suffix('.bak.reports'))
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir / f'{sprint_id}.md'

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
    reports_dir = Path.home() / '.hledac' / 'reports'
    if reports_dir.exists() and (not reports_dir.is_dir()):
        reports_dir.rename(reports_dir.with_suffix('.bak.reports'))
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir / f'{sprint_id}_report.json'

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
    reports_dir = Path.home() / '.hledac' / 'reports'
    if reports_dir.exists() and (not reports_dir.is_dir()):
        reports_dir.rename(reports_dir.with_suffix('.bak.reports'))
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir / f'{sprint_id}_next_seeds.json'

def get_sprint_bundle_path(sprint_id: str) -> Path:
    """
    ISSUE [APEX]-1010: Canonical .hledac-sprint bundle path computation.

    Path semantics: ~/.hledac/bundles/{sprint_id}.hledac-sprint

    Returns
    -------
    Path
        Absolute path to sprint bundle archive.
    """
    bundles_dir = Path.home() / '.hledac' / 'bundles'
    bundles_dir.mkdir(parents=True, exist_ok=True)
    return bundles_dir / f'{sprint_id}.hledac-sprint'

def _ensure_dir(path: Path, mode: int | None=None) -> None:
    """Ensure directory exists, optionally with specific permissions."""
    if mode is not None:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)
    else:
        path.mkdir(parents=True, exist_ok=True)
for _dir in [DB_ROOT, LMDB_ROOT, SPRINT_LMDB_ROOT, EVIDENCE_ROOT, RUNS_ROOT, SOCKETS_ROOT, CACHE_ROOT, DUCKDB_STORE_ROOT, LMDB_STORE_ROOT, LANCEDB_STORE_ROOT]:
    _ensure_dir(_dir)
for _dir in [KEYS_ROOT, TOR_ROOT, NYM_ROOT, I2P_ROOT]:
    _ensure_dir(_dir, mode=448)

def assert_ramdisk_alive() -> None:
    """
    Verify RAMDISK_ROOT is still available.

    Raises RuntimeError if RAMDISK_ACTIVE was True at import-time but
    RAMDISK_ROOT is no longer a valid mount point.
    """
    if RAMDISK_ACTIVE and (not _is_active_ramdisk(RAMDISK_ROOT)):
        raise RuntimeError(f'[GHOST OPSEC] RAMDISK at {RAMDISK_ROOT} is no longer available. Cannot continue with OPSEC-degraded storage. Set GHOST_RAMDISK env var or mount /Volumes/ghost_tmp.')

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
    try:
        fallback.relative_to(Path.home())
    except ValueError:
        return
    try:
        if not any(fallback.iterdir()):
            shutil.rmtree(fallback, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass

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
    removed = 0
    if not lmdb_root.exists():
        return 0
    direct_lock = lmdb_root / 'lock.mdb'
    if direct_lock.is_file():
        try:
            direct_lock.unlink()
            removed += 1
        except OSError:  # noqa: BLE001
            pass
    try:
        for entry in lmdb_root.iterdir():
            if entry.is_dir():
                lock_file = entry / 'lock.mdb'
                if lock_file.is_file():
                    try:
                        lock_file.unlink()
                        removed += 1
                    except OSError:  # noqa: BLE001
                        pass
    except OSError:  # noqa: BLE001
        pass
    return removed


def compact_sprint_lmdb() -> dict[str, object]:
    """
    Compact the sprint unified LMDB store to reclaim space after bulk deletions.

    RES-04: LMDB copy-to-new-DB compaction pattern.
    - Copies all live data from sprint_unified.lmdb to a new temp env
    - Atomically swaps the data.mdb to reclaim dead pages
    - Updates LMDB statistics

    Also calls cleanup_stale_lmdb_locks to remove orphaned lock files.

    M1 8GB safe: compaction is done in a single pass with bounded memory.

    Returns:
        Dict with keys:
          - 'unified_compacted': bool — sprint_unified.lmdb compaction result
          - 'locks_removed': int — count of stale lock files removed
    """
    import logging

    _logger = logging.getLogger(__name__)
    results: dict[str, object] = {
        "unified_compacted": False,
        "locks_removed": 0,
    }

    # Step 1: Remove stale lock files first
    try:
        locks_removed = cleanup_stale_lmdb_locks(SPRINT_LMDB_ROOT)
        results["locks_removed"] = locks_removed
        _logger.debug("[LMDB-COMPACT] Removed %d stale lock files", locks_removed)
    except Exception as exc:
        _logger.debug("[LMDB-COMPACT] cleanup_stale_lmdb_locks failed: %s", exc)

    # Step 2: Compact the unified LMDB store (sprint_unified.lmdb)
    unified_path = SPRINT_LMDB_ROOT / "sprint_unified.lmdb"
    if unified_path.exists():
        try:
            from hledac.universal.knowledge.lmdb_subdb import open_unified_lmdb

            store = open_unified_lmdb(str(unified_path), lazy=False)
            success = store.compact_database()
            store.close()
            results["unified_compacted"] = success
            _logger.info(
                "[LMDB-COMPACT] sprint_unified.lmdb compaction: %s",
                "success" if success else "failed",
            )
        except Exception as exc:
            _logger.warning("[LMDB-COMPACT] sprint_unified.lmdb compact failed: %s", exc)

    return dict(results)  # ensure JSON-serializable for callers

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
        except Exception:  # noqa: BLE001
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
            if entry.suffix == '.sock' and entry.is_socket():
                if _is_socket_orphaned(entry):
                    try:
                        entry.unlink()
                        removed += 1
                    except OSError:  # noqa: BLE001
                        pass
    except OSError:  # noqa: BLE001
        pass
    return removed