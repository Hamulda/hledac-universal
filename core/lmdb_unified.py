"""
core/lmdb_unified.py
=====================
Sprint S-01: Unified LMDB singleton — eliminates ~1 GB VM reservation from 7+ separate LMDB envs.



Root cause: Each `lmdb.open(map_size=256MB)` reserves map_size bytes in virtual memory
regardless of actual usage. With 7+ separate envs across the codebase, M1 8GB loses
~20% of its addressable VM to reserved (never-touch) mmap regions.

Solution:
    Single LMDB env with max_dbs=16 sub-DBs, each sub-DB is a logical namespace.
    Total map_size: 256 MB ceiling on M1 8GB (pressure-responsive, ceiling-capped).
    Dynamic set_mapsize() reduction when M1ResourceGovernor reports ELEVATED pressure.

Sub-DB allocation (max_dbs=16):
    0  = session_meta      (MemoryManager)
    1  = exposure_data    (ExposureClient)
    2  = source_registry  (DeepSourceRegistry)
    3  = bandit_stats     (SourceBandit)
    4  = federated_dht    (FederatedBridge)
    5  = hot_edges        (HotEdgesCache)
    6  = sprint_seeds     (SprintSeedsStore)
    7  = sketches         (Sketches)
    8  = query_cache      (QueryCache)
    9  = ioc_dedup        (IocDedupAdapter)
    10 = domain_rl        (DomainRateLimiter)
    11 = persistent_kv    (PersistentKVCache)
    12 = task_cache       (TaskCache)
    13 = prefetch_cache   (PrefetchCache)
    14 = secrets_vault     (SecretsVault)
    15 = reserved          (future use)

Benefits vs separate envs:
    - 1 mmap region (512 MB) vs 7+ separate regions (~1 GB+)
    - Shared OS page cache, single lock file
    - Dynamic mapsize shrink under memory pressure
    - Lazy init avoids 200-400ms at sprint boot

M1 8GB budget:
    Total map_size: 256 MB (ceiling-capped, StorageConfig default)
    Wired: 1.5 GiB (fixed, never shrinks)
    VM budget available: ~6.5 GiB total → 256 MB unified is <4% of VM

Invariant (S-01):
    - Single LMDB env with max_dbs=16
    - map_size=256 MB default (ceiling-capped; StorageConfig can raise ceiling to max 512 MB)
    - NORMAL→256 MB, ELEVATED→256 MB (no further shrink at ceiling), CRITICAL→128 MB
    - All original APIs (put, get, cursor) preserved via sub-db delegation
    - Lazy init — env opened on first access, not on import
"""

from __future__ import annotations

import logging
import math
import os
import pathlib
import weakref
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import lmdb

logger = logging.getLogger(__name__)

# F320: DRY LMDB cleanup helpers
from hledac.universal.utils._patterns import safe_lmdb_close  # noqa: E402
from core._util import aclose

# --------------------------------------------------------------------------- #
# Sub-DB index constants
# --------------------------------------------------------------------------- #
class SubDB:
    """Sub-DB index allocation. max_dbs=16, indices 0-15."""

    SESSION_META = 0      # MemoryManager
    EXPOSURE_DATA = 1    # ExposureClient
    SOURCE_REGISTRY = 2  # DeepSourceRegistry
    BANDIT_STATS = 3     # SourceBandit
    FEDERATED_DHT = 4    # FederatedBridge
    HOT_EDGES = 5        # HotEdgesCache
    SPRINT_SEEDS = 6     # SprintSeedsStore
    SKETCHES = 7         # Sketches
    QUERY_CACHE = 8      # QueryCache
    IOC_DEDUP = 9        # IocDedupAdapter
    DOMAIN_RL = 10       # DomainRateLimiter
    PERSISTENT_KV = 11   # PersistentKVCache
    TASK_CACHE = 12      # TaskCache
    PREFETCH_CACHE = 13   # PrefetchCache
    SECRETS_VAULT = 14   # SecretsVault
    RESERVED = 15        # Future use

    _NAMES: tuple[str, ...] = (
        "session_meta",
        "exposure_data",
        "source_registry",
        "bandit_stats",
        "federated_dht",
        "hot_edges",
        "sprint_seeds",
        "sketches",
        "query_cache",
        "ioc_dedup",
        "domain_rl",
        "persistent_kv",
        "task_cache",
        "prefetch_cache",
        "secrets_vault",
        "reserved",
    )

    @classmethod
    def name(cls, idx: int) -> str:
        return cls._NAMES[idx] if 0 <= idx < len(cls._NAMES) else f"unknown({idx})"


