"""
Dedup Manager — Sprint F216G refactor
=====================================

ROLE: Owns persistent dedup LMDB, hot cache, and semantic dedup cache.

Separated from DuckDBShadowStore so dedup logic is testable without touching DuckDB.

BOUNDARY:
    DuckDBShadowStore.async_ingest_findings_batch() delegates quality decisions
    (entropy check, dedup check) to QualityAssessor but manages dedup storage here.
    DedupManager owns:
      - Persistent LMDB at LMDB_ROOT/dedup.lmdb (cross-source dedup)
      - Bounded hot cache (in-process fingerprint → finding_id)
      - Semantic dedup cache (embedding-based near-duplicate)

CANONICAL WRITE PATH (unchanged):
    DuckDBShadowStore.async_ingest_findings_batch() →
        QualityAssessor.assess_quality() → dedup check via DedupManager
        → DuckDB insert → DedupManager.store_persistent_dedup()

LMDB NAMESPACE:
    dedup:{fingerprint_hex}  → finding_id (UTF-8 bytes)
"""
from __future__ import annotations



from collections import OrderedDict
from typing import Any

import psutil

# Sprint F222F: RotatingBloomFilter for cross-run URL dedup pre-check
# F266-U1: Replaced pure-Python bytearray+hashlib with Rust MmapBloomFilter (xxHash3-64, mmap persistence)
__all__ = ["DedupManager", "RotatingBloomFilter"]

import atexit
import fcntl
import msgspec.json as _json
import os
import threading
import weakref
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from hledac.universal.config.dedup_config import DEDUP_HOT_CACHE_MAX, DEDUP_LMDB_MAP_SIZE

# Backward compatibility: module-level aliases (DEPRECATED — use config.dedup_config)
_DEDUP_LMDB_MAP_SIZE: int = DEDUP_LMDB_MAP_SIZE
_DEDUP_HOT_CACHE_MAX: int = DEDUP_HOT_CACHE_MAX

# F267 + P0-01: Rust mmap-backed IOC dedup store via lazy resolver
from hledac.universal.utils.import_resolver import lazy

_rust_backend_resolver = lazy("core.rust_backend.rust")
_RUST_MMAP_IOC_DEDUP_AVAILABLE = False
RustMmapIocDedupStore: Any = None
_rust_backend = _rust_backend_resolver()
if _rust_backend is not None and getattr(_rust_backend, "is_available", False) and _rust_backend.raw is not None:
    RustMmapIocDedupStore = getattr(_rust_backend.raw, "MmapIocDedupStore", None)
    if RustMmapIocDedupStore is not None:
        _RUST_MMAP_IOC_DEDUP_AVAILABLE = True

# Sprint P1-14: Dead code removed — env override now honoured via config.dedup_config

# =============================================================================
# F267: Module-level cleanup + SIGTERM handler for DedupManager
# =============================================================================
# Global state to track active DedupManager instances requiring atexit cleanup.
# weakref.finalize ensures the instance is kept alive until exit.
_DEDUP_MANAGER_FINALIZERS: list[weakref.finalize] = []
_SIGTERM_HANDLER_REGISTERED: bool = False


def _dedup_manager_atexit_close() -> None:
    """F267: Called at interpreter exit via atexit.register().

    Fires AFTER all module-level __del__ (including Rust Drop impls).
    By this point all Python-level cleanup has run, so we only need
    to call close() on any surviving DedupManager instances to ensure
    their mmap-backed IOC dedup store is properly persisted.

    Exceptions are silenced because we're already in interpreter shutdown —
    logging may be unavailable and we must not raise.
    """
    for finalizer in _DEDUP_MANAGER_FINALIZERS:
        try:
            finalizer()
        except Exception:  # noqa: BLE001
            pass
    _DEDUP_MANAGER_FINALIZERS.clear()


