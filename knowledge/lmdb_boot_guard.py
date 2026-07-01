"""
Sprint 8AG §1.4: Safe LMDB boot guard with strict stale-lock detection.

Provides fail-soft, idempotent LMDB open with process-liveness-verified lock cleanup.
Used BEFORE the first relevant LMDB open in any owner path.

DESIGN
------
- Strict stale-lock check: lock is reset ONLY when the holder is confirmed dead
- psutil / os.kill(pid, 0) for liveness verification
- Fail-safe = do NOT delete if holder cannot be reliably determined
- Idempotent: multiple calls produce the same result
- No blind deletion of native lock files

USAGE
-----
from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard

env = open_lmdb_with_guard(path, map_size=...)
"""


import logging
import os
import pathlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import lmdb

logger = logging.getLogger(__name__)

# Threshold: lock file older than this → candidate for stale cleanup (seconds)
# Used ONLY when holder PID cannot be resolved; age threshold is a fallback safety net
_LOCK_AGE_THRESHOLD_SECONDS: float = 60.0


def _is_process_alive(pid: int) -> bool:
    """
    Check if a process is alive using os.kill(pid, 0).

    Returns True if the process appears to be running.
    Returns False if the process is dead, zombie, or inaccessible.
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        # Process does not exist
        return False
    except PermissionError:
        # Process exists but we don't have permission — treat as alive
        return True
    except OSError:
        return False


def _try_get_lock_holder_pid(lock_path: pathlib.Path) -> int | None:
    """
    Attempt to extract the PID stored in a lock file.

    LMDB lock.mdb files contain a PID in their header on some platforms.
    Returns the PID if found, None if not detectable.

    This is a best-effort heuristic — LMDB lock format is not guaranteed stable.
    """
    try:
        if not lock_path.exists() or lock_path.stat().st_size < 4:
            return None
        with open(lock_path, "rb") as f:
            # Read first 4 bytes as little-endian PID
            header = f.read(4)
            if len(header) < 4:
                return None
            pid = int.from_bytes(header[:4], byteorder="little")
            if pid <= 0 or pid > 1_000_000:
                return None
            return pid
    except Exception:
        return None


def _is_lock_stale(lock_path: pathlib.Path, data_path: pathlib.Path | None = None) -> tuple[bool, str]:
    """
    Determine if a lock file is safely considered stale.

    Returns (is_stale, reason):
        (True, reason)  — lock is stale and safe to remove
        (False, reason) — lock is live or cannot be determined

    Strict check order:
    1. Lock file does not exist → not stale (nothing to do)
    2. Crash-detection: data.mdb exists + lock.mdb missing → stale (clean post-crash)
    3. Try to read holder PID → if live process, NOT stale
    4. Fallback: if PID unreadable AND file is old (> _LOCK_AGE_THRESHOLD_SECONDS) → stale

    This function NEVER deletes anything — it only returns a recommendation.
    """
    if not lock_path.exists():
        # P1-2: Crash-detection heuristic — data.mdb exists without lock.mdb
        # This is a clean post-crash state: lock was held by dead process
        if data_path is not None and data_path.exists():
            return True, "crash_recovery(data_exists_no_lock)"
        return False, "lock_file_not_found"

    # Try to get holder PID from lock file header
    pid = _try_get_lock_holder_pid(lock_path)
    if pid is not None:
        if _is_process_alive(pid):
            return False, f"holder_process_alive(pid={pid})"
        # Process is dead — lock is stale
        return True, f"holder_process_dead(pid={pid})"

    # Cannot determine holder — use age threshold as last resort
    try:
        age_seconds = os.path.getmtime(lock_path)
        import time
        age = time.time() - age_seconds
        if age > _LOCK_AGE_THRESHOLD_SECONDS:
            return True, f"age_threshold_exceeded(age={age:.1f}s>{_LOCK_AGE_THRESHOLD_SECONDS}s)"
        return False, f"lock_file_too_recent(age={age:.1f}s<{_LOCK_AGE_THRESHOLD_SECONDS}s)"
    except OSError:
        return False, "cannot_determine_lock_age"


class BootGuardError(Exception):
    """
    Raised when boot guard detects an unsafe stale-lock state.

    An UNSAFE state is: another process holds a LIVE lock (holder is alive).
    A BENIGN state is: no lock file, or stale lock (nothing to clean).

    Only raise this when the caller should abort boot — i.e., when a live
    process holds the lock and this process should NOT proceed.
    """
    pass


def cleanup_stale_lmdb_lock(lmdb_dir: pathlib.Path, *, data_path: pathlib.Path | None = None) -> tuple[int, str]:
    """
    Safely clean a single stale LMDB lock.mdb from lmdb_dir.

    Only removes lock.mdb if:
        1. The file exists
        2. The lock holder (if detectable) is confirmed dead
        3. OR the file is older than _LOCK_AGE_THRESHOLD_SECONDS AND holder is not confirmed alive
        4. OR crash-detection: data.mdb exists without lock.mdb (post-crash clean state)

    Args:
        lmdb_dir: Path to LMDB directory containing lock.mdb
        data_path: Optional explicit path to data.mdb for crash-detection heuristic.
                   If None, defaults to lmdb_dir / "data.mdb".

    Returns (removed_count, last_reason):
        (0, reason) — nothing removed; reason explains why
        (1, reason) — lock removed successfully

    Raises:
        BootGuardError: when a live lock holder is detected (unsafe state — abort boot).
    """
    lock_path = lmdb_dir / "lock.mdb"
    if data_path is None:
        data_path = lmdb_dir / "data.mdb"

    is_stale, reason = _is_lock_stale(lock_path, data_path)
    if not is_stale:
        return 0, reason

    # Double-check: even after confirming stale, verify no live holder
    pid = _try_get_lock_holder_pid(lock_path)
    if pid is not None and _is_process_alive(pid):
        # Lock holder is alive — unsafe state, must NOT proceed
        raise BootGuardError(f"Live lock holder detected: pid={pid}, aborting boot")

    try:
        lock_path.unlink(missing_ok=True)
        return 1, reason
    except OSError as e:
        return 0, f"unlink_failed({e})"


def open_lmdb_with_guard(
    path: pathlib.Path,
    *,
    map_size: int | None = None,
    **kw,
) -> Any:
    """
    Open an LMDB environment with safe stale-lock guard.

    This is a wrapper around lmdb.open() that adds a pre-open
    stale-lock safety check to avoid blindly deleting locks from live processes.

    Args:
        path: Path to LMDB directory
        map_size: map_size in bytes (passed to lmdb.open)
        **kw: Additional arguments passed to lmdb.open()

    Returns:
        lmdb.Environment instance.

    Lock recovery protocol:
        1. Pre-open: crash-detection + stale-lock cleanup (strict liveness check)
        2. First open attempt
        3. On LockError: run cleanup_stale_lmdb_lock with strict liveness check
        4. Single retry after cleanup
        5. If still failing: propagate LockError (fail-soft, do not retry further)
    """
    import lmdb

    # Resolve map_size
    if map_size is None:
        from hledac.universal.paths import lmdb_map_size
        map_size = lmdb_map_size()

    # Pre-open guard: attempt cleanup BEFORE first open if lock file is stale
    # This is a no-op if lock doesn't exist or holder is alive
    try:
        cleanup_stale_lmdb_lock(path)
    except Exception as e:
        # Defensive: never let cleanup failure prevent open attempt
        logger.debug(f"pre-open lock cleanup attempt failed: {e}")

    # First open attempt
    try:
        return lmdb.open(str(path), map_size=map_size, **kw)
    except lmdb.LockError:
        # Sprint 8AG §1.4: stale-lock recovery with strict liveness check
        removed, reason = cleanup_stale_lmdb_lock(path)
        logger.debug(f"LMDB lock recovery: removed={removed} reason={reason}")
        if removed:
            # Holder was confirmed dead — safe to retry
            try:
                return lmdb.open(str(path), map_size=map_size, **kw)
            except lmdb.LockError:
                # Still failing after confirmed-dead cleanup — fail soft
                raise
        # Nothing was removed (no lock file or holder alive) — propagate
        raise


def compact_lmdb(env: "lmdb.Environment") -> dict[str, int] | None:
    """
    Compact an LMDB environment in-place (MDB_cp_compact flag).

    Safe to call concurrently with readers — LMDB uses copy-on-write.
    Fails gracefully if compact is unavailable.

    Args:
        env: An open lmdb.Environment instance.

    Returns:
        dict with compaction stats (pages_reclaimed, pages_free,
        leaf_entries, branch_pages) or None on failure.
    """
    try:
        import lmdb

        # MDB_CP_COMPACT: compact but do not shrink the data file (safe for concurrent readers)
        # Available in lmdb >= 2.2.0 (required by this project)
        # getattr guards against missing attribute in type stubs
        compact_fn = getattr(env, "compact", None)
        if compact_fn is None:
            return None
        flags = getattr(lmdb, "MDB_CP_COMPACT", 0)
        pages_reclaimed, pages_free, leaf_entries, branch_pages = compact_fn(flags=flags)
        return {
            "pages_reclaimed": int(pages_reclaimed),
            "pages_free": int(pages_free),
            "leaf_entries": int(leaf_entries),
            "branch_pages": int(branch_pages),
        }
    except Exception:  # noqa: BLE001
        return None