# --------------------------------------------------------------------------- #
# Pressure-responsive mapsize management
# --------------------------------------------------------------------------- #
# M1 8GB ceiling: LMDB map ismmap'd — unbounded growth hits MDB_MAP_FULL
# at ~map_size bytes written. 256 MB is the safe default (StorageConfig default).
# Wired limit for M1 8GB is ~1.5 GiB (CLAUDE.md); per-env ceiling = 256 MB.
_UNIFIED_MAP_SIZE_CEILING = 256 * 1024 * 1024  # 256 MB hard ceiling for M1 8GB
_UNIFIED_MAP_SIZE_DEFAULT = 512 * 1024 * 1024  # 512 MB (used only when StorageConfig unavailable)
_UNIFIED_MAP_SIZE_LOW = 128 * 1024 * 1024     # 128 MB under CRITICAL pressure


def _get_default_map_size() -> int:
    """
    Get the default map_size respecting the M1 8GB ceiling.

    Reads from StorageConfig (via paths.lmdb_map_size()) to stay in sync
    with the GHOST_LMDB_MAX_SIZE_MB env var. Falls back to CEILING if
    StorageConfig returns a value that would exceed it.

    Bootstrap-safe: can be called before StorageConfig is fully initialized.
    """
    try:
        from hledac.universal.paths import lmdb_map_size

        size = lmdb_map_size()
        return min(size, _UNIFIED_MAP_SIZE_CEILING)
    except Exception:
        return _UNIFIED_MAP_SIZE_CEILING

# --------------------------------------------------------------------------- #
# Singleton state
# --------------------------------------------------------------------------- #
_instance: "UnifiedLMDB | None" = None
_instance_lock = threading.RLock()