def _dedup_manager_sigterm_handler(signum: int, _frame: Any) -> None:
    """F267: SIGTERM handler — calls close() on all tracked DedupManager instances.

    Called synchronously on the signal-receiving thread. We ONLY call close()
    here (not __del__), so it's safe: close() persists mmap + releases fd.
    We then re-raise the signal so the OS can deliver it to the default handler,
    which will terminate the process.

    Note: signal handlers run on a different thread in Python, so we use
    an interrupt-driven approach — close() is thread-safe for our use case
    (DashMap + Arc<File> are Send+Sync on Unix).
    """
    _dedup_manager_atexit_close()
    # Re-raise SIGTERM to allow default OS termination after cleanup
    signal_raise = getattr(os, 'raise_signal', None)
    if signal_raise is not None:
        # Python 3.12+
        try:
            signal_raise(signum)
        except Exception:  # noqa: BLE001
            pass
    # Fallback: re-import and raise (works on Unix)
    try:
        import signal
        signal.raise_signal(signum)
    except Exception:  # noqa: BLE001
        pass


def _register_dedup_manager_finalizer(instance: DedupManager) -> weakref.finalize:
    """F267: Register a DedupManager instance for atexit + SIGTERM cleanup.

    Returns the finalizer. Call this from DedupManager.__init__ or from
    the code that creates the instance.

    Uses weakref.finalize (not atexit.register directly) because:
    1. weakref.finalize is called when the object is garbage-collected
    2. atexit.register ensures cleanup also happens when the process exits
       even if the object is still alive
    3. This combination handles both explicit close() and implicit GC/exit
    """
    global _SIGTERM_HANDLER_REGISTERED

    def _close_instance() -> None:
        try:
            instance.close()
        except Exception:  # noqa: BLE001
            pass

    finalizer = weakref.finalize(instance, _close_instance)
    _DEDUP_MANAGER_FINALIZERS.append(finalizer)

    # Register module-level atexit once
    if not _SIGTERM_HANDLER_REGISTERED:
        try:
            import signal
            signal.signal(signal.SIGTERM, _dedup_manager_sigterm_handler)
            _SIGTERM_HANDLER_REGISTERED = True
        except (AttributeError, OSError):  # noqa: BLE001
            # SIGTERM handler not available (Windows or other non-Unix)
            pass
        try:
            atexit.register(_dedup_manager_atexit_close)
        except Exception:  # noqa: BLE001
            pass

    return finalizer


def _load_rust_bloom() -> Any:
    """Lazy-load Rust MmapBloomFilter to avoid early import crash on M1."""
    # F265C: Use centralized rust backend
    try:
        from core.rust_backend import rust as _rust_backend

        if _rust_backend.is_available and _rust_backend.bloom is not None:
            return _rust_backend.bloom.MmapBloomFilter
        return None
    except Exception:
        return None


