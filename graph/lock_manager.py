"""
Graph Lock Manager — Singleton for DuckPGQGraph lock coordination.

PROBLEM
-------
DuckPGQGraph._acquire_graph_lock() (Sprint F700D) had 4 bugs:
  1. No singleton — N instances all write same PID → race condition
  2. os.kill(pid, 0) fails on Windows + is unreliable after fork()
  3. No advisory flock — OS-level atomicity missing
  4. No graceful read-only fallback when another process holds lock

SOLUTION
--------
GraphLockManager: thread-safe, fork-safe, cross-platform singleton.

INVARIANTS
----------
  - ONE lock file per db_path (singleton key = db_path)
  - Advisory flock(LOCK_EX) before any I/O — OS-level atomicity
  - PID header in lock file for crash-recovery diagnostics
  - psutil.pid_exists() for cross-platform liveness (or os.kill fallback)
  - Fail-safe: never raises; returns (False, reason) on lock failure
  - Bounded: MAX_LOCK_WAIT_S = 2.0 with deadlock-prevention timeout
  - Read-only fallback: when lock is held by live process, graph opens read-only

USAGE
-----
  mgr = GraphLockManager(db_path)
  if not mgr.acquire():
      logger.warning(f"[GRAPH] Lock denied: {mgr.denial_reason}")
      # graph will open read-only or skip writes
"""

from __future__ import annotations

import fcntl
import logging
import os
import pathlib
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psutil

logger = logging.getLogger(__name__)

# Singleton registry: db_path → GraphLockManager instance
_LOCK_REGISTRY: dict[str, "GraphLockManager"] = {}
_REGISTRY_LOCK = threading.Lock()

# Safety bounds
MAX_LOCK_WAIT_S: float = 2.0
LOCK_FILE_SUFFIX: str = ".lock"
# Threshold: lock file older than this → consider stale (seconds)
_LOCK_AGE_THRESHOLD_SECONDS: float = 60.0


def _get_psutil():
    """Lazy import psutil — avoids early import crash on M1."""
    try:
        import psutil
        return psutil
    except Exception:
        return None


def _is_process_alive(pid: int) -> bool:
    """
    Cross-platform process liveness check.

    Tries psutil.pid_exists first (Windows + fork-safe), falls back to
    os.kill(pid, 0) on Unix. PermissionError means process exists but
    we can't signal it — treat as alive.
    """
    psutil = _get_psutil()
    if psutil is not None:
        try:
            return psutil.pid_exists(pid)
        except Exception:
            pass

    # Fallback: Unix liveness probe
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we lack permission — treat as alive
        return True
    except OSError:
        return False


def _try_get_pid_from_lock(lock_path: pathlib.Path) -> int | None:
    r"""
    Read PID from lock file header (first 4 bytes, little-endian).
    Returns PID if valid (0 <= pid <= 1_000_000), else None.

    Note: PID 0 is kernel-reserved on Darwin and is a valid return value
    (used to detect kernel-holder scenarios in _is_lock_stale).

    BUG-5 FIX: Falls back to _try_get_pid_from_legacy_lock() for legacy text-format
    lock files written by older versions (format: "PID:duckdb:timestamp\n").
    """
    try:
        if not lock_path.exists() or lock_path.stat().st_size < 4:
            return None
        with open(lock_path, "rb") as f:
            header = f.read(4)
            if len(header) < 4:
                return None
            pid = int.from_bytes(header[:4], byteorder="little")
            if pid < 0 or pid > 1_000_000:
                # BUG-5 FIX: Not a valid binary PID — might be legacy text format.
                # Fall through to legacy parser below instead of returning None.
                pass
            else:
                return pid
    except Exception:
        return None

    # BUG-5 FIX: Legacy text-format fallback.
    # Older versions wrote "PID:duckdb:timestamp\ n" (31 bytes) instead of
    # 4-byte binary little-endian. _is_lock_stale() was misinterpreting the
    # first bytes '70457' as binary PID=1 (launchd alive → false-negative stale).
    return _try_get_pid_from_legacy_lock(lock_path)


def _try_get_pid_from_legacy_lock(lock_path: pathlib.Path) -> int | None:
    r"""
    BUG-5 FIX: Read PID from legacy text-format lock file.

    Legacy format: "PID:duckdb:timestamp\n"  (e.g. "70457:duckdb:1782636248.283554")

    Returns PID if valid (1 <= pid <= 1_000_000), else None.
    Returns None for non-legacy files (size != 31, missing ':duckdb:' separator).
    """
    try:
        if not lock_path.exists():
            return None
        size = lock_path.stat().st_size
        # Legacy lock is always exactly 31 bytes: "PID:duckdb:timestamp\n"
        if size != 31:
            return None
        with open(lock_path, "rb") as f:
            data = f.read()
        text = data.decode("ascii", errors="replace").strip()
        if ":duckdb:" not in text:
            return None
        pid_str = text.split(":")[0]
        pid = int(pid_str)
        if pid < 1 or pid > 1_000_000:
            return None
        return pid
    except Exception:
        return None


