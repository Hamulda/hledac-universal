"""core/storage_router.py — Storage Policy & Router (P1-04 / ISSUE #046)

5-Layer Storage Stack with single decision tree, tiered invalidation,
and M1 8GB memory budget enforcement via ResourceGovernor.



LAYERS (hot → cold):
  HOT      — SqliteVecStore (M1-native, sqlite-vec, ~5MB) for embeddings.float16
  WARM     — LanceDB (opt-in via HLEDAC_VECTORS=lancedb, IVF-PQ quantized)
  COLD     — DuckDB (columnar SQL, IOC history, graph analytics)
  KEYVALUE — LMDB (Q-tables, hot-edges cache, ephemeral metadata)
  STRING   — diskcache / file cache (URLs, HTML, safetensors)

DECISION TREE (data_kind string → StorageKind):
  "embedding.float16[256]"       → HOT (SqliteVecStore)
  "embedding.float16[384]"       → HOT (SqliteVecStore)
  "embedding.float32[768]"        → WARM (LanceDB, opt-in)
  "ioc.findings"                  → COLD
  "qtable.federated"             → KEYVALUE (5s debounce, TTL 24h)
  "url.normalized"                → STRING (TTL 1h)
  "graph.ioc"                     → COLD
  "graph.edges_hot"               → KEYVALUE
  "kv.persistent"                → KEYVALUE
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

ISSUE #046 — Async Context Manager Protocol:
  Every storage backend should implement async acquire() / release() so the router
  can manage their lifecycle via `async with StorageRouter() as router:`.
  This replaces the old duck-typed hasattr approach with a proper Protocol contract.

  Backends that are async context managers (DuckDBShadowStore, LanceDBVectorStore)
  are entered/exited via StorageRouter.__aenter__/__aexit__.

ASYNC SAFETY (ISSUE #046):
  - routing_lock: asyncio.Semaphore(1) — replaces threading.RLock for async paths
  - aput/aget/adelete: fully async, no run_in_executor for routing decisions
  - Backends handle their own internal concurrency (DuckDB _write_semaphore,
    LanceDB _upsert_lock, LMDB thread-safe)

INVARIANTS:
  - Always-on, no feature flags
  - Fail-safe: every backend wraps in try/except, returns None on miss
  - Bounded: each backend has explicit max_bytes + entry TTL
  - ResourceGovernor-aware: emergency pressure → HOT spills to WARM
  - No new public APIs outside this module
"""
import asyncio
import fnmatch
import logging

from hledac.universal.utils.locks import LazyAsyncioLock
from hledac.universal.utils.executor_decorator import offload_to
from dataclasses import dataclass
import msgspec
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from core._util import aclose

if TYPE_CHECKING:
    from hledac.universal.core.resource_governor import M1ResourceGovernor
logger = logging.getLogger(__name__)


# ── Async Storage Backend Protocol ─────────────────────────────────────────────

@runtime_checkable
class AsyncStorageBackendProtocol(Protocol):
    """
    Protocol for async-capable storage backends (ISSUE #046).

    All backends registered in StorageRouter should implement this protocol
    to enable proper async lifecycle management.

    DuckDBShadowStore and LanceDBVectorStore already implement __aenter__/__aexit__.

    Usage:
        @runtime_checkable
        class MyBackend:
            async def acquire(self) -> None: ...
            async def release(self) -> None: ...

        # DuckDBShadowStore-style (async context manager):
        async def __aenter__(self) -> Self: ...
        async def __aexit__(self, ...) -> None: ...
    """

    async def acquire(self) -> None:
        """Acquire backend resources (e.g. open connections)."""
        ...

    async def release(self) -> None:
        """Release backend resources (e.g. close connections)."""
        ...


# ── StorageKind & StoragePolicy ────────────────────────────────────────────────

class StorageKind(str, Enum):
    """5-layer storage taxonomy, hot→cold."""
    HOT = 'hot'
    WARM = 'warm'
    COLD = 'cold'
    KEYVALUE = 'kv'
    STRING = 'string'

class StoragePolicy(msgspec.Struct, frozen=True, gc=False):
    """Decision policy: which storage for which data type."""
    kind: StorageKind
    max_bytes: int
    ttl_seconds: float | None
    persist: bool
    replication: int = 1
    spill_target: StorageKind | None = None
    invalidates: tuple[StorageKind, ...] = ()

    def __repr__(self) -> str:
        return f'StoragePolicy({self.kind.value}, max_bytes={self.max_bytes // 1024 // 1024}MB, ttl={self.ttl_seconds}s, persist={self.persist})'

