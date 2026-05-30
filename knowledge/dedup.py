"""
Dedup Manager — Sprint F216G refactor
====================================

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
__all__ = ["DedupManager", "RotatingBloomFilter"]

import hashlib
import os
import struct
from typing import Any

# Sprint 8AG §6.17: Default dedup LMDB map size
_DEDUP_LMDB_MAP_SIZE: int = 64 * 1024 * 1024  # 64MB
# Sprint F216G: Same constant imported from quality_assessment for hot cache cap
_DEDUP_HOT_CACHE_MAX: int = 10000  # will be overridden by quality_assessment import


def _load_dedup_hot_cache_max() -> int:
    """Lazy-load DEDUP_HOT_CACHE_MAX from quality_assessment."""
    try:
        from .quality_assessment import _DEDUP_HOT_CACHE_MAX
        return _DEDUP_HOT_CACHE_MAX
    except ImportError:
        return 10000


class RotatingBloomFilter:
    """
    Cross-run URL dedup pre-check. Sprint F222F.

    Two-generation bloom filter:
    - active: current generation, being written to
    - previous: previous generation, read-only for lookups

    When active reaches capacity, rotate: active becomes previous, new active created.
    This prevents unbounded memory growth while maintaining dedup across many runs.

    Pure Python implementation using hashlib with multiple salt prefixes.
    """

    BLOOM_KEY_ACTIVE: str = "bloom_active"
    BLOOM_KEY_PREVIOUS: str = "bloom_previous"
    BLOOM_KEY_COUNTER: str = "bloom_counter"

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
            lmdb_path: Path to LMDB for persistence. If None, uses default.
        """
        self._capacity = capacity
        self._fp_rate = fp_rate

        # Calculate optimal bit count and hash count
        # bit_count = -capacity * log(fp_rate) / (log(2)^2)
        import math
        self._bit_count = int(-capacity * math.log(fp_rate) / (math.log(2) ** 2))
        # hash_count = (bit_count / capacity) * log(2)
        self._hash_count = max(1, int((self._bit_count / capacity) * math.log(2)))
        self._byte_count = (self._bit_count + 7) // 8

        # LMDB path
        if lmdb_path is None:
            from hledac.universal.paths import LMDB_ROOT
            lmdb_path = str(LMDB_ROOT / "bloom_filter.lmdb")
        self._lmdb_path = lmdb_path
        self._lmdb_env: Any | None = None

        # In-memory bitsets
        self._active: bytearray = bytearray(self._byte_count)
        self._previous: bytearray | None = None
        self._counter: int = 0

        # Salt prefixes for multiple hash functions
        self._salts = [f"hledac_bloom_gen{i}_" for i in range(self._hash_count)]

    def _init_lmdb(self) -> None:
        """Initialize LMDB for persistence."""
        if self._lmdb_env is not None:
            return
        try:
            import lmdb
            path = os.path.dirname(self._lmdb_path)
            if path:
                os.makedirs(path, exist_ok=True)
            self._lmdb_env = lmdb.open(self._lmdb_path, map_size=64 * 1024 * 1024)
        except Exception:
            self._lmdb_env = None

    def _set_bit(self, bitset: bytearray, bit: int) -> None:
        """Set a bit in the bitset."""
        bitset[bit // 8] |= 1 << (bit % 8)

    def _get_bit(self, bitset: bytearray, bit: int) -> bool:
        """Get a bit from the bitset."""
        return bool(bitset[bit // 8] & (1 << (bit % 8)))

    def _hash_n(self, item: str, salt: str) -> int:
        """Compute hash for item with salt, return int in range [0, bit_count)."""
        h = hashlib.blake2b(f"{salt}{item}".encode(), digest_size=8).digest()
        return struct.unpack("<Q", h)[0] % self._bit_count

    def _hashes(self, item: str) -> list[int]:
        """Compute all hash values for an item."""
        return [self._hash_n(item, salt) for salt in self._salts]

    def add(self, item: str) -> None:
        """
        Add item hash to active filter. Rotate if active is full.

        Args:
            item: URL or fingerprint string to add.
        """
        if self._counter >= self._capacity:
            self._rotate()

        for h in self._hashes(item):
            self._set_bit(self._active, h)
        self._counter += 1

    def contains(self, item: str) -> bool:
        """
        Check both active and previous filters.

        Args:
            item: URL or fingerprint string to check.

        Returns:
            True if item was previously added (possible duplicate).
        """
        for h in self._hashes(item):
            if not self._get_bit(self._active, h):
                return False
            if self._previous is not None and not self._get_bit(self._previous, h):
                return False
        return True

    def _rotate(self) -> None:
        """Rotate: active → previous, new empty active."""
        self._previous = self._active
        self._active = bytearray(self._byte_count)
        self._counter = 0

    def persist(self) -> None:
        """Save both filters to LMDB."""
        self._init_lmdb()
        if self._lmdb_env is None:
            return
        try:
            with self._lmdb_env.begin(write=True) as txn:
                txn.put(self.BLOOM_KEY_ACTIVE.encode(), bytes(self._active))
                txn.put(
                    self.BLOOM_KEY_PREVIOUS.encode(),
                    bytes(self._previous) if self._previous else b""
                )
                txn.put(self.BLOOM_KEY_COUNTER.encode(), struct.pack("<Q", self._counter))
        except Exception:
            pass

    def load(self) -> None:
        """Load from LMDB at startup."""
        self._init_lmdb()
        if self._lmdb_env is None:
            return
        try:
            with self._lmdb_env.begin(write=False) as txn:
                active = txn.get(self.BLOOM_KEY_ACTIVE.encode())
                if active:
                    self._active = bytearray(active)
                previous = txn.get(self.BLOOM_KEY_PREVIOUS.encode())
                if previous:
                    self._previous = bytearray(previous)
                counter = txn.get(self.BLOOM_KEY_COUNTER.encode())
                if counter:
                    self._counter = struct.unpack("<Q", counter)[0]
        except Exception:
            pass

    def close(self) -> None:
        """Close LMDB and persist data."""
        if self._lmdb_env is not None:
            self.persist()
            try:
                self._lmdb_env.close()
            except Exception:
                pass
            self._lmdb_env = None


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
            dedup_lmdb_path: Path to dedup LMDB. If None, resolved from LMDB_ROOT.
            semantic_lmdb_path: Path to semantic dedup LMDB. If None, uses default.
            map_size: LMDB map size in bytes for dedup store.
            max_keys: Max keys in dedup LMDB.
        """
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

        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Initialize persistent dedup LMDB and semantic dedup cache."""
        if self._initialized:
            return

        self._init_persistent_dedup_lmdb()
        self._init_semantic_dedup_cache()
        self._initialized = True

    def close(self) -> None:
        """Close all LMDB stores."""
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
                from hledac.universal.paths import LMDB_ROOT
                dedup_path = LMDB_ROOT / "dedup.lmdb"
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

    def _dedup_key_from_fingerprint(self, fp: str) -> bytes:
        """Build dedup namespace key from BLAKE2b fingerprint."""
        return f"{self.DEDUP_NAMESPACE}{fp}".encode()

    def _dedup_lmdb_key_to_fingerprint(self, key: bytes) -> str:
        """Extract fingerprint from dedup namespace key."""
        return key.decode("utf-8")[len(self.DEDUP_NAMESPACE):]

    def lookup_persistent_dedup(self, fp: str) -> str | None:
        """
        Lookup a fingerprint in the persistent dedup LMDB.

        LMDB remains authoritative.

        Args:
            fp: 32-char BLAKE2b fingerprint hex string

        Returns:
            finding_id string if found, None otherwise (miss or LMDB unavailable)
        """
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

        Args:
            fp: 32-char BLAKE2b fingerprint hex string
            finding_id: canonical finding ID
        """
        if self._dedup_lmdb is None:
            return
        try:
            key = self._dedup_key_from_fingerprint(fp)
            value_bytes = finding_id.encode("utf-8")
            with self._dedup_lmdb._env.begin(write=True) as txn:
                txn.put(key, value_bytes)
        except Exception as e:
            self._dedup_lmdb_last_error = f"store failed for fp={fp[:8]}: {e}"

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
                from hledac.universal.paths import LMDB_ROOT
                lmdb_path = str(LMDB_ROOT / "semantic_dedup.lmdb")
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