class RotatingBloomFilter:
    """
    Cross-run URL dedup pre-check. Sprint F222F, F266-U1, F288+, P1-10.

    Two-generation bloom filter using Rust RotatingMmapBloomFilter:
    - active: current generation, being written to
    - previous: previous generation, read-only for lookups

    When active reaches capacity, rotate: active becomes previous, new active created.
    This prevents unbounded memory growth while maintaining dedup across many runs.

    Uses Rust RotatingMmapBloomFilter via PyO3 FFI — xxHash3-64 hashing (NEON-SIMD
    on M1, 3-5× faster than prior blake2b), mmap-backed file persistence (no LMDB
    overhead), cross-restart persistence with zero warm-up cost.

    P1-10 invariants:
        - Always-on: no feature flag, no env var toggle
        - Bounded: capacity hard-capped, rotation prevents unbounded growth
        - Fail-safe: any error returns default (allow), never crashes sprint
        - M1 8GB safe: mmap working set bounded by access pattern
        - Race-free init: fcntl.flock prevents concurrent init race on mmap files
        - Lazy init: filter created on first add/contains, not in __init__
        - Single rust import: one try/except block, no redundant imports
        - __slots__: memory-efficient, no __dict__ per instance
    """

    __slots__ = (
        "_capacity",
        "_fp_rate",
        "_base_dir",
        "_filter",
        "_init_done",
    )

    def __init__(
        self,
        capacity: int = 100_000,
        fp_rate: float = 0.001,
        lmdb_path: str | None = None,
    ) -> None:
        """
        Args:
            capacity: Max items per generation before rotation.
            fp_rate: Target false positive rate.
            lmdb_path: Ignored (kept for API compat). Persistence via mmap files.
        """
        self._capacity = capacity
        self._fp_rate = fp_rate

        # P1-14: Resolve mmap file directory via centralized path resolver
        from hledac.universal.paths import get_dedup_paths

        if lmdb_path is None:
            base_dir = str(get_dedup_paths()["bloom_dir"])
        else:
            dirname = os.path.dirname(lmdb_path)
            base_dir = dirname if dirname else str(get_dedup_paths()["bloom_dir"])
        self._base_dir = base_dir
        os.makedirs(self._base_dir, exist_ok=True)

        # P1-10: Lazy init — filter created on first add/contains, not here.
        # This prevents event-loop blocking at init time and avoids creating
        # mmap files for sprints that never actually dedup anything.
        self._filter: Any | None = None
        self._init_done: bool = False

    def _ensure_filter(self) -> Any | None:
        """
        Lazy-init filter under fcntl.flock — race-free across processes.

        P1-10: Single import block, fcntl.flock prevents concurrent init race.
        Fallback Python in-memory filter has no file race (no persistence).
        """
        if self._init_done and self._filter is not None:
            return self._filter

        path_a = str(Path(self._base_dir) / "bloom_active.mmap")
        path_b = str(Path(self._base_dir) / "bloom_previous.mmap")
        lock_path = str(Path(self._base_dir) / "bloom.lock")

        # P1-10: fcntl.flock — POSIX advisory lock, atomic, no os.path.exists race.
        # Lock is per-filter-instance; multiple RotatingBloomFilter instances
        # for different base_dirs use different lock files (per base_dir).
        try:
            lock_fd = open(lock_path, "w")
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
                # Re-check after acquiring lock (another process may have init'd)
                if self._init_done and self._filter is not None:
                    return self._filter

                self._filter = self._try_rust_rotating(path_a, path_b)
                if self._filter is None:
                    self._filter = self._try_python_fallback(path_a, path_b)
                self._init_done = True
            finally:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                lock_fd.close()
        except Exception:  # noqa: BLE001
            # Fail-soft: return in-memory fallback if lock acquisition fails
            if self._filter is None:
                self._filter = self._try_python_fallback(path_a, path_b)
                self._init_done = True

        return self._filter

    def _try_rust_rotating(self, path_a: str, path_b: str) -> Any | None:
        """
        Try Rust RotatingMmapBloomFilter (F288+: race-free rotation in Rust).

        Single import block — no redundant re-imports.
        """
        try:
            from core.rust_backend import rust as _rb

            if not (_rb.is_available and _rb.bloom is not None):
                return None
            RotatingBF = getattr(_rb.bloom, "RotatingMmapBloomFilter", None)
            if RotatingBF is None:
                return None
            return RotatingBF(path_a, path_b, self._capacity, self._fp_rate)
        except Exception:  # noqa: BLE001
            return None

    def _try_python_fallback(self, path_a: str, path_b: str) -> Any:
        """
        Last-resort Python fallback — in-memory only, no file race possible.

        P1-10: Uses set-based in-memory filter. No mmap persistence
        (cross-run state is lost on crash, but dedup is best-effort anyway).
        No os.path.exists race because there are no files to race on.
        """

        class _InMemFilter:
            """In-memory two-generation bloom filter (Python fallback)."""

            __slots__ = ("_active", "_previous", "_lock")

            def __init__(self) -> None:
                self._active: set[str] = set()
                self._previous: set[str] = set()
                self._lock = threading.Lock()

            def add(self, item: str) -> None:
                with self._lock:
                    self._active.add(item)

            def __contains__(self, item: str) -> bool:
                with self._lock:
                    return item in self._active or item in self._previous

            def __len__(self) -> int:
                with self._lock:
                    return len(self._active)

            def rotate(self) -> None:
                """Rotate: active becomes previous (read-only), new empty active."""
                with self._lock:
                    self._previous = self._active
                    self._active = set()

            def sync(self) -> None:
                """No-op for in-memory filter."""
                pass

        # pylint: disable=unused-argument
        # path_a, path_b unused — in-memory filter has no files to race on
        return _InMemFilter()

    @property
    def _use_rust_rotate(self) -> bool:
        """True if using Rust RotatingMmapBloomFilter (race-free)."""
        if self._filter is None:
            return False
        try:
            from core.rust_backend import rust as _rb
        except Exception:  # noqa: BLE001
            return False

        return (
            _rb.is_available
            and _rb.bloom is not None
            and getattr(_rb.bloom, "RotatingMmapBloomFilter", None) is not None
        )

    def add(self, item: str) -> None:
        """
        Add item hash to active filter. Rotate if active is full.

        Args:
            item: URL or fingerprint string to add.
        """
        f = self._ensure_filter()
        if f is None:
            return

        try:
            if self._use_rust_rotate:
                if len(f) >= self._capacity:
                    f.rotate()
                f.add(item)
            else:
                # Python fallback: check counter via len(active)
                if len(f) >= self._capacity:
                    f.rotate()
                f.add(item)
        except Exception:  # noqa: BLE001
            pass

    def contains(self, item: str) -> bool:
        """
        Check both active and previous filters.

        Args:
            item: URL or fingerprint string to check.

        Returns:
            True if item was previously added (possible duplicate).
        """
        f = self._ensure_filter()
        if f is None:
            return False
        try:
            return bool(item in f)
        except Exception:  # noqa: BLE001
            return False

    def persist(self) -> None:
        """Sync active filter to disk (msync handled by Rust)."""
        if self._filter is not None and hasattr(self._filter, "sync"):
            try:
                self._filter.sync()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        """Close mmap filters and sync to disk."""
        self.persist()
        self._filter = None
        self._init_done = False


