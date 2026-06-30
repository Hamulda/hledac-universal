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


from collections import OrderedDict
from typing import Any

import psutil

# Sprint F222F: RotatingBloomFilter for cross-run URL dedup pre-check
# F266-U1: Replaced pure-Python bytearray+hashlib with Rust MmapBloomFilter (xxHash3-64, mmap persistence)
__all__ = ["DedupManager", "RotatingBloomFilter"]

import json
import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Sprint 8AG §6.17: Default dedup LMDB map size
_DEDUP_LMDB_MAP_SIZE: int = 64 * 1024 * 1024  # 64MB
# Sprint F216G: Same constant imported from quality_assessment for hot cache cap
_DEDUP_HOT_CACHE_MAX: int = 10000  # will be overridden by quality_assessment import

# F267: Rust mmap-backed IOC dedup store (cross-sprint persistence, M1 8GB safe)
# F265C: Use centralized rust backend
_RUST_MMAP_IOC_DEDUP_AVAILABLE = False
RustMmapIocDedupStore: Any = None
try:
    from core.rust_backend import rust as _rust_backend

    if _rust_backend.is_available and _rust_backend.raw is not None:
        # G-9 FIX: Use MmapIocDedupStore (file-backed), NOT IocDedupStore (in-memory).
        # raw.MmapIocDedupStore is the PyO3 class wrapping Rust mmap-backed store.
        RustMmapIocDedupStore = _rust_backend.raw.MmapIocDedupStore
        _RUST_MMAP_IOC_DEDUP_AVAILABLE = True
except ImportError:
    pass

# Sprint P1-3: Env override for explicit dedup LMDB path
_DEDUP_LMDB_PATH: str | None = os.environ.get("HLEDAC_DEDUP_LMDB_PATH")