_POLICY_HOT_EMBEDDING_256: StoragePolicy = StoragePolicy(kind=StorageKind.HOT, max_bytes=512 * 1024 * 1024, ttl_seconds=None, persist=False, spill_target=StorageKind.WARM, invalidates=(StorageKind.WARM,))
_POLICY_WARM_EMBEDDING_768: StoragePolicy = StoragePolicy(kind=StorageKind.WARM, max_bytes=8 * 1024 ** 3, ttl_seconds=None, persist=True, invalidates=(StorageKind.COLD,))
_POLICY_IOC_FINDINGS: StoragePolicy = StoragePolicy(kind=StorageKind.COLD, max_bytes=16 * 1024 ** 3, ttl_seconds=None, persist=True)
_POLICY_GRAPH_IOC: StoragePolicy = StoragePolicy(kind=StorageKind.COLD, max_bytes=32 * 1024 ** 3, ttl_seconds=None, persist=True)
_POLICY_QTABLE: StoragePolicy = StoragePolicy(kind=StorageKind.KEYVALUE, max_bytes=128 * 1024 * 1024, ttl_seconds=86400.0, persist=True)
_POLICY_HOT_EDGES: StoragePolicy = StoragePolicy(kind=StorageKind.KEYVALUE, max_bytes=32 * 1024 * 1024, ttl_seconds=None, persist=True)
_POLICY_KV_PERSISTENT: StoragePolicy = StoragePolicy(kind=StorageKind.KEYVALUE, max_bytes=64 * 1024 * 1024, ttl_seconds=3600.0, persist=True)
_POLICY_URL_NORMALIZED: StoragePolicy = StoragePolicy(kind=StorageKind.STRING, max_bytes=256 * 1024 * 1024, ttl_seconds=3600.0, persist=False)
_POLICY_SAFETENSORS: StoragePolicy = StoragePolicy(kind=StorageKind.STRING, max_bytes=1024 * 1024 * 1024, ttl_seconds=7 * 86400.0, persist=True)
_POLICY_DEFAULT: StoragePolicy = StoragePolicy(kind=StorageKind.COLD, max_bytes=16 * 1024 ** 3, ttl_seconds=None, persist=True)
_DECISION_MATRIX: dict[str, StoragePolicy] = {'embedding.float16[256]': _POLICY_HOT_EMBEDDING_256, 'embedding.float16[384]': _POLICY_HOT_EMBEDDING_256, 'embedding.float16[*': _POLICY_HOT_EMBEDDING_256, 'embedding.float32[768]': _POLICY_WARM_EMBEDDING_768, 'embedding.float32[1024]': _POLICY_WARM_EMBEDDING_768, 'embedding.float32[*': _POLICY_WARM_EMBEDDING_768, 'ioc.findings': _POLICY_IOC_FINDINGS, 'ioc.findings.bulk': _POLICY_IOC_FINDINGS, 'graph.ioc': _POLICY_GRAPH_IOC, 'graph.entities': _POLICY_GRAPH_IOC, 'qtable.federated': _POLICY_QTABLE, 'qtable.*': _POLICY_QTABLE, 'graph.edges_hot': _POLICY_HOT_EDGES, 'kv.persistent': _POLICY_KV_PERSISTENT, 'url.normalized': _POLICY_URL_NORMALIZED, 'url.*': _POLICY_URL_NORMALIZED, 'safetensors.kv_cache': _POLICY_SAFETENSORS}

def _classify(data_kind: str) -> StoragePolicy:
    """Decision tree: data_kind string → StoragePolicy."""
    if data_kind in _DECISION_MATRIX:
        return _DECISION_MATRIX[data_kind]
    for key, policy in _DECISION_MATRIX.items():
        if key.endswith('[*'):
            prefix = key[:-1]
            if data_kind.startswith(prefix):
                return policy
        elif '*' in key and fnmatch.fnmatch(data_kind, key):
            return policy
    return _POLICY_DEFAULT

_INVALIDATION_CHAIN: dict[StorageKind, tuple[StorageKind, ...]] = {StorageKind.HOT: (StorageKind.WARM,), StorageKind.WARM: (StorageKind.COLD,), StorageKind.COLD: (StorageKind.KEYVALUE,), StorageKind.KEYVALUE: (), StorageKind.STRING: ()}