class DedupManager:
    """
    Owns dedup storage lifecycle for DuckDBShadowStore.

    Responsible for:
      - Persistent LMDB dedup at LMDB_ROOT/dedup.lmdb (cross-source dedup)
      - Bounded hot cache (in-process fingerprint → finding_id)
      - Semantic dedup cache (embedding-based near-duplicate, optional)
    """

    DEDUP_NAMESPACE: str = "dedup:"

    def __init__(
        self,
        dedup_lmdb_path: str | None = None,
        semantic_lmdb_path: str | None = None,
        *,
        map_size: int = _DEDUP_LMDB_MAP_SIZE,
        max_keys: int = 1_000_000,
    ) -> None:
        """
        Args:
            dedup_lmdb_path: Path to dedup LMDB. If None, resolved from HLEDAC_DEDUP_LMDB_PATH env
                or LMDB_ROOT/dedup.lmdb fallback.
            semantic_lmdb_path: Path to semantic dedup LMDB. If None, uses default.
            map_size: LMDB map size in bytes for dedup store.
            max_keys: Max keys in dedup LMDB.
        """
        # P1-14: env override honoured via get_dedup_paths()
        if dedup_lmdb_path is not None:
            self._dedup_lmdb_path_str: str | None = dedup_lmdb_path
        else:
            from hledac.universal.paths import get_dedup_paths

            self._dedup_lmdb_path_str = str(get_dedup_paths()["dedup_lmdb"])
        self._semantic_lmdb_path: str | None = semantic_lmdb_path
        self._map_size = map_size
        self._max_keys = max_keys

        # Persistent dedup LMDB
        self._dedup_lmdb: Any | None = None
        self._dedup_lmdb_last_error: str | None = None
        self._dedup_lmdb_boot_error: str | None = None

        # Bounded hot cache
        self._dedup_hot_cache: dict[str, str] = {}
        self._dedup_hot_cache_order: OrderedDict = OrderedDict()

        # Semantic dedup cache (lazy init)
        self._semantic_dedup_cache: Any | None = None
        self._semantic_dedup_boot_error: str | None = None

        # P1-4: Rust BloomFilter pre-check (fast negative dedup)
        self._bloom_filter: Any | None = None
        self._bloom_filter_error: str | None = None

        # F267: Rust mmap-backed IOC dedup store (persistent, cross-sprint)
        self._ioc_dedup_store: Any | None = None
        self._ioc_dedup_store_error: str | None = None

        # F267: Register this instance for atexit + SIGTERM cleanup.
        # _register_dedup_manager_finalizer registers the instance's close() method
        # as both a weakref.finalize callback (on GC) and an atexit callback (on exit),
        # plus installs a SIGTERM handler that calls close() on all tracked instances.
        _register_dedup_manager_finalizer(self)

        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """
        Eager initialize — kept for backward compat, marks initialized.
        All sub-systems are now lazy-initialized on first actual use.
        """
        if self._initialized:
            return
        self._initialized = True

    async def ainitialize(self) -> None:
        """
        Async version of initialize() — runs all sync I/O in thread pool.

        F268: Prevents event-loop blocking during DedupManager init.
        All 4 init methods do file I/O (LMDB open, mmap files).
        Running them in thread pool keeps event loop responsive.
        """
        if self._initialized:
            return

        def _init_sync() -> None:
            """Synchronous init — runs in thread pool to avoid event-loop blocking."""
            self._init_persistent_dedup_lmdb()
            self._init_bloom_filter_precheck()
            self._init_mmap_ioc_dedup_store()
            self._init_semantic_dedup_cache()
            self._initialized = True

        import asyncio
        await asyncio.to_thread(_init_sync)

    def close(self) -> None:
        """Close all LMDB stores and Bloom filter."""
        # P1-4: Close Bloom filter first
        if self._bloom_filter is not None:
            try:
                sync = getattr(self._bloom_filter, "sync", None)
                if sync:
                    sync()
            except Exception:  # noqa: BLE001
                pass
            self._bloom_filter = None
        self._bloom_previous = None
        self._bloom_filter_error = None

        # F267: Close mmap IOC dedup store — call close() (persist + release fd)
        # Fall back to msync() if close() not available (Python fallback has no fd).
        if self._ioc_dedup_store is not None:
            try:
                close = getattr(self._ioc_dedup_store, "close", None)
                if close:
                    close()
                else:
                    msync = getattr(self._ioc_dedup_store, "msync", None)
                    if msync:
                        msync()
            except Exception:  # noqa: BLE001
                pass
            self._ioc_dedup_store = None
        self._ioc_dedup_store_error = None

        if self._dedup_lmdb is not None:
            try:
                self._dedup_lmdb.close()
            except Exception:  # noqa: BLE001
                pass
            self._dedup_lmdb = None
        self._dedup_lmdb_last_error = None
        self._dedup_lmdb_boot_error = None

    # ------------------------------------------------------------------
    # Persistent Dedup LMDB
    # ------------------------------------------------------------------

    def _init_persistent_dedup_lmdb(self) -> None:
        """
        Initialize persistent dedup LMDB.

        Fails softly: any exception is caught and stored in _dedup_lmdb_boot_error.
        """
        try:
            if self._dedup_lmdb_path_str is None:
                # P1-14: use centralized path resolver (honours HLEDAC_DEDUP_LMDB_PATH)
                from hledac.universal.paths import get_dedup_paths

                paths = get_dedup_paths()
                self._dedup_lmdb_path_str = str(paths["dedup_lmdb"])

            from hledac.universal.tools.lmdb_kv import LMDBKVStore
            self._dedup_lmdb = LMDBKVStore(
                path=self._dedup_lmdb_path_str,
                map_size=self._map_size,
                max_keys=self._max_keys,
            )
            self._dedup_lmdb_last_error = None
            self._dedup_lmdb_boot_error = None
        except Exception as e:
            self._dedup_lmdb = None
            self._dedup_lmdb_path_str = None
            self._dedup_lmdb_boot_error = str(e)
            self._dedup_lmdb_last_error = str(e)

    def _init_bloom_filter_precheck(self) -> None:
        """
        Initialize Rust MmapBloomFilter pre-check for fast negative dedup.

        P1-4: Bloom filter sits in front of LMDB for O(1) negative dedup —
        if Bloom says "not seen", skip LMDB entirely. If Bloom says "seen",
        verify against LMDB (authoritative).

        Fails softly: any exception stored in _bloom_filter_error.
        """
        # F265C: Use centralized rust backend
        try:
            from core.rust_backend import rust as _rust_backend

            MmapBloomFilter = None
            if _rust_backend.is_available and _rust_backend.bloom is not None:
                MmapBloomFilter = _rust_backend.bloom.MmapBloomFilter

            if MmapBloomFilter is None:
                # G-9: Try Python fallback before giving up
                _PythonFallback = getattr(_rust_backend, "_PythonMmapBloomFilter", None)
                if _PythonFallback is None:
                    _PythonFallback = getattr(_rust_backend, "MmapBloomFilter", None)
                if _PythonFallback is not None:
                    MmapBloomFilter = _PythonFallback
                else:
                    self._bloom_filter_error = "Rust MmapBloomFilter not available"
                    return

            # P1-14: use centralized path resolver
            from hledac.universal.paths import get_dedup_paths

            _paths = get_dedup_paths()
            _bd = _paths["bloom_dir"]
            _bd.mkdir(parents=True, exist_ok=True)
            active_path = str(_bd / "dedup_bloom_active.mmap")
            previous_path = str(_bd / "dedup_bloom_previous.mmap")

            # Two-generation Bloom: active + previous
            self._bloom_filter = MmapBloomFilter(
                active_path, 100_000, 0.001, force_new=False
            )
            # Previous generation (reuse if exists)
            if _bd.exists():
                self._bloom_previous = MmapBloomFilter(
                    previous_path, 100_000, 0.001, force_new=False
                )
            else:
                self._bloom_previous = MmapBloomFilter(
                    previous_path, 100_000, 0.001, force_new=True
                )
            self._bloom_filter_error = None
        except Exception as e:
            self._bloom_filter = None
            self._bloom_previous = None
            self._bloom_filter_error = str(e)

    def _init_mmap_ioc_dedup_store(self) -> None:
        """
        Initialize Rust MmapIocDedupStore for persistent IOC dedup.

        F267: Mmap-backed IOC dedup replaces LMDB-based IOC dedup.
        Persists across process restarts with zero warm-up cost.
        M1 8GB safe: demand-paged, HashSet rebuilt on load.
        G-9: Falls back to pure-Python _PythonMmapIocDedupStore if Rust unavailable.

        Fails softly: any exception stored in _ioc_dedup_store_error.
        """
        # G-9: Resolve path first (shared between Rust and Python fallback)
        try:
            # P1-14: use centralized path resolver
            from hledac.universal.paths import get_dedup_paths

            _paths = get_dedup_paths()
            _bd = _paths["bloom_dir"]
            _bd.mkdir(parents=True, exist_ok=True)
            ioc_path = str(_bd / "ioc_dedup.mmap")
        except Exception as e:
            self._ioc_dedup_store = None
            self._ioc_dedup_store_error = f"path resolution failed: {e}"
            return

        # G-9: Try Rust first, fall back to Python
        store_class: Any = None
        if _RUST_MMAP_IOC_DEDUP_AVAILABLE:
            store_class = RustMmapIocDedupStore
        else:
            # Try Python fallback
            try:
                from core.rust_backend import rust as _rb

                store_class = getattr(_rb, "_PythonMmapIocDedupStore", None)
                if store_class is None:
                    store_class = getattr(_rb, "MmapIocDedupStore", None)
                if store_class is None:
                    self._ioc_dedup_store_error = "Rust MmapIocDedupStore not available"
                    return
            except Exception:
                self._ioc_dedup_store_error = "Rust MmapIocDedupStore not available"
                return

        try:
            self._ioc_dedup_store = store_class(ioc_path, force_new=False)
            self._ioc_dedup_store_error = None
        except Exception as e:
            self._ioc_dedup_store = None
            self._ioc_dedup_store_error = str(e)

    def _dedup_key_from_fingerprint(self, fp: str) -> bytes:
        """Build dedup namespace key from BLAKE2b fingerprint."""
        return f"{self.DEDUP_NAMESPACE}{fp}".encode()

    def _dedup_lmdb_key_to_fingerprint(self, key: bytes) -> str:
        """Extract fingerprint from dedup namespace key."""
        return key.decode("utf-8")[len(self.DEDUP_NAMESPACE):]

    def lookup_persistent_dedup(self, fp: str) -> str | None:
        """
        Lookup a fingerprint in the persistent dedup LMDB.

        P1-4: Bloom filter pre-check — O(1) negative dedup, skip LMDB if Bloom says "not seen".
        LMDB remains authoritative for positive matches.

        F272: Lazy init — each sub-system initializes on first actual use, not at sprint start.
        Saves ~2s from sprint boot when dedup LMDB mmap files are cold.

        Args:
            fp: 32-char BLAKE2b fingerprint hex string

        Returns:
            finding_id string if found, None otherwise (miss or LMDB unavailable)
        """
        # F272: Lazy init on first use — deferred until first finding processed
        if self._bloom_filter is None:
            self._init_bloom_filter_precheck()
        if self._dedup_lmdb is None:
            self._init_persistent_dedup_lmdb()
        if self._ioc_dedup_store is None:
            self._init_mmap_ioc_dedup_store()

        # P1-4: Bloom pre-check — fast negative dedup
        if self._bloom_filter is not None:
            try:
                # Check both active and previous generations using .contains() method
                _bloom_contains = getattr(self._bloom_filter, "contains", None)
                in_active = _bloom_contains(fp) if _bloom_contains else False
                in_previous = False
                if hasattr(self, "_bloom_previous") and self._bloom_previous is not None:
                    _prev_contains = getattr(self._bloom_previous, "contains", None)
                    in_previous = _prev_contains(fp) if _prev_contains else False
                if not in_active and not in_previous:
                    # Bloom says "definitely not seen" — skip LMDB entirely
                    return None
            except Exception:  # noqa: BLE001
                pass  # noqa: BLE001  # Bloom error — fall through to LMDB

        if self._dedup_lmdb is None:
            return None
        try:
            key = self._dedup_key_from_fingerprint(fp)
            with self._dedup_lmdb._env.begin(write=False, buffers=True) as txn:
                raw = txn.get(key)
                if raw is None:
                    return None
                return bytes(raw).decode("utf-8")
        except Exception:
            self._dedup_lmdb_last_error = f"lookup failed for fp={fp[:8]}"
            return None

    def store_persistent_dedup(self, fp: str, finding_id: str) -> None:
        """
        Store a fingerprint → finding_id mapping in persistent dedup LMDB.

        P1-4: Also update Bloom filter for fast negative dedup.
        F272: Lazy init on first use.

        Args:
            fp: 32-char BLAKE2b fingerprint hex string
            finding_id: canonical finding ID
        """
        # F272: Lazy init on first use
        if self._bloom_filter is None:
            self._init_bloom_filter_precheck()
        if self._dedup_lmdb is None:
            self._init_persistent_dedup_lmdb()

        # P1-4: Update Bloom filter first (O(1), in-memory)
        if self._bloom_filter is not None:
            try:
                self._bloom_filter.add(fp)
            except Exception:  # noqa: BLE001
                pass  # noqa: BLE001  # Bloom update failure is non-fatal

        if self._dedup_lmdb is None:
            return
        try:
            key = self._dedup_key_from_fingerprint(fp)
            value_bytes = finding_id.encode("utf-8")
            # putmulti_bounded: one txn, bounded chunking, fail-safe
            from hledac.universal.utils.lmdb_bulk import putmulti_bounded
            putmulti_bounded(
                self._dedup_lmdb._env,
                [(key, value_bytes)],
                overwrite=True,
            )
        except Exception as e:
            self._dedup_lmdb_last_error = f"store failed for fp={fp[:8]}: {e}"

    # S3: Batch store for bulk inserts — single txn.begin() instead of N
    def store_persistent_dedup_batch(self, items: list[tuple[str, str]]) -> None:
        """
        Store multiple fingerprint → finding_id mappings in persistent dedup LMDB.

        S3: Single transaction for batch insert, reduces N txn.begin() to 1.

        Args:
            items: List of (fp, finding_id) tuples
        """
        if not items:
            return
        # F272: Lazy init on first use
        if self._bloom_filter is None:
            self._init_bloom_filter_precheck()
        if self._dedup_lmdb is None:
            self._init_persistent_dedup_lmdb()

        # Update Bloom filters
        if self._bloom_filter is not None:
            for fp, _ in items:
                try:
                    self._bloom_filter.add(fp)
                except Exception:  # noqa: BLE001
                    pass  # noqa: BLE001

        if self._dedup_lmdb is None:
            return
        try:
            encoded = [
                (self._dedup_key_from_fingerprint(fp), finding_id.encode("utf-8"))
                for fp, finding_id in items
            ]
            with self._dedup_lmdb._env.begin(write=True) as txn:
                cursor = txn.cursor()
                cursor.putmulti(encoded)
        except Exception as e:
            self._dedup_lmdb_last_error = f"batch store failed ({len(items)} items): {e}"

    # ------------------------------------------------------------------
    # Hot Cache
    # ------------------------------------------------------------------

    def _hot_cache_max(self) -> int:
        """Hot cache max size from config."""
        return DEDUP_HOT_CACHE_MAX

    def add_to_hot_cache(self, fp: str, finding_id: str) -> None:
        """
        Add entry to bounded hot cache with FIFO eviction.

        Hard cap: _DEDUP_HOT_CACHE_MAX entries.
        O(1) operations using OrderedDict: move_to_end() for MRU, popitem(last=False) for FIFO.
        """
        max_cap = self._hot_cache_max()
        if fp in self._dedup_hot_cache:
            self._dedup_hot_cache_order.move_to_end(fp)
            return
        if len(self._dedup_hot_cache) >= max_cap:
            oldest, _ = self._dedup_hot_cache_order.popitem(last=False)
            self._dedup_hot_cache.pop(oldest, None)
        self._dedup_hot_cache[fp] = finding_id
        self._dedup_hot_cache_order[fp] = None

    def hot_cache_lookup(self, fp: str) -> str | None:
        """Bounded hot cache lookup."""
        return self._dedup_hot_cache.get(fp)

    # ------------------------------------------------------------------
    # Semantic Dedup Cache
    # ------------------------------------------------------------------

    def _init_semantic_dedup_cache(self) -> None:
        """
        Initialize semantic dedup cache (Sprint F195).

        Memory-aware: skips init if RSS > 6GB threshold.
        Fail-soft: any exception stored in _semantic_dedup_boot_error.
        """
        try:
            rss = psutil.Process().memory_info().rss
            if rss > 6.0 * 1024**3:
                self._semantic_dedup_cache = None
                self._semantic_dedup_boot_error = "memory pressure — skipped"
                return
        except Exception:  # noqa: BLE001
            pass

        try:
            if self._semantic_lmdb_path is None:
                from hledac.universal.paths import get_dedup_paths

                lmdb_path = str(get_dedup_paths()["lmdb_root"] / "semantic_dedup.lmdb")
            else:
                lmdb_path = self._semantic_lmdb_path

            from hledac.universal.semantic_deduplicator import SemanticDedupCache
            self._semantic_dedup_cache = SemanticDedupCache(lmdb_path=lmdb_path)
            self._semantic_dedup_boot_error = None
        except Exception as e:
            self._semantic_dedup_cache = None
            self._semantic_dedup_boot_error = str(e)

    @property
    def semantic_dedup_cache(self) -> Any | None:
        """Return the semantic dedup cache instance."""
        return self._semantic_dedup_cache

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_runtime_status(
        self,
        quality_state: Any,
    ) -> dict:
        """
        Return typed/cheap status surface for dedup subsystem.

        Args:
            quality_state: QualityAssessmentState instance with _quality_duplicate_count,
                          _persistent_duplicate_count, _accepted_count, _quality_rejected_count,
                          _quality_fail_open_count.
        """
        return {
            "persistent_dedup_enabled": self._dedup_lmdb is not None,
            "bloom_filter_enabled": self._bloom_filter is not None,
            "bloom_filter_error": self._bloom_filter_error,
            "last_boot_cleanup_error": self._dedup_lmdb_boot_error,
            "last_dedup_error": self._dedup_lmdb_last_error,
            "dedup_lmdb_path": self._dedup_lmdb_path_str or "",
            "dedup_namespace": self.DEDUP_NAMESPACE,
            "hot_cache_size": len(self._dedup_hot_cache),
            "hot_cache_capacity": self._hot_cache_max(),
            "in_memory_duplicate_count": quality_state._quality_duplicate_count,
            "persistent_duplicate_count": quality_state._persistent_duplicate_count,
            "accepted_count": quality_state._accepted_count,
            "low_information_rejected_count": quality_state._quality_rejected_count,
            "in_memory_duplicate_rejected_count": quality_state._quality_duplicate_count,
            "persistent_duplicate_rejected_count": quality_state._persistent_duplicate_count,
            "other_rejected_count": quality_state._quality_fail_open_count,
        }