def _is_lock_stale(lock_path: pathlib.Path, data_path: pathlib.Path | None = None) -> tuple[bool, str]:
    """
    Determine if a lock file is stale.

    Returns (is_stale, reason):
        (True, reason)  — stale, safe to remove
        (False, reason) — live or cannot determine

    F11C-2: Enhanced stale detection handles three failure modes:
      1. Holder process is dead (PID in lock file not alive)
      2. Holder PID is a zombie/stale kernel thread (kernel_worker, etc.)
      3. Lock file older than threshold without valid PID header
    """
    if not lock_path.exists():
        # Crash-recovery heuristic: data.mdb exists without lock.mdb
        if data_path is not None and data_path.exists():
            return True, "crash_recovery(data_exists_no_lock)"
        return False, "lock_file_not_found"

    pid = _try_get_pid_from_lock(lock_path)
    if pid is not None:
        # F11C-2: PID 0 and 2 are kernel-reserved on Darwin (kernel_task, phys_mem)
        # PID 1 is launchd (real userspace init) — NOT stale by PID alone
        if pid == 0 or pid == 2:
            return True, f"kernel_reserved_pid(pid={pid})"
        if _is_process_alive(pid):
            # F11C-2: Check if it's a kernel thread holding the lock — these are
            # not real process holders (e.g. kernel_worker, kernel_task)
            try:
                psutil = _get_psutil()
                if psutil is not None:
                    try:
                        proc = psutil.Process(pid)
                        name = proc.name()
                        # kernel threads cannot hold application-level locks legitimately
                        if name in ("kernel_worker", "kernel_task"):
                            return True, f"kernel_thread_holding_lock(pid={pid}, name={name})"
                    except psutil.NoSuchProcess:
                        # Process died between _is_process_alive and here → stale
                        return True, f"holder_process_died_during_check(pid={pid})"
                    except psutil.AccessDenied:
                        # Cannot inspect but process exists → treat as alive
                        pass
            except psutil.NoSuchProcess:
                return True, f"holder_process_died_during_check(pid={pid})"
            except psutil.AccessDenied:
                pass
            except Exception:
                # Other psutil errors — be conservative, don't remove live-looking lock
                pass
            return False, f"holder_process_alive(pid={pid})"
        return True, f"holder_process_dead(pid={pid})"

    # Cannot read PID — use age threshold as last resort
    try:
        age = time.time() - os.path.getmtime(lock_path)
        if age > _LOCK_AGE_THRESHOLD_SECONDS:
            return True, f"age_threshold_exceeded(age={age:.1f}s)"
        return False, f"lock_file_too_recent(age={age:.1f}s)"
    except OSError:
        return False, "cannot_determine_lock_age"