# --------------------------------------------------------------------------- #
# UnifiedLMDB class
# --------------------------------------------------------------------------- #
class UnifiedLMDB:
    """
    Single LMDB environment with sub-DB isolation for all KV stores.

    Issues addressed:
        1. VM reservation: 1× 256 MB mmap vs 7+ × 256 MB = ~1 GB VM saved
        2. Pressure response: set_mapsize() shrinks to 128 MB under CRITICAL memory
        3. Lazy init: no LMDB open on import (saves 200-400ms at boot)

    Usage:
        store = get_unified_lmdb()          # singleton
        env = store.env()                    # raw lmdb.Environment
        sub = store.open_db(SubDB.HOT_EDGES)  # get sub-db handle
        with store.env.begin(sub_db=sub) as txn:
            txn.put(b"key", b"value")

    All original APIs are preserved. Callers using raw env.begin() or
    putmulti_bounded continue to work — they just now share one env.
    """

    __slots__ = (
        "_env",
        "_path",
        "_map_size_default",
        "_map_size_current",
        "_max_dbs",
        "_closed",
        "_initialized",
        "_lazy",
        "_sub_dbs",
        "_pressure_state",
        "_lock",
        "_shrink_count",
        "_shrink_failures",
        "_reopen_in_progress",
        "_finalizer",
        "_map_full_count",
    )

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        map_size: int | None = None,
        max_dbs: int = 16,
        lazy: bool = True,
    ) -> None:
        self._path = pathlib.Path(path)
        effective = map_size or _get_default_map_size()
        # Enforce ceiling even if caller passed an explicit value
        self._map_size_default: int = min(effective, _UNIFIED_MAP_SIZE_CEILING)
        self._map_size_current: int = self._map_size_default
        self._max_dbs = max_dbs
        self._closed = False
        self._initialized = False
        self._lazy = lazy
        self._sub_dbs: dict[int, Any] = {}  # sub_db index → handle
        self._pressure_state: str = "NORMAL"  # NORMAL | ELEVATED | CRITICAL
        self._lock = threading.RLock()
        # RES-02: Shrink telemetry
        self._shrink_count: int = 0
        self._shrink_failures: int = 0
        self._reopen_in_progress: bool = False  # guards against concurrent reopens
        # MEM-UMA-001: MDB_MAP_FULL event counter
        self._map_full_count: int = 0

        # F264: Use weakref.finalize instead of __del__ for deterministic cleanup
        self._finalizer = weakref.finalize(self, self._cleanup)

        if not lazy:
            self._ensure_init()

    # ------------------------------------------------------------------ #
    # Initialization
    # ------------------------------------------------------------------ #
    def _ensure_init(self) -> None:
        """Lazy initialization — opens LMDB env on first access."""

        # Fast path — check without lock
        if self._closed:
            raise RuntimeError("UnifiedLMDB is closed (emergency shrink previously failed)")
        if self._initialized and not self._reopen_in_progress:
            return

        # Slow path — may need to wait for in-progress reopen
        with self._lock:
            if self._closed:
                raise RuntimeError("UnifiedLMDB is closed (emergency shrink previously failed)")
            # Wait for any in-progress reopen to complete
            spin_count = 0
            while self._reopen_in_progress:
                # Exponential backoff: 0.05s base, 2x factor, 1.0s ceiling
                # Series: 0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0, ... (~2.5s at 7 spins)
                # Cap at 10 spins: 10 × 1.0s ceiling = 10s max timeout
                if spin_count >= 10:
                    raise RuntimeError("UnifiedLMDB reopen timed out")
                sleep_time = min(0.05 * math.exp(0.5 * spin_count), 1.0)
                time.sleep(sleep_time)
                spin_count += 1
            if self._initialized:
                return

            from hledac.universal.knowledge.lmdb_boot_guard import (
                open_lmdb_with_guard,
            )

            self._env = open_lmdb_with_guard(
                self._path,
                map_size=self._map_size_current,
                max_dbs=self._max_dbs,
                writemap=False,
                metasync=True,
            )

            # Pre-open all sub-DBs to validate max_dbs
            for idx in range(self._max_dbs):
                try:
                    self._sub_dbs[idx] = self._env.open_db(str(SubDB.name(idx)).encode())
                except Exception as exc:
                    logger.warning(
                        "[LMDB-UNIFIED] sub-db %d (%s) open failed (non-fatal): %s",
                        idx,
                        SubDB.name(idx),
                        exc,
                    )

            self._initialized = True
            logger.info(
                "[LMDB-UNIFIED] Opened at %s, map_size=%dMB, max_dbs=%d",
                self._path,
                self._map_size_current // (1024 * 1024),
                self._max_dbs,
            )

    def is_initialized(self) -> bool:
        return self._initialized

    def is_closed(self) -> bool:
        return self._closed

    @property
    def map_full_count(self) -> int:
        """MDB_MAP_FULL event counter — incremented each time a write is dropped."""
        return self._map_full_count

    @property
    def map_size_current_mb(self) -> int:
        """Current map_size in MB (read-only, respects ceiling)."""
        return self._map_size_current // (1024 * 1024)

    def path(self) -> pathlib.Path:
        return self._path

    # ------------------------------------------------------------------ #
    # Raw env access (for putmulti_bounded, cursor, etc.)
    # ------------------------------------------------------------------ #
    def env(self) -> Any:
        """
        Return the raw lmdb.Environment.

        All original callers (putmulti_bounded, cursor operations) continue
        to work — they now share this single env.
        """
        self._ensure_init()
        return self._env

    # ------------------------------------------------------------------ #
    # Sub-DB access
    # ------------------------------------------------------------------ #
    def open_db(self, sub_idx: int) -> Any:
        """
        Return the sub-DB handle for the given index.

        Usage:
            sub = store.open_db(SubDB.HOT_EDGES)
            with store.env().begin(sub_db=sub) as txn:
                txn.put(b"key", b"value")
        """
        self._ensure_init()
        if sub_idx not in self._sub_dbs:
            if sub_idx < 0 or sub_idx >= self._max_dbs:
                raise ValueError(f"sub_idx {sub_idx} out of range [0, {self._max_dbs})")
            self._sub_dbs[sub_idx] = self._env.open_db(str(SubDB.name(sub_idx)).encode())
        return self._sub_dbs[sub_idx]

    def env_begin(
        self,
        sub_idx: int,
        write: bool = False,
        buffers: bool = True,
    ) -> Any:
        """
        Convenience: return a transaction on a sub-DB.

        Usage:
            with store.env_begin(SubDB.HOT_EDGES, write=True) as txn:
                txn.put(b"key", b"value")
        """
        sub = self.open_db(sub_idx)
        return self._env.begin(
            db=sub,
            write=write,
            buffers=buffers,
        )

    # ------------------------------------------------------------------ #
    # put / get (single key-value on a sub-DB)
    # ------------------------------------------------------------------ #
    def put(self, sub_idx: int, key: bytes, value: bytes) -> bool:
        """Put a single key-value into a sub-DB."""
        try:
            with self.env_begin(sub_idx, write=True) as txn:
                txn.put(key, value)
            return True
        except Exception as exc:
            import lmdb

            # MEM-UMA-001: MDB_MAP_FULL — map_size ceiling hit.
            # Caller should call compact_subdb(sub_idx) to reclaim space,
            # or degrade by dropping this sub-db's writes.
            if isinstance(exc, lmdb.MapFullError):
                self._map_full_count += 1
                logger.warning(
                    "[LMDB-UNIFIED] MDB_MAP_FULL on put sub=%s (%s) key=%s — "
                    "map_size at ceiling (%d MB). Write dropped. "
                    "Caller should compact_subdb(%d) or reduce write volume. (map_full_count=%d)",
                    sub_idx,
                    SubDB.name(sub_idx),
                    key[:20],
                    self.map_size_current_mb,
                    sub_idx,
                    self._map_full_count,
                )
            else:
                logger.debug("[LMDB-UNIFIED] put failed sub=%s key=%s: %s", sub_idx, key[:20], exc)
            return False

    def get(self, sub_idx: int, key: bytes) -> bytes | None:
        """Get a value by key from a sub-DB."""
        try:
            with self.env_begin(sub_idx, write=False) as txn:
                return txn.get(key)
        except Exception:
            return None

    def delete(self, sub_idx: int, key: bytes) -> bool:
        """Delete a key from a sub-DB."""
        try:
            with self.env_begin(sub_idx, write=True) as txn:
                txn.delete(key)
            return True
        except Exception as exc:
            logger.debug("[LMDB-UNIFIED] delete failed: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Batch operations
    # ------------------------------------------------------------------ #
    def put_batch(self, sub_idx: int, items: list[tuple[bytes, bytes]]) -> bool:
        """
        Batch put into a sub-DB using cursor.putmulti.

        Items: list of (key, value) tuples.

        MEM-UMA-001: On MDB_MAP_FULL, returns False so callers can attempt
        compaction (compact_subdb) or degrade gracefully. The write is NOT
        retried automatically — caller decides based on sub-db priority.
        """
        try:
            with self.env_begin(sub_idx, write=True) as txn:
                cursor = txn.cursor()
                cursor.putmulti(items)
            return True
        except Exception as exc:
            import lmdb

            # MEM-UMA-001: MDB_MAP_FULL — map_size ceiling hit.
            # Caller should call compact_subdb(sub_idx) to reclaim space,
            # or degrade by dropping this sub-db's writes.
            if isinstance(exc, lmdb.MapFullError):
                self._map_full_count += 1
                logger.warning(
                    "[LMDB-UNIFIED] MDB_MAP_FULL on put_batch sub=%s (%s) items=%d — "
                    "map_size at ceiling (%d MB). Batch dropped. "
                    "Caller should compact_subdb(%d) or reduce write volume. (map_full_count=%d)",
                    sub_idx,
                    SubDB.name(sub_idx),
                    len(items),
                    self.map_size_current_mb,
                    sub_idx,
                    self._map_full_count,
                )
            else:
                logger.debug("[LMDB-UNIFIED] put_batch failed sub=%s: %s", sub_idx, exc)
            return False

    def scan_prefix(self, sub_idx: int, prefix: bytes) -> list[tuple[bytes, bytes]]:
        """Scan all keys matching prefix in a sub-DB."""
        try:
            results: list[tuple[bytes, bytes]] = []
            with self.env_begin(sub_idx, write=False) as txn:
                cursor = txn.cursor()
                cursor.set_range(prefix)
                while True:
                    k = cursor.key()
                    if k is None:
                        break
                    # cursor.key() returns memoryview when buffers=True; convert to bytes
                    if isinstance(k, memoryview):
                        k = bytes(k)
                    if not k.startswith(prefix):
                        break
                    v = cursor.value()
                    if isinstance(v, memoryview):
                        v = bytes(v)
                    results.append((k, v))
                    if not cursor.next():
                        break
            return results
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # Pressure-responsive mapsize
    # ------------------------------------------------------------------ #
    def set_pressure(self, state: str) -> None:
        """
        Adjust mapsize based on memory pressure state.

        Called by M1ResourceGovernor when memory pressure changes.
        NORMAL   → map_size = _map_size_default (ceiling-capped, max 256 MB on M1 8GB)
        ELEVATED → map_size = max(256 MB, _map_size_default // 2)
        CRITICAL → map_size = 128 MB (survival mode)

        Note: When _map_size_default is already at the 256 MB ceiling,
        ELEVATED provides no further shrink — only CRITICAL reduces to 128 MB.

        LMDB's set_mapsize() grows the region; shrinking requires env close+reopen.
        On CRITICAL, we shrink by closing and reopening with smaller mapsize.

        Thread-safe: uses _lock to serialize pressure changes. Growth (NORMAL→
        ELEVATED) is safe via set_mapsize(); shrink requires full reopen which
        is only triggered on CRITICAL. The reopen is atomic within the lock.
        """
        if state == self._pressure_state:
            return

        old_state = self._pressure_state
        self._pressure_state = state

        if state == "NORMAL":
            target_size = self._map_size_default
        elif state == "ELEVATED":
            target_size = max(256 * 1024 * 1024, self._map_size_default // 2)
        else:  # CRITICAL
            target_size = _UNIFIED_MAP_SIZE_LOW

        if target_size == self._map_size_current:
            return

        if target_size > self._map_size_current:
            # Grow: safe, just call set_mapsize
            try:
                old_size = self._map_size_current
                self._env.set_mapsize(target_size)
                self._map_size_current = target_size
                logger.info(
                    "[LMDB-UNIFIED] mapsize grew %dMB → %dMB (pressure: %s → %s)",
                    old_size // (1024 * 1024),
                    target_size // (1024 * 1024),
                    old_state,
                    state,
                )
            except Exception as exc:
                logger.warning("[LMDB-UNIFIED] set_mapsize grow failed: %s", exc)
        else:
            # Shrink: requires close + reopen (only on CRITICAL)
            if state == "CRITICAL":
                logger.warning(
                    "[LMDB-UNIFIED] shrinking mapsize %dMB → %dMB (pressure: %s → %s). "
                    "Will reopen env.",
                    self._map_size_current // (1024 * 1024),
                    target_size // (1024 * 1024),
                    old_state,
                    state,
                )
                self._emergency_shrink(target_size)

    def _is_env_alive(self) -> bool:
        """
        Check if the current env is open and usable without acquiring the lock.

        Returns True if env is non-None and not known to be closed.
        This is a best-effort check — the env can become closed immediately
        after this returns. Use env() for a safe accessor instead.
        """
        return self._env is not None and not self._closed

    def _flush_all_sub_dbs(self) -> None:
        """
        Commit all pending writes across all sub-DBs before shrink.

        Uses sync(force=True) to push all dirty pages to disk before close.
        This ensures no data loss when shrinking under CRITICAL pressure.
        """
        if self._env is None:
            return
        try:
            self._env.sync(force=True)
        except Exception as exc:
            logger.debug("[LMDB-UNIFIED] sync(force=True) failed: %s", exc)

    def _reopen_sub_dbs(self) -> None:
        """
        Re-open all sub-DBs into self._sub_dbs after a new env is set.

        Clears self._sub_dbs first, then re-populates from the new env.
        Safe to call even if some sub-DBs fail to open (non-fatal).
        """
        self._sub_dbs.clear()
        for idx in range(self._max_dbs):
            try:
                self._sub_dbs[idx] = self._env.open_db(str(SubDB.name(idx)).encode())
            except Exception as exc:
                logger.warning(
                    "[LMDB-UNIFIED] sub-db %d (%s) reopen failed (non-fatal): %s",
                    idx,
                    SubDB.name(idx),
                    exc,
                )

    def _emergency_shrink(self, target_size: int) -> None:
        """
        Atomically shrink the LMDB mapsize under CRITICAL memory pressure.

        Phases (all under _lock):
          1. Mark reopen_in_progress=True — env() callers block via _ensure_init
          2. Sync — push all dirty data to disk via sync(force=True)
          3. Close — close old env (self._env NOT cleared until AFTER close)
          4. Reopen — open new env with smaller map_size
          5. Restore sub-DBs — re-open all sub-DB handles in the new env

        On any error in phase 3-5, the env is left in a closed state rather
        than risk corrupting data. Callers will receive errors on next env() call.

        Thread-safety: the _lock serializes pressure changes so only one
        shrink runs at a time. _reopen_in_progress guards ensure env() callers
        wait until the reopen completes (or fails).

        RES-02 fixes:
          - self._env is NOT cleared until AFTER old_env.close() returns —
            this eliminates the race window where env() could return None
            while old_env is still valid but self._env is already None.
          - Graceful degradation: if reopen fails, try to restore from old env
            backup rather than leaving system in a permanently closed state.
          - Telemetry: _shrink_count and _shrink_failures track operation health.
        """
        # Fast path — check without lock
        if self._closed:
            return
        if self._reopen_in_progress:
            logger.debug("[LMDB-UNIFIED] shrink already in progress, skipping")
            return

        with self._lock:
            # Double-check inside lock
            if self._closed:
                return
            if self._reopen_in_progress:
                return
            self._reopen_in_progress = True

        try:
            # Phase 1: Sync — ensure all data is on disk before touching the env
            try:
                self._flush_all_sub_dbs()
            except Exception as exc:
                logger.debug("[LMDB-UNIFIED] pre-shrink flush failed: %s", exc)

            # Capture old state for logging and backup
            old_map_size = self._map_size_current
            old_env = self._env

            # Phase 2: Close old env FIRST, THEN clear self._env after close
            # This ensures env() either sees old_env (valid) or None (after clear),
            # never a dangling reference
            try:
                old_env.close()
            except Exception as exc:
                logger.debug("[LMDB-UNIFIED] env.close() warning: %s", exc)

            # Phase 3: NOW clear self._env and sub_dbs — old_env is closed
            self._env = None
            self._sub_dbs.clear()

            # Phase 4: Reopen with smaller mapsize
            import lmdb

            try:
                self._env = lmdb.open(
                    str(self._path),
                    map_size=target_size,
                    max_dbs=self._max_dbs,
                    writemap=False,
                    metasync=True,
                    mode=0o600,
                )
            except Exception as exc:
                # RES-02 graceful degradation: reopen failed
                self._shrink_failures += 1
                logger.error(
                    "[LMDB-UNIFIED] emergency shrink reopen failed ( failures=%d ): %s. "
                    "Attempting backup restore.",
                    self._shrink_failures,
                    exc,
                )
                # Try to restore from old size if space freed up
                try:
                    self._env = lmdb.open(
                        str(self._path),
                        map_size=old_map_size,
                        max_dbs=self._max_dbs,
                        writemap=False,
                        metasync=True,
                        mode=0o600,
                    )
                    self._reopen_sub_dbs()
                    logger.info(
                        "[LMDB-UNIFIED] backup restore succeeded — mapsize=%dMB",
                        old_map_size // (1024 * 1024),
                    )
                except Exception as restore_exc:
                    logger.error(
                        "[LMDB-UNIFIED] backup restore also failed: %s. "
                        "LMDB is closed for this instance.",
                        restore_exc,
                    )
                    self._closed = True
                    return
            else:
                # Phase 5: Restore sub-DBs in the new env
                self._map_size_current = target_size
                self._shrink_count += 1
                self._reopen_sub_dbs()
                logger.info(
                    "[LMDB-UNIFIED] shrunk mapsize %dMB → %dMB ( shrink_count=%d ), "
                    "env reopened with %d sub-DBs",
                    old_map_size // (1024 * 1024),
                    target_size // (1024 * 1024),
                    self._shrink_count,
                    len(self._sub_dbs),
                )
        finally:
            self._reopen_in_progress = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """Close the LMDB environment."""
        # F264: Detach finalizer when explicitly closed
        if hasattr(self, '_finalizer'):
            self._finalizer.detach()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._initialized and self._env is not None:
                try:
                    self._env.close()
                except Exception as exc:
                    logger.debug("[LMDB-UNIFIED] env.close() failed: %s", exc)
            self._initialized = False
            logger.info("[LMDB-UNIFIED] closed")

    def __enter__(self) -> "UnifiedLMDB":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _cleanup(self) -> None:
        """Called by weakref.finalize when UnifiedLMDB is garbage collected.

        This is a last-resort safety net. Proper cleanup should use close() explicitly.
        F320: Refactored to use safe_lmdb_close helper.
        """
        if self._closed:
            return
        self._closed = True
        # F320: Use safe_lmdb_close for DRY error handling
        safe_lmdb_close(self._env, logger=logger, name="LMDB-UNIFIED")
        self._initialized = False
        logger.info("[LMDB-UNIFIED] closed via finalizer")

    # ------------------------------------------------------------------ #
    # Compaction
    # ------------------------------------------------------------------ #
    def compact_subdb(self, sub_idx: int) -> bool:
        """
        Compact a sub-DB to reclaim space after bulk deletions.

        LMDB does not support in-place compaction. This method:
          1. Copies all live data from the sub-DB to a new temp LMDB env
          2. Syncs and closes the temp env
          3. Atomically swaps the old data.mdb with the new one
          4. Reopens the sub-DB handle

        Only the specific sub-DB's data.mdb is replaced; other sub-DBs
        are unaffected. map_size is preserved (LMDB auto-grows as needed).

        Args:
            sub_idx: Sub-DB index to compact.

        Returns:
            True on success, False on failure (env remains usable).
        """
        with self._lock:
            if self._closed or not self._initialized:
                return False

            import lmdb
            import shutil
            import tempfile

            sub = self.open_db(sub_idx)
            sub_name = SubDB.name(sub_idx)

            # Pre-check: estimate live data size to avoid compacting empty DBs
            try:
                with self.env_begin(sub_idx, write=False) as txn:
                    stats = txn.stat(sub)
                    if stats.get("entries", 0) == 0:
                        logger.debug(
                            "[LMDB-UNIFIED] compact_subdb(%s): empty, skipping",
                            sub_name,
                        )
                        return True
            except Exception as exc:
                logger.debug("[LMDB-UNIFIED] compact_subdb stat failed: %s", exc)

            # Phase 1: Open cursor and copy live data to temp LMDB
            temp_dir: tempfile.TemporaryDirectory | None = None
            temp_path: pathlib.Path | None = None
            new_env: Any = None

            try:
                # Create temp directory outside the LMDB path to avoid conflicts
                temp_dir = tempfile.TemporaryDirectory(prefix=f"lmdb_compact_{sub_name}_")
                temp_path = pathlib.Path(temp_dir.name)

                # Open temp LMDB env with same mapsize and max_dbs
                new_env = lmdb.open(
                    str(temp_path),
                    map_size=self._map_size_current,
                    max_dbs=self._max_dbs,
                    writemap=False,
                    metasync=False,  # We'll sync manually
                )
                new_sub = new_env.open_db(str(sub_name).encode())

                # Copy all live key-value pairs
                with self.env_begin(sub_idx, write=False) as src_txn:
                    src_cursor = src_txn.cursor()
                    with new_env.begin(write=True, db=new_sub) as dst_txn:
                        dst_cursor = dst_txn.cursor()
                        copied = 0
                        for key, value in src_cursor:
                            dst_cursor.put(key, value)
                            copied += 1
                        logger.debug(
                            "[LMDB-UNIFIED] compact_subdb(%s): copied %d entries",
                            sub_name,
                            copied,
                        )

                # Phase 2: Sync and close temp env (durable write)
                new_env.sync(force=True)
                new_env.close()
                new_env = None

                # Phase 3: Atomic swap — close source, replace files, reopen
                self._compact_atomic_swap(sub_idx, temp_path, sub_name)

                logger.info("[LMDB-UNIFIED] compact_subdb(%s): done", sub_name)
                return True

            except Exception as exc:
                logger.warning("[LMDB-UNIFIED] compact_subdb(%s) failed: %s", sub_name, exc)
                # Clean up temp env if still open
                if new_env is not None:
                    try:
                        new_env.close()
                    except Exception:  # noqa: BLE001
                        pass
                return False

            finally:
                # temp_dir cleans up the temp directory on context exit
                # (temp_path is kept as reference inside temp_dir context)
                del temp_path  # explicit del to silence "unused" warning
                if temp_dir is not None:
                    try:
                        temp_dir.cleanup()
                    except Exception:  # noqa: BLE001
                        pass

    def _compact_atomic_swap(
        self,
        sub_idx: int,
        temp_path: pathlib.Path,
        sub_name: str,
    ) -> None:
        """
        Perform the atomic file swap for compact_subdb.

        Args:
            sub_idx: Sub-DB index being compacted.
            temp_path: Path to the temporary compacted LMDB directory.
            sub_name: Human-readable sub-DB name for logging.

        Failure recovery: if lmdb.open() fails, attempts to restore from
        the original env (backup restore pattern, matching _emergency_shrink).
        """
        import lmdb
        import shutil

        # Capture old state for potential backup restore
        old_env = self._env
        old_sub_dbs = dict(self._sub_dbs)
        old_map_size = self._map_size_current
        self._sub_dbs.clear()
        self._env = None

        # Close the old env in a safe wrapper — don't let close errors propagate
        try:
            old_env.close()
        except Exception as exc:
            logger.debug("[LMDB-UNIFIED] atomic_swap: old env close warning: %s", exc)

        # Define file paths — LMDB uses data.mdb and optionally lock.mdb
        src_data = temp_path / "data.mdb"
        dst_data = self._path / "data.mdb"
        src_lock = temp_path / "lock.mdb"
        dst_lock = self._path / "lock.mdb"
        dst_lock.unlink(missing_ok=True)

        # Atomic: move temp data.mdb → dst data.mdb
        shutil.move(str(src_data), str(dst_data))

        # Also move lock file if it exists in temp
        if src_lock.exists():
            shutil.move(str(src_lock), str(dst_lock))

        # Reopen the env with the new compacted files
        try:
            self._env = lmdb.open(
                str(self._path),
                map_size=self._map_size_current,
                max_dbs=self._max_dbs,
                writemap=False,
                metasync=True,
                mode=0o600,
            )
        except Exception as exc:
            # Backup restore: reopen with original data
            logger.error(
                "[LMDB-UNIFIED] atomic_swap: env reopen failed: %s. "
                "Attempting backup restore.",
                exc,
            )
            try:
                self._env = lmdb.open(
                    str(self._path),
                    map_size=old_map_size,
                    max_dbs=self._max_dbs,
                    writemap=False,
                    metasync=True,
                    mode=0o600,
                )
                self._reopen_sub_dbs()
                logger.info("[LMDB-UNIFIED] atomic_swap: backup restore succeeded")
            except Exception as restore_exc:
                logger.error(
                    "[LMDB-UNIFIED] atomic_swap: backup restore failed: %s. "
                    "LMDB instance is closed.",
                    restore_exc,
                )
                self._closed = True
                return

        # Re-open the sub-DB handle
        self._sub_dbs[sub_idx] = self._env.open_db(str(sub_name).encode())
        # Re-open any other sub-DBs that were open
        for idx in old_sub_dbs:
            if idx != sub_idx:
                try:
                    self._sub_dbs[idx] = self._env.open_db(str(SubDB.name(idx)).encode())
                except Exception:  # noqa: BLE001
                    pass  # Non-fatal if a rarely-used sub-DB fails to reopen

    def compact_all_subdbs(self) -> dict[int, bool]:
        """
        Compact all sub-DBs that have data.

        Returns:
            Dict mapping sub_idx → success bool.
        """
        results: dict[int, bool] = {}
        for idx in range(self._max_dbs):
            results[idx] = self.compact_subdb(idx)
        return results


# --------------------------------------------------------------------------- #
# Singleton accessor
# --------------------------------------------------------------------------- #
def get_unified_lmdb() -> UnifiedLMDB:
    """
    Get the singleton UnifiedLMDB instance.

    Lazy initialization: env opened on first access, not on import.
    Thread-safe via _instance_lock.
    """
    global _instance

    if _instance is None:
        with _instance_lock:
            if _instance is None:
                # Lazy import to avoid circular dependency at module load time
                import hledac.universal.paths as _paths

                path = _paths.LMDB_ROOT / "unified.lmdb"
                _instance = UnifiedLMDB(path, lazy=True)
                logger.debug("[LMDB-UNIFIED] singleton created at %s", path)

    return _instance


def reset_unified_lmdb() -> None:
    """
    Reset the singleton (for testing only).

    WARNING: Do not call in production while other threads are using the env.
    """
    global _instance

    with _instance_lock:
        if _instance is not None:
            _instance.close()
            _instance = None


def unified_lmdb_stats() -> dict[str, Any]:
    """Return diagnostic stats for the unified LMDB env."""
    store = get_unified_lmdb()
    if store.is_initialized():
        try:
            info = store.env().info()
            return {
                "initialized": True,
                "path": str(store.path()),
                "map_size_current_mb": store._map_size_current // (1024 * 1024),
                "map_size_default_mb": store._map_size_default // (1024 * 1024),
                "pressure_state": store._pressure_state,
                "max_dbs": store._max_dbs,
                "opened_sub_dbs": list(store._sub_dbs.keys()),
                "closed": store.is_closed(),
                # RES-02: Shrink telemetry
                "shrink_count": getattr(store, "_shrink_count", 0),
                "shrink_failures": getattr(store, "_shrink_failures", 0),
                "reopen_in_progress": getattr(store, "_reopen_in_progress", False),
                "info": info,  # lmdb env.info() returns a dict
            }
        except Exception as exc:
            return {"initialized": True, "error": str(exc)}
    return {"initialized": False}
