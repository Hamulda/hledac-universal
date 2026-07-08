"""core/storage_router.py — Storage Policy & Router (P1-04)

5-Layer Storage Stack with single decision tree, tiered invalidation,
and M1 8GB memory budget enforcement via ResourceGovernor.

LAYERS (hot → cold):
  HOT      — EmbeddingCache (np.memmap float16, L1+L2 LRU, 512 MB cap)
  WARM     — LanceDB (Rust HNSW, disk-backed ANN, IVF-PQ quantized)
  COLD     — DuckDB (columnar SQL, IOC history, graph analytics)
  KEYVALUE — LMDB (Q-tables, hot-edges cache, ephemeral metadata)
  STRING   — diskcache / file cache (URLs, HTML, safetensors)

DECISION TREE (data_kind string → StorageKind):
  "embedding.float16[256]"       → HOT
  "embedding.float32[768]"        → WARM (spills from HOT on emergency)
  "ioc.findings"                  → COLD
  "qtable.federated"             → KEYVALUE (5s debounce, TTL 24h)
  "url.normalized"                → STRING (TTL 1h)
  "graph.ioc"                     → COLD
  "graph.edges_hot"               → KEYVALUE
  "kv.persistent"                 → KEYVALUE
  "safetensors.kv_cache"          → STRING
  default                         → COLD

TIERED INVALIDATION:
  HOT evict  → NOTIFY WARM (re-index)
  WARM evict → NOTIFY COLD (re-derive)
  COLD delete → NOTIFY KEYVALUE (remove derived keys)

M1 8GB BOUNDS:
  HOT:      512 MB (np.memmap float16)
  WARM:     8 GB (LanceDB IVF-PQ, disk-backed)
  COLD:     16 GB (DuckDB file-backed mmap)
  KEYVALUE: 128 MB LMDB (Q-tables, hot-edges)
  STRING:   256 MB diskcache

INVARIANTS:
  - Always-on, no feature flags
  - Fail-safe: every backend wraps in try/except, returns None on miss
  - Bounded: each backend has explicit max_bytes + entry TTL
  - ResourceGovernor-aware: emergency pressure → HOT spills to WARM
  - No new public APIs outside this module
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.resource_governor import M1ResourceGovernor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Storage Kind Taxonomy
# ---------------------------------------------------------------------------

class StorageKind(str, Enum):
    """5-layer storage taxonomy, hot→cold."""

    HOT = "hot"       # np.memmap float16 LRU (RAM-resident)
    WARM = "warm"     # LanceDB HNSW (disk-backed ANN)
    COLD = "cold"     # DuckDB columnar (durable SQL)
    KEYVALUE = "kv"   # LMDB (ephemeral key-value)
    STRING = "string" # diskcache (URLs, HTML, safetensors)


# ---------------------------------------------------------------------------
# Storage Policy
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class StoragePolicy:
    """Decision policy: which storage for which data type."""

    kind: StorageKind
    max_bytes: int
    ttl_seconds: float | None
    persist: bool            # survives process restart
    replication: int = 1     # 1=single, 2+=replica (future)
    spill_target: StorageKind | None = None   # where to spill on OOM
    invalidates: tuple[StorageKind, ...] = ()  # downstream layers

    def __repr__(self) -> str:
        return (
            f"StoragePolicy({self.kind.value}, "
            f"max_bytes={self.max_bytes // 1024 // 1024}MB, "
            f"ttl={self.ttl_seconds}s, persist={self.persist})"
        )


# ---------------------------------------------------------------------------
# Decision Matrix
# ---------------------------------------------------------------------------

# Hot-path policies (always-resident on M1 8GB)
_POLICY_HOT_EMBEDDING_256: StoragePolicy = StoragePolicy(
    kind=StorageKind.HOT,
    max_bytes=512 * 1024 * 1024,  # 512 MB
    ttl_seconds=None,
    persist=False,
    spill_target=StorageKind.WARM,
    invalidates=(StorageKind.WARM,),
)

_POLICY_WARM_EMBEDDING_768: StoragePolicy = StoragePolicy(
    kind=StorageKind.WARM,
    max_bytes=8 * 1024**3,  # 8 GB
    ttl_seconds=None,
    persist=True,
    invalidates=(StorageKind.COLD,),
)

# Analytics / IOC (durable columnar)
_POLICY_IOC_FINDINGS: StoragePolicy = StoragePolicy(
    kind=StorageKind.COLD,
    max_bytes=16 * 1024**3,  # 16 GB
    ttl_seconds=None,
    persist=True,
)

_POLICY_GRAPH_IOC: StoragePolicy = StoragePolicy(
    kind=StorageKind.COLD,
    max_bytes=32 * 1024**3,  # 32 GB
    ttl_seconds=None,
    persist=True,
)

# Key-value ephemeral (low-latency lookups)
_POLICY_QTABLE: StoragePolicy = StoragePolicy(
    kind=StorageKind.KEYVALUE,
    max_bytes=128 * 1024 * 1024,  # 128 MB LMDB
    ttl_seconds=86400.0,  # 24h TTL
    persist=True,
)

_POLICY_HOT_EDGES: StoragePolicy = StoragePolicy(
    kind=StorageKind.KEYVALUE,
    max_bytes=32 * 1024 * 1024,  # 32 MB LMDB
    ttl_seconds=None,
    persist=True,
)

_POLICY_KV_PERSISTENT: StoragePolicy = StoragePolicy(
    kind=StorageKind.KEYVALUE,
    max_bytes=64 * 1024 * 1024,  # 64 MB
    ttl_seconds=3600.0,  # 1h
    persist=True,
)

# String caches (URLs, HTML, safetensors)
_POLICY_URL_NORMALIZED: StoragePolicy = StoragePolicy(
    kind=StorageKind.STRING,
    max_bytes=256 * 1024 * 1024,  # 256 MB diskcache
    ttl_seconds=3600.0,  # 1h
    persist=False,
)

_POLICY_SAFETENSORS: StoragePolicy = StoragePolicy(
    kind=StorageKind.STRING,
    max_bytes=1024 * 1024 * 1024,  # 1 GB disk
    ttl_seconds=7 * 86400.0,  # 7 days
    persist=True,
)

# Default fallback
_POLICY_DEFAULT: StoragePolicy = StoragePolicy(
    kind=StorageKind.COLD,
    max_bytes=16 * 1024**3,
    ttl_seconds=None,
    persist=True,
)

_DECISION_MATRIX: dict[str, StoragePolicy] = {
    # Embeddings (hot path) — fnmatch patterns where brackets are literal
    # (brackets in data_kind are literal, not char-classes, so prefix match is safe)
    "embedding.float16[256]": _POLICY_HOT_EMBEDDING_256,
    "embedding.float16[384]": _POLICY_HOT_EMBEDDING_256,
    "embedding.float16[*": _POLICY_HOT_EMBEDDING_256,   # prefix: embedding.float16[anything
    "embedding.float32[768]": _POLICY_WARM_EMBEDDING_768,
    "embedding.float32[1024]": _POLICY_WARM_EMBEDDING_768,
    "embedding.float32[*": _POLICY_WARM_EMBEDDING_768,  # prefix: embedding.float32[anything
    # IOC / Analytics
    "ioc.findings": _POLICY_IOC_FINDINGS,
    "ioc.findings.bulk": _POLICY_IOC_FINDINGS,
    "graph.ioc": _POLICY_GRAPH_IOC,
    "graph.entities": _POLICY_GRAPH_IOC,
    # Key-value
    "qtable.federated": _POLICY_QTABLE,
    "qtable.*": _POLICY_QTABLE,
    "graph.edges_hot": _POLICY_HOT_EDGES,
    "kv.persistent": _POLICY_KV_PERSISTENT,
    # String
    "url.normalized": _POLICY_URL_NORMALIZED,
    "url.*": _POLICY_URL_NORMALIZED,
    "safetensors.kv_cache": _POLICY_SAFETENSORS,
}


def _classify(data_kind: str) -> StoragePolicy:
    """Decision tree: data_kind string → StoragePolicy."""
    if data_kind in _DECISION_MATRIX:
        return _DECISION_MATRIX[data_kind]
    for key, policy in _DECISION_MATRIX.items():
        if key.endswith("[*"):
            # Prefix match for bracket-containing keys: "embedding.float16[*" matches "embedding.float16[512]"
            prefix = key[:-1]  # "embedding.float16[" → "embedding.float16["
            if data_kind.startswith(prefix):
                return policy
        elif "*" in key and fnmatch.fnmatch(data_kind, key):
            return policy
    return _POLICY_DEFAULT


# ---------------------------------------------------------------------------
# Invalidation Registry
# ---------------------------------------------------------------------------

_INVALIDATION_CHAIN: dict[StorageKind, tuple[StorageKind, ...]] = {
    StorageKind.HOT: (StorageKind.WARM,),
    StorageKind.WARM: (StorageKind.COLD,),
    StorageKind.COLD: (StorageKind.KEYVALUE,),
    StorageKind.KEYVALUE: (),
    StorageKind.STRING: (),
}


# ---------------------------------------------------------------------------
# Storage Router
# ---------------------------------------------------------------------------


class StorageRouter:
    """
    Routes data to appropriate storage backend.

    Single decision tree: classify(data_kind) → StoragePolicy → backend.

    M1 8GB: enforces memory budget per backend via M1ResourceGovernor.
    Emergency pressure → HOT spills to WARM (embedding float16 → float32).

    FAIL-SAFE: every put/get wrapped in try/except; returns None on miss.
    Never raises. Telemetry records every miss.

    THREAD SAFETY:
      - Single _state_lock: threading.RLock serializes ALL operations (put, get,
        delete) and protects LMDB access. Replaces 3 locks:
          * _sync_lock — REMOVED
          * _backend_locks[StorageKind] — REMOVED (5 locks, unnecessary)
          * _router_lock — module-level singleton guard (separate asyncio.Lock)
      - RLock allows same-thread re-entry: get() → put() during HOT→WARM spill.
      - DuckDBShadowStore has its own _write_semaphore (WAL+DuckDB pair).
      - LanceDBVectorStore has its own _upsert_lock (upsert operations).
      - aput/aget/adelete use run_in_executor → threading.RLock works across threads.
    """

    def __init__(
        self,
        governor: M1ResourceGovernor | None = None,
        hot_cache: Any | None = None,
        warm_store: Any | None = None,
        cold_store: Any | None = None,
        kv_store: Any | None = None,
        string_store: Any | None = None,
    ) -> None:
        self._governor = governor
        self._backends: dict[StorageKind, Any] = {
            StorageKind.HOT: hot_cache,
            StorageKind.WARM: warm_store,
            StorageKind.COLD: cold_store,
            StorageKind.KEYVALUE: kv_store,
            StorageKind.STRING: string_store,
        }
        # Single RLock: replaces _sync_lock + _backend_locks[5].
        # RLock allows same-thread re-entry for get()→put() spill path.
        self._state_lock: threading.RLock = threading.RLock()
        # Invalidation subscriptions: StorageKind → list of callbacks
        self._invalidation_subscribers: dict[StorageKind, list] = {
            StorageKind.HOT: [],
            StorageKind.WARM: [],
            StorageKind.COLD: [],
            StorageKind.KEYVALUE: [],
            StorageKind.STRING: [],
        }
        # Telemetry
        self._stats: dict[str, Any] = {
            "puts": 0,
            "gets": 0,
            "misses": 0,
            "spills": 0,
            "invalidations": 0,
        }

    # ------------------------------------------------------------------
    # Backend registration
    # ------------------------------------------------------------------

    def register_backend(self, kind: StorageKind, backend: Any) -> None:
        """Register or replace a storage backend for a given StorageKind."""
        self._backends[kind] = backend

    def register_invalidation_callback(
        self, kind: StorageKind, callback: Any
    ) -> None:
        """Subscribe a callback to invalidation events for a StorageKind."""
        if kind in self._invalidation_subscribers:
            self._invalidation_subscribers[kind].append(callback)

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    def classify(self, data_kind: str) -> StoragePolicy:
        """Decision tree: data_kind string → StoragePolicy."""
        return _classify(data_kind)

    # ------------------------------------------------------------------
    # Memory pressure response
    # ------------------------------------------------------------------

    def _spill_policy(self, policy: StoragePolicy) -> StoragePolicy:
        """On emergency pressure, spill HOT → WARM for embeddings."""
        if self._governor is None:
            return policy
        try:
            uma_state = self._governor.sample_uma_status()
            if uma_state.uma_state in ("emergency", "critical"):
                if policy.kind == StorageKind.HOT and policy.spill_target:
                    logger.warning(
                        "[StorageRouter] emergency pressure — spilling %s → %s",
                        policy.kind.value,
                        policy.spill_target.value,
                    )
                    self._stats["spills"] += 1
                    return _DECISION_MATRIX.get(
                        "embedding.float32[768]", _POLICY_WARM_EMBEDDING_768
                    )
        except Exception:
            pass
        return policy

    # ------------------------------------------------------------------
    # Core put / get
    # ------------------------------------------------------------------

    def put(self, key: str, value: Any, *, data_kind: str) -> bool:
        """
        Route put to appropriate backend.

        Args:
            key: storage key
            value: value to store (type varies by backend)
            data_kind: classification string (e.g. "embedding.float16[256]")

        Returns:
            True if stored, False on error/miss.
        """
        self._stats["puts"] += 1
        # Thread-safe write serialization via threading.RLock.
        # Protects against concurrent put() from multiple ThreadPoolExecutor workers.
        with self._state_lock:
            try:
                base_policy = self.classify(data_kind)
                policy = self._spill_policy(base_policy)
                backend = self._backends.get(policy.kind)

                if backend is None:
                    # No backend registered — but notify invalidation chain anyway.
                    # Subscribers may hold derived data keyed on (key, kind) that
                    # is now stale regardless of whether we persisted anything.
                    self._notify_invalidation(policy.kind, key)
                    return False

                stored = self._backend_put(backend, key, value)
                # Notify invalidation chain based on policy.kind, not on whether
                # a backend happened to store the value. Even if put() returns
                # False (backend full, error, etc.), downstream subscribers holding
                # derived data need to know this (key, kind) was presented.
                self._notify_invalidation(policy.kind, key)
                return stored
            except Exception as e:
                logger.debug("[StorageRouter] put failed for %s: %s", key, e)
                return False

    def get(self, key: str, *, data_kind: str) -> Any:
        """
        Route get to appropriate backend (try HOT → WARM → COLD cascade).

        Returns:
            Stored value or None on miss.
        """
        self._stats["gets"] += 1
        # ISSUE-026: Read path uses async lock to prevent concurrent writes
        # during read (especially important for LMDB which is not async-safe).
        base_policy = self.classify(data_kind)
        policy = self._spill_policy(base_policy)

        candidates = [policy.kind]
        if policy.kind == StorageKind.HOT:
            candidates.extend([StorageKind.WARM, StorageKind.COLD])
        elif policy.kind == StorageKind.KEYVALUE:
            candidates.append(StorageKind.COLD)

        for kind in candidates:
            backend = self._backends.get(kind)
            if backend is None:
                continue
            # Per-backend lock via single _state_lock for LMDB key-value access.
            # Single lock is safe: reads don't mutate state, LMDB is single-writer.
            try:
                with self._state_lock:
                    value = self._backend_get(backend, key)
            except Exception as e:
                logger.debug(
                    "[StorageRouter] get miss kind=%s key=%s: %s",
                    kind.value,
                    key,
                    e,
                )
                continue
            if value is not None:
                if kind != policy.kind:
                    self.put(key, value, data_kind=data_kind)
                return value

        self._stats["misses"] += 1
        return None

    def delete(self, key: str, *, data_kind: str) -> bool:
        """
        Delete from primary layer + fire invalidation chain.

        Returns:
            True if deleted, False otherwise.
        """
        # Thread-safe delete serialization via threading.RLock.
        with self._state_lock:
            policy = self.classify(data_kind)
            backend = self._backends.get(policy.kind)
            deleted = False
            if backend is not None:
                try:
                    deleted = self._backend_delete(backend, key)
                except Exception as e:
                    logger.debug("[StorageRouter] delete failed %s: %s", key, e)

            if deleted:
                self._notify_invalidation(policy.kind, key)
            return deleted

    # ------------------------------------------------------------------
    # Backend operations (polymorphic)
    # ------------------------------------------------------------------

    def _backend_put(self, backend: Any, key: str, value: Any, _policy: StoragePolicy | None = None) -> bool:
        """Call backend.put()/set()/upsert()/store(). Fail-safe."""
        try:
            if hasattr(backend, "put"):
                result = backend.put(key, value)
                return result is not False  # True/None/MagicMock = success; False = failure
            if hasattr(backend, "set"):
                result = backend.set(key, value)
                return result is not False
            if hasattr(backend, "upsert"):
                backend.upsert(key, value)
                return True
            if hasattr(backend, "store"):
                backend.store(key, value)
                return True
            logger.warning(
                "[StorageRouter] backend %s has no put/set/upsert/store",
                type(backend).__name__,
            )
            return False
        except Exception as e:
            logger.debug("[StorageRouter] backend put failed: %s", e)
            return False

    def _backend_get(self, backend: Any, key: str) -> Any:
        """Call backend.get()/lookup()/fetch(). Fail-safe."""
        try:
            if hasattr(backend, "get"):
                return backend.get(key)
            if hasattr(backend, "lookup"):
                return backend.lookup(key)
            if hasattr(backend, "fetch"):
                return backend.fetch(key)
            return None
        except Exception as e:
            logger.debug("[StorageRouter] backend get failed: %s", e)
            return None

    def _backend_delete(self, backend: Any, key: str) -> bool:
        """Call backend.delete()/remove(). Fail-safe."""
        try:
            if hasattr(backend, "delete"):
                result = backend.delete(key)
                return result is True or result is None
            if hasattr(backend, "remove"):
                backend.remove(key)
                return True
            return False
        except Exception as e:
            logger.debug("[StorageRouter] backend delete failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # ISSUE-026: Async put/get wrappers for async contexts
    # ------------------------------------------------------------------

    async def aput(self, key: str, value: Any, *, data_kind: str) -> bool:
        """
        Async put — runs self.put() in a thread to avoid blocking event loop.

        Args:
            key: storage key
            value: value to store
            data_kind: classification string

        Returns:
            True if stored, False on error/miss.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self.put(key, value, data_kind=data_kind)
        )

    async def aget(self, key: str, *, data_kind: str) -> Any:
        """
        Async get — runs self.get() in a thread to avoid blocking event loop.

        Args:
            key: storage key
            data_kind: classification string

        Returns:
            Stored value or None on miss.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self.get(key, data_kind=data_kind)
        )

    async def adelete(self, key: str, *, data_kind: str) -> bool:
        """
        Async delete — runs self.delete() in a thread to avoid blocking event loop.

        Args:
            key: storage key
            data_kind: classification string

        Returns:
            True if deleted, False otherwise.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self.delete(key, data_kind=data_kind)
        )

    # ------------------------------------------------------------------
    # Invalidation chain
    # ------------------------------------------------------------------

    def _notify_invalidation(self, source_kind: StorageKind, key: str) -> None:
        """Fire invalidation callbacks for all downstream layers."""
        downstream = _INVALIDATION_CHAIN.get(source_kind, ())
        for kind in downstream:
            for callback in self._invalidation_subscribers.get(kind, []):
                try:
                    callback(key, source_kind=source_kind)
                    self._stats["invalidations"] += 1
                except Exception as e:
                    logger.debug(
                        "[StorageRouter] invalidation callback failed %s.%s: %s",
                        kind.value,
                        key,
                        e,
                    )

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return router telemetry + per-backend stats."""
        stats = dict(self._stats)
        for kind, backend in self._backends.items():
            if backend is not None and hasattr(backend, "get_stats"):
                try:
                    stats[f"backend.{kind.value}"] = backend.get_stats()
                except Exception:
                    pass
        return stats


# ---------------------------------------------------------------------------
# Singleton access
# ---------------------------------------------------------------------------

_router: StorageRouter | None = None
_router_lock = asyncio.Lock()


async def get_storage_router(
    governor: M1ResourceGovernor | None = None,
) -> StorageRouter:
    """Get or create the global StorageRouter singleton."""
    global _router
    async with _router_lock:
        if _router is None:
            _router = StorageRouter(governor=governor)
        return _router


def reset_storage_router() -> None:
    """Reset router singleton (for testing)."""
    global _router
    _router = None