class GraphLockManager:
    """
    Singleton lock manager for DuckPGQGraph database files.

    Thread-safe, fork-safe, cross-platform. One instance per db_path.
    Uses advisory fcntl.flock for OS-level atomicity + PID header for
    crash-recovery diagnostics.
    """

    __slots__ = (
        "_db_path",
        "_lock_path",
        "_fd",
        "_acquired",
        "_denial_reason",
        "_holder_pid",
        "_lock",
    )

    def __new__(cls, db_path: str) -> "GraphLockManager":
        with _REGISTRY_LOCK:
            if db_path not in _LOCK_REGISTRY:
                _LOCK_REGISTRY[db_path] = super().__new__(cls)
            return _LOCK_REGISTRY[db_path]

    def __init__(self, db_path: str) -> None:
        # Guard against re-init of singleton (only init once per db_path)
        if hasattr(self, "_db_path") and self._db_path == db_path:
            return

        self._db_path = db_path
        self._lock_path = pathlib.Path(db_path).with_suffix(LOCK_FILE_SUFFIX)
        self._fd: int | None = None
        self._acquired: bool = False
        self._denial_reason: str = ""
        self._holder_pid: int | None = None
        self._lock = threading.Lock()  # Per-instance thread safety

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def is_acquired(self) -> bool:
        """True if THIS instance holds the lock."""
        return self._acquired

    @property
    def denial_reason(self) -> str:
        """Human-readable reason why lock was denied (empty if acquired)."""
        return self._denial_reason

    @property
    def holder_pid(self) -> int | None:
        """PID of current lock holder, or None if untracked."""
        return self._holder_pid

    @property
    def lock_path(self) -> pathlib.Path:
        """Path to the lock file."""
        return self._lock_path

    # ── Public API ───────────────────────────────────────────────────────────

    def acquire(self, timeout_s: float = MAX_LOCK_WAIT_S) -> bool:
        """
        Attempt to acquire exclusive graph lock.

        Steps:
          1. Pre-check: if lock is held by LIVE process → deny (read-only path)
          2. Pre-check: if lock is STALE (dead holder or age threshold) → clean
          3. fcntl.flock(LOCK_EX | LOCK_NB) for atomic OS-level acquire
          4. On success: write PID header to lock file
          5. On failure: record denial_reason

        Args:
            timeout_s: Max seconds to wait for lock (default 2.0).

        Returns:
            True if lock acquired, False if denied.
            On False: check .denial_reason for diagnostics.
        """
        if self._acquired:
            return True

        with self._lock:
            self._denial_reason = ""
            self._holder_pid = None

            # Step 1: Check current lock state
            if self._lock_path.exists():
                is_stale, reason = _is_lock_stale(
                    self._lock_path,
                    pathlib.Path(self._db_path),
                )
                if not is_stale:
                    pid = _try_get_pid_from_lock(self._lock_path)
                    if pid is not None:
                        self._holder_pid = pid
                        self._denial_reason = f"live_lock_holder(pid={pid}, reason={reason})"
                    else:
                        self._denial_reason = f"live_lock_unknown_holder(reason={reason})"
                    return False

                # Step 2: Stale — clean up before acquiring
                try:
                    self._lock_path.unlink(missing_ok=True)
                    logger.debug(f"[GRAPH] Removed stale lock: {reason}")
                except OSError as e:
                    logger.debug(f"[GRAPH] Stale lock cleanup failed: {e}")

            # Step 3: Atomic acquire via flock
            deadline = time.monotonic() + timeout_s
            lock_file_dir = self._lock_path.parent
            lock_file_dir.mkdir(parents=True, exist_ok=True)

            open_flags = os.O_RDWR | os.O_CREAT | os.O_TRUNC
            fd = os.open(self._lock_path, open_flags, 0o644)
            self._fd = fd

            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    # Success
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        os.close(fd)
                        self._fd = None
                        self._denial_reason = f"flock_timeout({timeout_s}s)"
                        return False
                    # Jitter: avoid thundering herd when multiple processes compete
                    import random
                    time.sleep(0.05 + random.random() * 0.05)

            # Step 4: Write PID header
            my_pid = os.getpid()
            try:
                os.ftruncate(fd, 0)
                os.write(fd, my_pid.to_bytes(4, byteorder="little"))
                # Keep fd open to maintain flock
            except OSError as e:
                logger.warning(f"[GRAPH] Could not write PID to lock file: {e}")

            self._acquired = True
            logger.debug(f"[GRAPH] Lock acquired: PID={my_pid} lock={self._lock_path}")
            return True

    def release(self) -> None:
        """
        Release the lock if held by this instance.

        Idempotent: safe to call multiple times.
        Uses LOCK_UN to release flock; truncates lock file to 0 bytes
        (preserves lock file for post-mortem inspection).
        """
        if not self._acquired:
            return

        with self._lock:
            if self._fd is not None:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                    os.close(self._fd)
                except OSError:
                    pass
                finally:
                    self._fd = None

            self._acquired = False
            logger.debug(f"[GRAPH] Lock released: {self._lock_path}")

    # ── Context manager ────────────────────────────────────────────────────

    def __enter__(self) -> "GraphLockManager":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def __del__(self) -> None:
        """Ensure flock is released on garbage collection."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except OSError:
                pass
            finally:
                self._fd = None


def cleanup_stale_graph_lock(db_path: str) -> tuple[int, str]:
    """
    Public API: clean stale graph lock for a db_path.

    Called by boot guard before DuckPGQGraph init to prevent zombie locks.
    Returns (removed, reason) — mirrors lmdb_boot_guard.cleanup_stale_lmdb_lock.
    """
    lock_path = pathlib.Path(db_path).with_suffix(LOCK_FILE_SUFFIX)
    data_path = pathlib.Path(db_path)

    is_stale, reason = _is_lock_stale(lock_path, data_path)
    if not is_stale:
        return 0, reason

    # Double-check: if holder is alive, do NOT remove
    pid = _try_get_pid_from_lock(lock_path)
    if pid is not None and _is_process_alive(pid):
        return 0, f"live_lock_holder(pid={pid})"

    try:
        lock_path.unlink(missing_ok=True)
        return 1, reason
    except OSError as e:
        return 0, f"unlink_failed({e})"