def _load_dedup_hot_cache_max() -> int:
    """Lazy-load DEDUP_HOT_CACHE_MAX from quality_assessment."""
    try:
        from .quality_assessment import _DEDUP_HOT_CACHE_MAX
        return _DEDUP_HOT_CACHE_MAX
    except ImportError:
        return 10000


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
    Cross-run URL dedup pre-check. Sprint F222F, F266-U1.

    Two-generation bloom filter using Rust MmapBloomFilter:
    - active: current generation, being written to
    - previous: previous generation, read-only for lookups

    When active reaches capacity, rotate: active becomes previous, new active created.
    This prevents unbounded memory growth while maintaining dedup across many runs.

    Uses Rust MmapBloomFilter via PyO3 FFI — xxHash3-64 hashing (NEON-SIMD on M1,
    3-5× faster than prior blake2b), mmap-backed file persistence (no LMDB overhead),
    cross-restart persistence with zero warm-up cost.

    Invariants:
        - Always-on: no feature flag, no env var toggle
        - Bounded: capacity hard-capped, rotation prevents unbounded growth
        - Fail-safe: any error returns default (allow), never crashes sprint
        - M1 8GB safe: mmap working set bounded by access pattern, not allocation size
    """

    # Sidecar JSON file for generation state (written atomically)
    _GEN_FILE: str = "bloom_generation.json"

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

        # Resolve mmap file directory
        from hledac.universal.paths import LMDB_STORE_ROOT
        if lmdb_path is None:
            base_dir = str(LMDB_STORE_ROOT)
        else:
            dirname = os.path.dirname(lmdb_path)
            base_dir = dirname if dirname else str(LMDB_STORE_ROOT)
        self._base_dir = base_dir
        os.makedirs(self._base_dir, exist_ok=True)

        self._active_path: str = os.path.join(self._base_dir, "bloom_active.mmap")
        self._previous_path: str = os.path.join(self._base_dir, "bloom_previous.mmap")
        self._gen_path: str = os.path.join(self._base_dir, self._GEN_FILE)

        # Rust filter class (lazy-loaded)
        self._MmapBloomFilter: Any = None

        # Two-generation filters (None until _init_filters called)
        self._active: Any | None = None
        self._previous: Any | None = None
        self._counter: int = 0

        # Thread safety for add() (contains is GIL-protected read-only)
        self._lock = threading.Lock()

        # Initialize filters
        self._init_filters()

    def _init_filters(self) -> None:
        """Lazy-init Rust MmapBloomFilter instances with Python fallback."""
        if self._MmapBloomFilter is None:
            self._MmapBloomFilter = _load_rust_bloom()
        if self._MmapBloomFilter is None:
            # G-9: Fall back to pure-Python MmapBloomFilter (not no-op)
            try:
                from core.rust_backend import rust as _rb

                _PythonFallback = getattr(_rb, "_PythonMmapBloomFilter", None)
                if _PythonFallback is None:
                    _PythonFallback = getattr(_rb, "MmapBloomFilter", None)
                if _PythonFallback is not None:
                    self._active = _PythonFallback(
                        self._active_path,
                        self._capacity,
                        self._fp_rate,
                        force_new=True,
                    )
                    self._previous = _PythonFallback(
                        self._previous_path,
                        self._capacity,
                        self._fp_rate,
                        force_new=True,
                    )
                    return
            except Exception:
                pass
            # Final fallback: no-op filter
            self._active = None
            self._previous = None
            return

        try:
            # Load generation state
            gen_state = self._load_gen_state()

            # Open/create active filter — reuse existing file if present
            if os.path.exists(self._active_path):
                self._active = self._MmapBloomFilter(
                    self._active_path,
                    self._capacity,
                    self._fp_rate,
                    force_new=False,
                )
            else:
                # First run: create new active
                self._active = self._MmapBloomFilter(
                    self._active_path,
                    self._capacity,
                    self._fp_rate,
                    force_new=True,
                )

            # Open/create previous filter — reuse existing file if present
            if os.path.exists(self._previous_path):
                self._previous = self._MmapBloomFilter(
                    self._previous_path,
                    self._capacity,
                    self._fp_rate,
                    force_new=False,
                )
            else:
                # First run: create empty previous
                self._previous = self._MmapBloomFilter(
                    self._previous_path,
                    self._capacity,
                    self._fp_rate,
                    force_new=True,
                )

            self._counter = gen_state.get("counter", 0)

        except Exception:
            # Fail-safe: any error → create fresh filters
            self._active = None
            self._previous = None
            self._counter = 0

    def _load_gen_state(self) -> dict:
        """Load generation state from sidecar JSON."""
        try:
            if os.path.exists(self._gen_path):
                with open(self._gen_path) as f:
                    return json.load(f)
        except Exception:
            pass
        return {"counter": 0}

    def _save_gen_state(self) -> None:
        """Atomically save generation state to sidecar JSON."""
        try:
            tmp = self._gen_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"counter": self._counter}, f)
            os.replace(tmp, self._gen_path)
        except Exception:
            pass

    def add(self, item: str) -> None:
        """
        Add item hash to active filter. Rotate if active is full.

        Args:
            item: URL or fingerprint string to add.
        """
        if self._counter >= self._capacity:
            self._rotate()

        with self._lock:
            if self._active is not None:
                try:
                    self._active.add(item)
                    self._counter += 1
                except Exception:
                    pass

    def contains(self, item: str) -> bool:
        """
        Check both active and previous filters.

        Args:
            item: URL or fingerprint string to check.

        Returns:
            True if item was previously added (possible duplicate).
        """
        if self._active is not None:
            try:
                if self._active.__contains__(item):
                    return True
            except Exception:
                pass
        if self._previous is not None:
            try:
                if self._previous.__contains__(item):
                    return True
            except Exception:
                pass
        return False

    def _rotate(self) -> None:
        """Rotate: active → previous, new empty active."""
        try:
            if self._previous is not None:
                self._previous.reset()
            self._active, self._previous = self._previous, self._active
            self._counter = 0
            self._save_gen_state()
        except Exception:
            pass

    def persist(self) -> None:
        """Sync active filter to disk (msync handled by Rust MmapBloomFilter)."""
        if self._active is not None:
            try:
                self._active.sync()
            except Exception:
                pass

    def load(self) -> None:
        """Re-initialize from mmap files (no-op, files are mmapped at init)."""
        # Files are mmapped at __init__ time — no separate load needed
        pass

    def close(self) -> None:
        """Close mmap filters and sync to disk."""
        if self._active is not None:
            try:
                self._active.sync()
            except Exception:
                pass
        self._active = None
        self._previous = None


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
        # P1-3: HLEDAC_DEDUP_LMDB_PATH env var takes precedence over explicit arg
        if _DEDUP_LMDB_PATH:
            self._dedup_lmdb_path_str: str | None = _DEDUP_LMDB_PATH
        else:
            self._dedup_lmdb_path_str: str | None = dedup_lmdb_path
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
            except Exception:
                pass
            self._bloom_filter = None
        self._bloom_previous = None
        self._bloom_filter_error = None

        # F267: Close mmap IOC dedup store
        if self._ioc_dedup_store is not None:
            try:
                msync = getattr(self._ioc_dedup_store, "msync", None)
                if msync:
                    msync()
            except Exception:
                pass
            self._ioc_dedup_store = None
        self._ioc_dedup_store_error = None

        if self._dedup_lmdb is not None:
            try:
                self._dedup_lmdb.close()
            except Exception:
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
                # P1-3: HLEDAC_DEDUP_LMDB_PATH env var takes precedence
                if _DEDUP_LMDB_PATH:
                    self._dedup_lmdb_path_str = _DEDUP_LMDB_PATH
                else:
                    from hledac.universal.paths import LMDB_STORE_ROOT
                    dedup_path = LMDB_STORE_ROOT / "dedup.lmdb"
                    dedup_path.mkdir(parents=True, exist_ok=True)
                    self._dedup_lmdb_path_str = str(dedup_path)

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

            # Resolve mmap file directory from dedup LMDB path
            if self._dedup_lmdb_path_str:
                import os
                base_dir = os.path.dirname(self._dedup_lmdb_path_str) or str(
                    __import__("hledac.universal.paths", fromlist=["LMDB_STORE_ROOT"]).LMDB_STORE_ROOT
                )
            else:
                from hledac.universal.paths import LMDB_STORE_ROOT
                base_dir = str(LMDB_STORE_ROOT)

            import os as _os
            _os.makedirs(base_dir, exist_ok=True)
            active_path = _os.path.join(base_dir, "dedup_bloom_active.mmap")
            previous_path = _os.path.join(base_dir, "dedup_bloom_previous.mmap")

            # Two-generation Bloom: active + previous
            self._bloom_filter = MmapBloomFilter(
                active_path, 100_000, 0.001, force_new=False
            )
            # Previous generation (reuse if exists)
            if _os.path.exists(previous_path):
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
            if self._dedup_lmdb_path_str:
                import os
                base_dir = os.path.dirname(self._dedup_lmdb_path_str) or str(
                    __import__("hledac.universal.paths", fromlist=["LMDB_STORE_ROOT"]).LMDB_STORE_ROOT
                )
            else:
                from hledac.universal.paths import LMDB_STORE_ROOT
                base_dir = str(LMDB_STORE_ROOT)

            import os as _os
            _os.makedirs(base_dir, exist_ok=True)
            ioc_path = _os.path.join(base_dir, "ioc_dedup.mmap")
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
            except Exception:
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
            except Exception:
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
                except Exception:
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
        """Lazy-load hot cache max size."""
        return _load_dedup_hot_cache_max()

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
        except Exception:
            pass

        try:
            if self._semantic_lmdb_path is None:
                from hledac.universal.paths import LMDB_STORE_ROOT
                lmdb_path = str(LMDB_STORE_ROOT / "semantic_dedup.lmdb")
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