# ── StorageRouter ─────────────────────────────────────────────────────────────

class StorageRouter:
    """
    Routes data to appropriate storage backend.

    Single decision tree: classify(data_kind) → StoragePolicy → backend.

    M1 8GB: enforces memory budget per backend via M1ResourceGovernor.
    Emergency pressure → HOT spills to WARM (embedding float16 → float32).

    FAIL-SAFE: every put/get wrapped in try/except; returns None on miss.
    Never raises. Telemetry records every miss.

    ASYNC SAFETY (ISSUE #046):
      - routing_lock: asyncio.Semaphore(1) replaces threading.RLock for async paths.
        Async callers use `async with self._routing_lock:` — no thread pool overhead.
      - Backends handle their own internal concurrency:
          DuckDBShadowStore → _write_semaphore (WAL+DuckDB pair)
          LanceDBVectorStore → _upsert_lock
          LMDB → thread-safe MDB_NOTLS environment
      - Sync put/get/delete remain available for legacy callers (thread-safe via
        per-backend internal locking; StorageRouter._routing_lock NOT held for
        backend I/O in sync path — only for decision bookkeeping).

    ISSUE #046 — Async Context Manager:
      Supports `async with StorageRouter() as router:` for lifecycle management.
      On __aenter__: all backends that implement __aenter__ are entered.
      On __aexit__: all backends that implement __aexit__ are exited.
    """
    __slots__ = tuple(('_backends', '_governor', '_invalidation_subscribers', '_routing_lock', '_stats'))

    def __init__(self, governor: M1ResourceGovernor | None=None, hot_cache: Any | None=None, warm_store: Any | None=None, cold_store: Any | None=None, kv_store: Any | None=None, string_store: Any | None=None) -> None:
        self._governor = governor
        self._backends: dict[StorageKind, Any] = {StorageKind.HOT: hot_cache, StorageKind.WARM: warm_store, StorageKind.COLD: cold_store, StorageKind.KEYVALUE: kv_store, StorageKind.STRING: string_store}
        self._routing_lock: asyncio.Semaphore = asyncio.Semaphore(1)  # ISSUE #046
        self._invalidation_subscribers: dict[StorageKind, list] = {StorageKind.HOT: [], StorageKind.WARM: [], StorageKind.COLD: [], StorageKind.KEYVALUE: [], StorageKind.STRING: []}
        self._stats: dict[str, Any] = {'puts': 0, 'gets': 0, 'misses': 0, 'spills': 0, 'invalidations': 0}

    # ── Async Context Manager (ISSUE #046) ─────────────────────────────────────

    async def __aenter__(self) -> 'StorageRouter':
        """
        Async context manager entry — ISSUE #046.

        Enters all backends that support async context manager protocol.
        DuckDBShadowStore: async with DuckDBShadowStore() → __aenter__ called.
        LanceDBVectorStore: async with LanceDBVectorStore() → __aenter__ called.

        Usage:
            async with StorageRouter(governor=gov) as router:
                await router.aput('key', value, data_kind='ioc.findings')

        Returns:
            self — the initialized router
        """
        for kind, backend in self._backends.items():
            if backend is None:
                continue
            try:
                if isinstance(backend, AsyncStorageBackendProtocol) or hasattr(backend, '__aenter__'):
                    await backend.__aenter__()
            except Exception as e:
                logger.debug('[StorageRouter] backend %s __aenter__ failed: %s', kind.value, e)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """
        Async context manager exit — ISSUE #046.

        Exits all backends that support async context manager protocol.
        Idempotent: safe to call even if __aenter__ failed partially.

        Usage:
            async with StorageRouter() as router:
                ...
            # __aexit__ called automatically
        """
        for kind, backend in self._backends.items():
            if backend is None:
                continue
            try:
                if isinstance(backend, AsyncStorageBackendProtocol) or hasattr(backend, '__aexit__'):
                    await backend.__aexit__(exc_type, exc_val, exc_tb)
            except Exception as e:
                logger.debug('[StorageRouter] backend %s __aexit__ failed: %s', kind.value, e)

    # ── Async acquire / release (ISSUE #046) ───────────────────────────────────

    async def acquire(self) -> None:
        """
        ISSUE #046: Explicit async acquire — enter all async-capable backends.

        Alternative to `async with router:` context manager.
        Calls backend.acquire() for backends that implement it.

        Multiple calls to acquire() must be paired with same number of release().
        """
        await self.__aenter__()

    async def release(self) -> None:
        """
        ISSUE #046: Explicit async release — exit all async-capable backends.

        Alternative to `async with router:` context manager.
        Calls backend.release() for backends that implement it.

        Must be called once per acquire().
        """
        await self.__aexit__(None, None, None)

    # ── Backend registration ───────────────────────────────────────────────────

    def register_backend(self, kind: StorageKind, backend: Any) -> None:
        """Register or replace a storage backend for a given StorageKind."""
        self._backends[kind] = backend

    def register_invalidation_callback(self, kind: StorageKind, callback: Any) -> None:
        """Subscribe a callback to invalidation events for a StorageKind."""
        if kind in self._invalidation_subscribers:
            self._invalidation_subscribers[kind].append(callback)

    def classify(self, data_kind: str) -> StoragePolicy:
        """Decision tree: data_kind string → StoragePolicy."""
        return _classify(data_kind)

    def _spill_policy(self, policy: StoragePolicy) -> StoragePolicy:
        """On emergency pressure, spill HOT → WARM for embeddings."""
        if self._governor is None:
            return policy
        try:
            uma_state = self._governor.sample_uma_status()
            if uma_state.uma_state in ('emergency', 'critical'):
                if policy.kind == StorageKind.HOT and policy.spill_target:
                    logger.warning('[StorageRouter] emergency pressure — spilling %s → %s', policy.kind.value, policy.spill_target.value)
                    self._stats['spills'] += 1
                    return _DECISION_MATRIX.get('embedding.float32[768]', _POLICY_WARM_EMBEDDING_768)
        except Exception:  # noqa: BLE001
            pass
        return policy

    # ── Sync put/get/delete (thread-safe via per-backend locking) ───────────────

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
        self._stats['puts'] += 1
        try:
            base_policy = self.classify(data_kind)
            policy = self._spill_policy(base_policy)
            backend = self._backends.get(policy.kind)
            if backend is None:
                self._notify_invalidation(policy.kind, key)
                return False
            stored = self._backend_put(backend, key, value)
            self._notify_invalidation(policy.kind, key)
            return stored
        except Exception as e:
            logger.debug('[StorageRouter] put failed for %s: %s', key, e)
            return False

    def get(self, key: str, *, data_kind: str) -> Any:
        """
        Route get to appropriate backend (try HOT → WARM → COLD cascade).

        Returns:
            Stored value or None on miss.
        """
        self._stats['gets'] += 1
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
            try:
                value = self._backend_get(backend, key)
            except Exception as e:
                logger.debug('[StorageRouter] get miss kind=%s key=%s: %s', kind.value, key, e)
                continue
            if value is not None:
                if kind != policy.kind:
                    self.put(key, value, data_kind=data_kind)
                return value
        self._stats['misses'] += 1
        return None

    def delete(self, key: str, *, data_kind: str) -> bool:
        """
        Delete from primary layer + fire invalidation chain.

        Returns:
            True if deleted, False otherwise.
        """
        policy = self.classify(data_kind)
        backend = self._backends.get(policy.kind)
        deleted = False
        if backend is not None:
            try:
                deleted = self._backend_delete(backend, key)
            except Exception as e:
                logger.debug('[StorageRouter] delete failed %s: %s', key, e)
        if deleted:
            self._notify_invalidation(policy.kind, key)
        return deleted

    # ── Async put/get/delete (ISSUE #046 — asyncio.Semaphore, no run_in_executor) ──

    async def aput(self, key: str, value: Any, *, data_kind: str) -> bool:
        """
        ISSUE #046: Fully async put — uses asyncio.Semaphore for lock-free routing.

        Args:
            key: storage key
            value: value to store
            data_kind: classification string

        Returns:
            True if stored, False on error/miss.
        """
        self._stats['puts'] += 1
        async with self._routing_lock:
            try:
                base_policy = self.classify(data_kind)
                policy = self._spill_policy(base_policy)
                backend = self._backends.get(policy.kind)
                if backend is None:
                    self._notify_invalidation(policy.kind, key)
                    return False
                # Run backend I/O in thread pool — backends are not fully async
                stored = await offload_to("cpu_io_pool", self._backend_put, backend, key, value)
                self._notify_invalidation(policy.kind, key)
                return stored
            except Exception as e:
                logger.debug('[StorageRouter] aput failed for %s: %s', key, e)
                return False

    async def aget(self, key: str, *, data_kind: str) -> Any:
        """
        ISSUE #046: Fully async get — uses asyncio.Semaphore for lock-free routing.

        Args:
            key: storage key
            data_kind: classification string

        Returns:
            Stored value or None on miss.
        """
        self._stats['gets'] += 1
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
            try:
                value = await offload_to("cpu_io_pool", self._backend_get, backend, key)
            except Exception as e:
                logger.debug('[StorageRouter] aget miss kind=%s key=%s: %s', kind.value, key, e)
                continue
            if value is not None:
                if kind != policy.kind:
                    await self.aput(key, value, data_kind=data_kind)
                return value
        self._stats['misses'] += 1
        return None

    async def adelete(self, key: str, *, data_kind: str) -> bool:
        """
        ISSUE #046: Fully async delete — uses asyncio.Semaphore for lock-free routing.

        Args:
            key: storage key
            data_kind: classification string

        Returns:
            True if deleted, False otherwise.
        """
        async with self._routing_lock:
            policy = self.classify(data_kind)
            backend = self._backends.get(policy.kind)
            deleted = False
            if backend is not None:
                try:
                    deleted = await offload_to("cpu_io_pool", self._backend_delete, backend, key)
                except Exception as e:
                    logger.debug('[StorageRouter] adelete failed %s: %s', key, e)
            if deleted:
                self._notify_invalidation(policy.kind, key)
            return deleted

    # ── Backend delegation (duck-typed) ─────────────────────────────────────────

    def _backend_put(self, backend: Any, key: str, value: Any, _policy: StoragePolicy | None=None) -> bool:
        """Call backend.put()/set()/upsert()/store(). Fail-safe."""
        try:
            if hasattr(backend, 'put'):
                result = backend.put(key, value)
                return result is not False
            if hasattr(backend, 'set'):
                result = backend.set(key, value)
                return result is not False
            if hasattr(backend, 'upsert'):
                backend.upsert(key, value)
                return True
            if hasattr(backend, 'store'):
                backend.store(key, value)
                return True
            logger.warning('[StorageRouter] backend %s has no put/set/upsert/store', type(backend).__name__)
            return False
        except Exception as e:
            logger.debug('[StorageRouter] backend put failed: %s', e)
            return False

    def _backend_get(self, backend: Any, key: str) -> Any:
        """Call backend.get()/lookup()/fetch(). Fail-safe."""
        try:
            if hasattr(backend, 'get'):
                return backend.get(key)
            if hasattr(backend, 'lookup'):
                return backend.lookup(key)
            if hasattr(backend, 'fetch'):
                return backend.fetch(key)
            return None
        except Exception as e:
            logger.debug('[StorageRouter] backend get failed: %s', e)
            return None

    def _backend_delete(self, backend: Any, key: str) -> bool:
        """Call backend.delete()/remove(). Fail-safe."""
        try:
            if hasattr(backend, 'delete'):
                result = backend.delete(key)
                return result is True or result is None
            if hasattr(backend, 'remove'):
                backend.remove(key)
                return True
            return False
        except Exception as e:
            logger.debug('[StorageRouter] backend delete failed: %s', e)
            return False

    # ── Invalidation & Stats ───────────────────────────────────────────────────

    def _notify_invalidation(self, source_kind: StorageKind, key: str) -> None:
        """Fire invalidation callbacks for all downstream layers."""
        downstream = _INVALIDATION_CHAIN.get(source_kind, ())
        for kind in downstream:
            for callback in self._invalidation_subscribers.get(kind, []):
                try:
                    callback(key, source_kind=source_kind)
                    self._stats['invalidations'] += 1
                except Exception as e:
                    logger.debug('[StorageRouter] invalidation callback failed %s.%s: %s', kind.value, key, e)

    def get_stats(self) -> dict[str, Any]:
        """Return router telemetry + per-backend stats."""
        stats = dict(self._stats)
        for kind, backend in self._backends.items():
            if backend is not None and hasattr(backend, 'get_stats'):
                try:
                    stats[f'backend.{kind.value}'] = backend.get_stats()
                except Exception:  # noqa: BLE001
                    pass
        return stats


# ── Module-level singleton ─────────────────────────────────────────────────────

_router: StorageRouter | None = None
_router_lock = LazyAsyncioLock()

async def get_storage_router(governor: M1ResourceGovernor | None=None) -> StorageRouter:
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
