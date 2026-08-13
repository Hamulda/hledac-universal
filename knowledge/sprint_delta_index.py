# knowledge/sprint_delta_index.py — [META]-002: Cross-Sprint Entity Delta Sync
"""
[META]-002: Cross-Sprint Entity Delta Sync Engine + SprintDeltaIndex Compatibility.





Architecture
-------------
Two-tier design (consistent with cross_sprint_gate.py [META-001]):
  1. DeltaSyncEngine (this module): persistence + in-memory cache
     - sync(): aggregates entity_observations → cross_sprint_entity_index at winddown
     - load_cache(): populates KnownGoodCache at prelude
     - pre_fetch_filter(): checks KnownGoodCache before network fetch
  2. SprintDeltaIndex (this module): DuckDB-backed query interface for cross_sprint_gate.py
     - is_known_good_batch(): batch confirmation check
     - is_known_good(): single entity confirmation check
     - mmap_load_entity(): loads content (DuckDB path — returns None)

Wire:
  FetchCoordinator._do_start()
    → DeltaSyncEngine.configure() + load_cache()   [META-002 KnownGoodCache]
    → CrossSprintGate.inject_delta_index()         [META-001 DuckDB path]

  FetchCoordinator._do_step()
    → CrossSprintGate.should_skip_batch()           [META-001]
    → SprintDeltaIndex.is_known_good_batch()       [META-001 DuckDB fast path]

  WinddownOrchestrator._run_delta_sync()
    → DeltaSyncEngine.sync()                       [META-002 persistent write]

M1 8GB constraints:
  - KnownGoodCache: LRU with maxsize=4096 entries (~200 KB peak)
  - DuckDB write: batch upsert via async executor
  - DuckDB read: single SELECT at prelude (fast)
  - No MLX/GPU at import time (lazy imports throughout)
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import tarfile
import time as _time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_ENABLE_DELTA_INDEX: bool = (
    os.environ.get("HLEDAC_ENABLE_CROSS_SPRINT_GATE", "1").lower()
    in ("1", "true", "yes", "on")
)

# Maximum entries in KnownGoodCache (M1 8GB bounded: ~200 KB peak)
_KNOWN_GOOD_CACHE_MAX_SIZE: int = 4096

# Staleness TTL: entries older than this trigger re-fetch even if cached
_CACHE_ENTRY_TTL_S: float = 90 * 24 * 3600.0

# DuckDB write batch size for entity aggregation
_AGGREGATION_BATCH_SIZE: int = 500


# ── EntityRef (for SprintDeltaIndex compatibility) ───────────────────────────

@dataclass(frozen=True, slots=True)
class EntityRef:
    """Reference to a confirmed entity in SprintDeltaIndex."""
    entity_value: str
    ioc_type: str
    source_count: int = 1
    last_confirmed_ts: float = 0.0
    last_confirmed_sprint: str = ""
    confirmed_sources: list[str] | None = None
    content_hash: str | None = None


# ── KnownGoodCache ───────────────────────────────────────────────────────────

class KnownGoodCache:
    """
    Bounded LRU cache for cross-sprint confirmed entities.

    Key: canonicalized "ioc_type:entity_value" string.
    Value: dict with confirmation metadata.

    M1 8GB: ~200 KB peak for 4096 entries (avg 50 bytes/entry).
    O(1) lookup via OrderedDict move_to_end().
    """

    __slots__ = ("_data", "_hits", "_misses", "_evictions")

    def __init__(self, maxsize: int = _KNOWN_GOOD_CACHE_MAX_SIZE) -> None:
        self._data: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0

    def get(self, key: str) -> dict[str, Any] | None:
        """Return cached entry or None. LRU update + TTL check."""
        entry = self._data.get(key)
        if entry is None:
            self._misses += 1
            return None
        if _time.time() - entry.get("_cached_at_s", 0.0) > _CACHE_ENTRY_TTL_S:
            del self._data[key]
            self._misses += 1
            self._evictions += 1
            return None
        self._data.move_to_end(key)
        self._hits += 1
        return entry

    def set(self, key: str, value: dict[str, Any]) -> None:
        """Store entry. Evicts LRU entry if at capacity."""
        if key in self._data:
            self._data.move_to_end(key)
        elif len(self._data) >= _KNOWN_GOOD_CACHE_MAX_SIZE:
            evicted_key, _ = self._data.popitem(last=False)
            self._evictions += 1
            logger.debug("[KnownGoodCache] Evicted: %s", evicted_key[:64])
        self._data[key] = {**value, "_cached_at_s": _time.time()}

    def bulk_load(self, entries: list[dict[str, Any]]) -> int:
        """Bulk-load entities from cross_sprint_entity_index. Returns count."""
        loaded = 0
        now = _time.time()
        for entry in entries:
            ev = entry.get("entity_value", "")
            ioc_type = entry.get("ioc_type", "")
            if not ev:
                continue
            key = f"{ioc_type}:{ev}"
            if key not in self._data and len(self._data) >= _KNOWN_GOOD_CACHE_MAX_SIZE:
                self._data.popitem(last=False)
                self._evictions += 1
            self._data[key] = {
                "entity_value": ev,
                "ioc_type": ioc_type,
                "confirmation_count": entry.get("confirmation_count", 1),
                "last_confirmed_sprint": entry.get("last_confirmed_sprint", []),
                "first_seen_sprint": entry.get("first_seen_sprint", ""),
                "sha256_content_hash": entry.get("sha256_content_hash"),
                "last_confirmed_ts": entry.get("last_confirmed_ts", now),
                "avg_confidence": entry.get("avg_confidence", 0.0),
                "_cached_at_s": now,
            }
            loaded += 1
        logger.info(
            "[KnownGoodCache] Loaded %d (total=%d, evicted=%d)",
            loaded, len(self._data), self._evictions,
        )
        return loaded

    def clear(self) -> None:
        self._data.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @property
    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "size": len(self._data),
            "maxsize": _KNOWN_GOOD_CACHE_MAX_SIZE,
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "hit_rate_pct": round(hit_rate * 100, 2),
        }


# ── DeltaSyncEngine ──────────────────────────────────────────────────────────

class DeltaSyncEngine:
    """
    [META]-002: Cross-sprint entity delta synchronization engine.

    Responsibilities:
    1. sync(): At sprint WINDDOWN — aggregates entity_observations from the
       current sprint into cross_sprint_entity_index (DuckDB persistent).
    2. load_cache(): At sprint PRELUDE — loads cross_sprint_entity_index
       for prior sprints into KnownGoodCache (in-memory bounded LRU).
    3. pre_fetch_filter(url): Checks KnownGoodCache before network fetch.
       On hit → returns synthetic CanonicalFinding metadata; caller skips fetch.

    Singleton via get_delta_sync_engine().
    """

    __slots__ = (
        "_cache", "_duckdb_store", "_prior_sprint_ids",
        "_sprint_id", "_initialized", "_sync_lock",
        "_stats",
    )

    def __init__(self) -> None:
        self._cache: KnownGoodCache = KnownGoodCache()
        self._duckdb_store: Any = None
        self._prior_sprint_ids: list[str] = []
        self._sprint_id: str = ""
        self._initialized: bool = False
        self._sync_lock: asyncio.Lock = asyncio.Lock()
        self._stats: dict[str, Any] = {
            "sync_calls": 0, "cache_loads": 0,
            "filter_hits": 0, "filter_misses": 0,
            "entities_synced": 0, "entities_loaded": 0,
        }

    # ── Public API ────────────────────────────────────────────────────────

    def configure(self, duckdb_store: Any, sprint_id: str) -> None:
        """Configure engine with DuckDB store and current sprint ID."""
        self._duckdb_store = duckdb_store
        self._sprint_id = sprint_id
        self._initialized = True
        logger.info("[DeltaSyncEngine] Configured for sprint=%s", sprint_id)

    def set_prior_sprint_ids(self, prior_sprint_ids: list[str]) -> None:
        """Set prior sprint IDs to load into KnownGoodCache at prelude."""
        self._prior_sprint_ids = list(prior_sprint_ids)

    async def sync(self) -> dict[str, Any]:
        """
        [META-002] Sprint WINDDOWN — aggregate entity_observations into cross_sprint_entity_index.

        Reads all entity_observations for the current sprint, aggregates by
        (entity_value, ioc_type), computes EWMA confidence, and upserts into
        cross_sprint_entity_index (DuckDB persistent).

        Returns sync statistics.
        """
        if not self._initialized or self._duckdb_store is None:
            return {"synced": 0, "errors": 0, "skipped": True}

        async with self._sync_lock:
            self._stats["sync_calls"] += 1
            store = self._duckdb_store
            sprint_id = self._sprint_id

            logger.info("[DeltaSyncEngine] Starting sync for sprint=%s", sprint_id)

            try:
                # 1. Read entity_observations for this sprint
                observations = await self._get_sprint_observations(store, sprint_id)
                if not observations:
                    logger.info("[DeltaSyncEngine] No observations for sprint=%s", sprint_id)
                    return {"synced": 0, "errors": 0, "observations": 0}

                # 2. Aggregate by (entity_value, ioc_type)
                aggregated = self._aggregate_observations(observations)
                logger.info(
                    "[DeltaSyncEngine] %d obs → %d unique entities",
                    len(observations), len(aggregated),
                )

                # 3. Enrich with content hashes
                enriched = self._enrich_with_hashes(aggregated)

                # 4. Batch upsert into DuckDB
                synced, errors = await self._batch_upsert_entities(store, enriched, sprint_id)
                self._stats["entities_synced"] += synced

                logger.info("[DeltaSyncEngine] Sync done: synced=%d, errors=%d", synced, errors)
                return {"synced": synced, "errors": errors, "observations": len(observations)}

            except Exception as exc:
                logger.error("[DeltaSyncEngine] sync() failed: %s", exc)
                return {"synced": 0, "errors": 1, "error": str(exc)}

    async def load_cache(self) -> int:
        """
        [META-002] Sprint PRELUDE — load KnownGoodCache from cross_sprint_entity_index.

        Returns number of entities loaded.
        """
        if not self._initialized or self._duckdb_store is None:
            return 0

        self._stats["cache_loads"] += 1
        store = self._duckdb_store
        prior = self._prior_sprint_ids

        if not prior:
            logger.info("[DeltaSyncEngine] No prior sprints — cache empty")
            return 0

        logger.info("[DeltaSyncEngine] Loading cache for prior sprints: %s", prior)

        try:
            loop = asyncio.get_running_loop()
            entities = await loop.run_in_executor(
                None, store._sync_get_cross_sprint_entities, prior,
            )
            if not entities:
                return 0
            loaded = self._cache.bulk_load(entities)
            self._stats["entities_loaded"] += loaded
            logger.info("[DeltaSyncEngine] Cache loaded: %d entities", loaded)
            return loaded
        except Exception as exc:
            logger.warning("[DeltaSyncEngine] load_cache() failed: %s", exc)
            return 0

    def pre_fetch_filter(self, url: str) -> dict[str, Any] | None:
        """
        [META-002] Pre-fetch filter — check if URL is in KnownGoodCache.

        On HIT: returns filter result dict. Caller should skip network fetch.
        On MISS: returns None. Caller proceeds with normal fetch.

        Strategy:
          1. Extract domain → check KnownGoodCache for "domain:domain"
          2. Check KnownGoodCache for "url:url"
        """
        if not self._initialized:
            return None

        # Strategy 1: domain-level check
        domain = self._extract_domain(url)
        if domain:
            entry = self._cache.get(f"domain:{domain}")
            if entry is not None:
                self._stats["filter_hits"] += 1
                return self._make_result(entry, url, "domain")

        # Strategy 2: URL-level check
        entry = self._cache.get(f"url:{url}")
        if entry is not None:
            self._stats["filter_hits"] += 1
            return self._make_result(entry, url, "url")

        self._stats["filter_misses"] += 1
        return None

    @property
    def cache(self) -> KnownGoodCache:
        return self._cache

    @property
    def stats(self) -> dict[str, Any]:
        return {**self._stats, "cache_stats": self._cache.stats}

    def clear(self) -> None:
        self._cache.clear()
        self._stats = {k: 0 for k in self._stats}
        self._stats["cache_loads"] = 0
        self._initialized = False
        self._duckdb_store = None
        self._prior_sprint_ids = []

    # ── Internal helpers ──────────────────────────────────────────────────

    async def _get_sprint_observations(self, store: Any, sprint_id: str) -> list[dict[str, Any]]:
        """Read entity_observations for a given sprint using indexed sprint_id lookup."""
        try:
            # [META-002]: Use indexed sprint_id column directly — O(log n) via idx_entity_observations_sprint
            return await store.async_get_entity_observations_by_sprint(
                sprint_id=sprint_id, limit=100_000,
            )
        except Exception:
            return []

    def _aggregate_observations(
        self, observations: list[dict[str, Any]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Aggregate observations by (entity_value, ioc_type)."""
        aggregated: dict[tuple[str, str], dict[str, Any]] = {}
        for obs in observations:
            ev = obs.get("entity_value", "")
            et = obs.get("entity_type", "")
            if not ev or not et:
                continue
            key = (ev, et)
            if key not in aggregated:
                aggregated[key] = {
                    "entity_value": ev, "ioc_type": et,
                    "confidence_sum": 0.0, "confidence_count": 0,
                    "ts_sum": 0.0, "ts_count": 0,
                }
            agg = aggregated[key]
            agg["confidence_sum"] += obs.get("confidence", 0.0)
            agg["confidence_count"] += 1
            agg["ts_sum"] += obs.get("ts", 0.0)
            agg["ts_count"] += 1
        for agg in aggregated.values():
            n = max(agg["confidence_count"], 1)
            agg["avg_confidence"] = agg["confidence_sum"] / n
            agg["avg_ts"] = agg["ts_sum"] / n
        return aggregated

    def _enrich_with_hashes(
        self, aggregated: dict[tuple[str, str], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Compute SHA-256 content hashes for each aggregated entity."""
        enriched: list[dict[str, Any]] = []
        for (ev, ioc_type), agg in aggregated.items():
            content_hash = hashlib.sha256(ev.encode("utf-8")).hexdigest()
            enriched.append({
                "entity_value": ev,
                "ioc_type": ioc_type,
                "avg_confidence": agg["avg_confidence"],
                "last_confirmed_ts": agg["avg_ts"],
                "sha256_content_hash": content_hash,
            })
        return enriched

    async def _batch_upsert_entities(
        self, store: Any, entities: list[dict[str, Any]], sprint_id: str,
    ) -> tuple[int, int]:
        """Batch upsert entities into cross_sprint_entity_index via DuckDB.

        Uses single executor dispatch per batch chunk for efficiency.
        """
        synced = 0
        errors = 0

        def _batch_upsert(chunk: list[dict[str, Any]]) -> tuple[int, int]:
            """Sync batch upsert (runs in thread pool executor)."""
            s, e = 0, 0
            for entity in chunk:
                try:
                    store._sync_upsert_cross_sprint_entity(
                        entity["entity_value"], entity["ioc_type"],
                        sprint_id, entity["last_confirmed_ts"],
                        entity["avg_confidence"], entity["sha256_content_hash"],
                    )
                    s += 1
                except Exception:
                    e += 1
            return s, e

        for chunk_start in range(0, len(entities), _AGGREGATION_BATCH_SIZE):
            chunk = entities[chunk_start:chunk_start + _AGGREGATION_BATCH_SIZE]
            try:
                loop = asyncio.get_running_loop()
                s, e = await loop.run_in_executor(None, _batch_upsert, chunk)
                synced += s
                errors += e
            except Exception:
                errors += len(chunk)
        return synced, errors

    def _extract_domain(self, url: str) -> str | None:
        """Extract domain from URL."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            if parsed.netloc:
                host = parsed.netloc.split(":")[0]
                return host.lower()
        except Exception:  # noqa: BLE001
            pass
        return None

    def _make_result(
        self, entry: dict[str, Any], url: str, match_type: str,
    ) -> dict[str, Any]:
        """Build filter result dict from cache entry."""
        return {
            "source": "cross_sprint_delta",
            "matched_key": f"{entry['ioc_type']}:{entry['entity_value']}",
            "match_type": match_type,
            "url": url,
            "entity_value": entry["entity_value"],
            "ioc_type": entry["ioc_type"],
            "confirmation_count": entry.get("confirmation_count", 1),
            "last_confirmed_sprint": entry.get("last_confirmed_sprint", []),
            "avg_confidence": entry.get("avg_confidence", 0.0),
            "sha256_content_hash": entry.get("sha256_content_hash"),
            "last_confirmed_ts": entry.get("last_confirmed_ts", 0.0),
            "skip_fetch": True,
        }


# ── SprintDeltaIndex (DuckDB-backed, cross_sprint_gate.py compatible) ────────

class SprintDeltaIndex:
    """
    DuckDB-backed SprintDeltaIndex compatible with cross_sprint_gate.py.

    Provides is_known_good_batch() for the pre-fetch gate.
    DuckDB cross_sprint_entity_index is persistent and survives sprint teardown.
    """

    __slots__ = ("_duckdb_store", "_enabled", "_lock")

    def __init__(self, duckdb_store: Any | None = None) -> None:
        self._duckdb_store: Any = duckdb_store
        self._enabled: bool = _ENABLE_DELTA_INDEX
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def is_known_good_batch(
        self,
        entity_tuples: list[tuple[str, str]],
        current_sprint_id: str = "current",
    ) -> dict[str, tuple[bool, EntityRef | None]]:
        """Batch check if entities are confirmed by prior sprints."""
        if not self._enabled or not entity_tuples:
            return {}

        async with self._lock:
            return await self._do_batch_check(entity_tuples)

    async def _do_batch_check(
        self, entity_tuples: list[tuple[str, str]],
    ) -> dict[str, tuple[bool, EntityRef | None]]:
        """DuckDB batch lookup via cross_sprint_entity_index."""
        results: dict[str, tuple[bool, EntityRef | None]] = {}
        if not entity_tuples or self._duckdb_store is None:
            return results

        try:
            entity_values = [t[0] for t in entity_tuples]
            loop = asyncio.get_running_loop()
            rows = await loop.run_in_executor(
                None,
                self._duckdb_store._sync_check_cross_sprint_batch,
                entity_values,
            )

            found: set[str] = set()
            for row in rows:
                ev, ioc_type, conf_count, last_ts, sprint, content_hash = row
                key = f"{ioc_type}:{ev}"
                results[key] = (True, EntityRef(
                    entity_value=ev, ioc_type=ioc_type,
                    source_count=conf_count, last_confirmed_ts=last_ts,
                    last_confirmed_sprint=sprint, content_hash=content_hash,
                ))
                found.add(ev)

            for ev, ioc_type in entity_tuples:
                key = f"{ioc_type}:{ev}"
                if key not in results:
                    results[key] = (False, None)

        except Exception as e:
            logger.debug("[SprintDeltaIndex] Batch check failed: %s", e)
            for ev, ioc_type in entity_tuples:
                results[f"{ioc_type}:{ev}"] = (False, None)

        return results

    async def is_known_good(
        self, entity_value: str, ioc_type: str = "domain",
        current_sprint_id: str = "current",
    ) -> tuple[bool, EntityRef | None]:
        """Single-entity check."""
        results = await self.is_known_good_batch(
            [(entity_value, ioc_type)], current_sprint_id,
        )
        return results.get(f"{ioc_type}:{entity_value}", (False, None))

    async def mmap_load_entity(self, ref: EntityRef) -> bytes | None:
        """
        Load cached entity content.
        For DuckDB path: content is in canonical_findings.payload_text.
        Returns None (caller should query canonical_findings separately).
        """
        return None


# ── MmapDeltaIndex (NEXTGEN-04: Zero-Latency Bundle Delta Index) ──────────────

class MmapDeltaIndex:
    """
    [NEXTGEN-04]: Memory-mapped delta index for zero-latency sprint caching.
    
    Loads entity_index from .hledac-sprint bundles and provides O(1) lookups
    without DuckDB I/O. Enables skipping redundant fetches for entities
    confirmed within the last 24 hours (configurable TTL).
    
    Key benefits:
    - Zero network I/O for freshness checks (data from bundles in memory/disk cache)
    - O(1) lookup via dict hash table
    - Zero-copy read via get_delta_patch() returning (bundle_path, offset, length)
    - M1 8GB optimized: in-memory index ~2MB per 100K entities
    
    Usage:
        index = MmapDeltaIndex()
        index.register_bundle(bundle_path, sprint_id)
        
        if index.is_fresh(entity_value, ioc_type, max_age_hours=24):
            patch = index.get_delta_patch(entity_value, ioc_type)
            # Apply patch to IOCGraph.buffer_ioc()
    
    Integration points:
    - FetchCoordinator._apply_gate_filters() pre-fetch gate
    - IOCGraph.buffer_ioc() for delta patch application
    """

    __slots__ = (
        "_index",           # dict[str, dict]: idx_key → entity data
        "_bundle_map",      # dict[str, Path]: sprint_id → bundle_path
        "_mmap_cache",      # dict[Path, bytes]: decompressed bundle → entity_index bytes
        "_enabled",         # bool: feature flag
        "_max_age_hours",   # float: staleness TTL
        "_stats",           # dict: telemetry counters
        "_lock",            # asyncio.Lock: thread safety
    )

    def __init__(
        self,
        max_age_hours: float = 24.0,
        enabled: bool = True,
    ) -> None:
        """
        Initialize MmapDeltaIndex.
        
        Args:
            max_age_hours: Staleness threshold (default: 24 hours)
            enabled: Feature flag (default: True)
        """
        self._index: dict[str, dict[str, Any]] = {}
        self._bundle_map: dict[str, Path] = {}
        self._mmap_cache: dict[str, bytes] = {}  # Key: bundle_path str
        self._enabled: bool = enabled and _ENABLE_DELTA_INDEX
        self._max_age_hours: float = max_age_hours
        self._stats: dict[str, int] = {
            "bundles_registered": 0,
            "entities_loaded": 0,
            "fresh_checks": 0,
            "fresh_hits": 0,
            "delta_patches": 0,
        }
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        """Check if MmapDeltaIndex is enabled."""
        return self._enabled

    @property
    def stats(self) -> dict[str, Any]:
        """Return telemetry counters."""
        return {**self._stats}

    def register_bundle(
        self,
        bundle_path: Path,
        sprint_id: str,
    ) -> int:
        """
        Register a bundle and load its entity_index into memory.
        
        [NEXTGEN-04]: Uses _mmap_cache to avoid re-reading/decompressing
        previously loaded bundles. Cache key is bundle_path string.
        
        Args:
            bundle_path: Path to .hledac-sprint bundle
            sprint_id: Sprint identifier
            
        Returns:
            Number of entities loaded from bundle
        """
        if not self._enabled or not bundle_path.exists():
            return 0

        # [OPTIMIZATION]: Check if already registered
        if sprint_id in self._bundle_map:
            logger.debug("[MmapDeltaIndex] Bundle %s already registered, skipping", sprint_id)
            return 0

        try:
            # [OPTIMIZATION]: Use cached bundle bytes if available
            bundle_key = str(bundle_path)
            if bundle_key in self._mmap_cache:
                index_bytes = self._mmap_cache[bundle_key]
            else:
                # Load bundle and extract entity_index
                bundle_bytes = bundle_path.read_bytes()
                index_bytes = self._extract_entity_index_from_bundle(bundle_bytes, bundle_path)
                
                if index_bytes is None:
                    return 0
                
                # [OPTIMIZATION]: Cache decompressed index bytes for reuse
                self._mmap_cache[bundle_key] = index_bytes

            # Decompress zstd if needed
            try:
                import compression.zstd
                index_text = compression.zstd.decompress(index_bytes).decode("utf-8")
            except Exception:
                index_text = index_bytes.decode("utf-8")

            # Parse JSON
            import orjson
            entity_data: dict[str, dict[str, Any]] = orjson.loads(index_text)

            # Index entities with sprint_id context
            loaded = 0
            for idx_key, entry in entity_data.items():
                self._index[idx_key] = {
                    **entry,
                    "_sprint_id": sprint_id,
                    "_bundle_path": str(bundle_path),
                    "_loaded_at": _time.time(),
                }
                loaded += 1

            self._bundle_map[sprint_id] = bundle_path
            self._stats["bundles_registered"] += 1
            self._stats["entities_loaded"] += loaded

            logger.info(
                "[MmapDeltaIndex] Registered bundle %s: %d entities (total: %d)",
                sprint_id,
                loaded,
                len(self._index),
            )
            return loaded

        except Exception as e:
            logger.debug("[MmapDeltaIndex] Bundle registration failed: %s", e)
            return 0

    def _extract_entity_index_from_bundle(
        self,
        bundle_bytes: bytes,
        bundle_path: Path,
    ) -> bytes | None:
        """
        Extract entity_index.json.zst from bundle tar archive.
        
        [NEXTGEN-04]: This enables loading the delta index directly from
        the compressed bundle without full extraction.
        """
        try:
            # Decompress bundle (zstd or raw tar)
            try:
                import compression.zstd
                tar_bytes = compression.zstd.decompress(bundle_bytes)
            except Exception:
                tar_bytes = bundle_bytes

            # Open tar and extract entity_index.json.zst
            tar_buffer = io.BytesIO(tar_bytes)
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                member = tar.getmember("entity_index.json.zst")
                if member:
                    f = tar.extractfile(member)
                    if f is not None:
                        return f.read()

            # Try without .zst extension (backward compatibility)
            tar_buffer.seek(0)
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                member = tar.getmember("entity_index.json")
                if member:
                    f = tar.extractfile(member)
                    if f is not None:
                        return f.read()

        except Exception as e:
            logger.debug("[MmapDeltaIndex] entity_index extraction failed: %s", e)

        return None

    def _make_key(self, entity_value: str, ioc_type: str = "domain") -> str:
        """Create canonical index key."""
        return f"{ioc_type}:{entity_value}"

    def is_fresh(
        self,
        entity_value: str,
        ioc_type: str = "domain",
        max_age_hours: float | None = None,
    ) -> bool:
        """
        O(1) check if entity is fresh (confirmed within TTL).
        
        Args:
            entity_value: IOC value to check
            ioc_type: IOC type (default: domain)
            max_age_hours: Override default TTL (default: 24 hours)
            
        Returns:
            True if entity was confirmed within TTL, False otherwise
        """
        if not self._enabled:
            return False

        self._stats["fresh_checks"] += 1
        idx_key = self._make_key(entity_value, ioc_type)
        
        entry = self._index.get(idx_key)
        if entry is None:
            return False

        # Check staleness
        max_age = max_age_hours if max_age_hours is not None else self._max_age_hours
        age_seconds = _time.time() - entry.get("last_confirmed_ts", 0.0)
        
        if age_seconds > max_age * 3600.0:
            return False

        self._stats["fresh_hits"] += 1
        return True

    def is_fresh_batch(
        self,
        entities: list[tuple[str, str]],
        max_age_hours: float | None = None,
    ) -> dict[str, bool]:
        """
        Batch O(1) freshness check for multiple entities.
        
        Args:
            entities: List of (entity_value, ioc_type) tuples
            max_age_hours: Override default TTL
            
        Returns:
            Dict mapping idx_key → is_fresh bool
        """
        results: dict[str, bool] = {}
        max_age = max_age_hours if max_age_hours is not None else self._max_age_hours
        cutoff = _time.time() - max_age * 3600.0
        
        for ev, ioc_type in entities:
            idx_key = self._make_key(ev, ioc_type)
            entry = self._index.get(idx_key)
            
            if entry is not None:
                last_ts = entry.get("last_confirmed_ts", 0.0)
                results[idx_key] = last_ts > cutoff
            else:
                results[idx_key] = False

        return results

    def get_delta_patch(
        self,
        entity_value: str,
        ioc_type: str = "domain",
    ) -> dict[str, Any] | None:
        """
        Get delta patch for zero-copy entity application.
        
        Returns metadata for applying the entity to IOCGraph without
        re-fetching from network. The patch contains:
        - bundle_path: Source bundle for reference
        - mmap_offset: Byte offset in evidence file (if available)
        - mmap_length: Byte length for mmap read
        - last_confirmed_ts: Original confirmation timestamp
        - confidence: Entity confidence
        - sources: List of confirming sources
        
        Args:
            entity_value: IOC value
            ioc_type: IOC type
            
        Returns:
            Delta patch dict or None if entity not in index
        """
        if not self._enabled:
            return None

        self._stats["delta_patches"] += 1
        idx_key = self._make_key(entity_value, ioc_type)
        entry = self._index.get(idx_key)
        
        if entry is None:
            return None

        return {
            "entity_value": entry.get("entity_value", entity_value),
            "ioc_type": entry.get("ioc_type", ioc_type),
            "bundle_path": entry.get("_bundle_path", ""),
            "mmap_offset": entry.get("mmap_offset", 0),
            "mmap_length": entry.get("mmap_length", 0),
            "last_confirmed_ts": entry.get("last_confirmed_ts", 0.0),
            "first_seen_ts": entry.get("first_seen_ts", 0.0),
            "confidence": entry.get("confidence_sum", 0.0) / max(entry.get("source_count", 1), 1),
            "source_count": entry.get("source_count", 0),
            "sources": entry.get("sources", []),
            "sprint_id": entry.get("_sprint_id", ""),
            "sha256": entry.get("sha256", ""),
            "skip_fetch": True,
            "skip_reason": "delta_confirmed",
        }

    def get_delta_patches_batch(
        self,
        entities: list[tuple[str, str]],
    ) -> dict[str, dict[str, Any] | None]:
        """
        Batch get delta patches for multiple entities.
        
        Args:
            entities: List of (entity_value, ioc_type) tuples
            
        Returns:
            Dict mapping idx_key → delta patch or None
        """
        return {
            self._make_key(ev, ioc_type): self.get_delta_patch(ev, ioc_type)
            for ev, ioc_type in entities
        }

    async def apply_delta_patch_to_graph(
        self,
        patch: dict[str, Any],
        graph: Any,  # IOCGraph or compatible
    ) -> bool:
        """
        Apply delta patch to IOCGraph.buffer_ioc().
        
        [NEXTGEN-04]: Enables applying cached entity data directly
        without network fetch. The observed_at timestamp preserves
        the original confirmation time.
        
        Args:
            patch: Delta patch from get_delta_patch()
            graph: IOCGraph instance with buffer_ioc method
            
        Returns:
            True if patch applied successfully
        """
        if not self._enabled or patch is None:
            return False

        try:
            # Extract patch data
            ioc_type = patch.get("ioc_type", "domain")
            entity_value = patch.get("entity_value", "")
            observed_at = patch.get("last_confirmed_ts", None)
            confidence = patch.get("confidence", 0.5)

            if not entity_value:
                return False

            # Apply to graph buffer
            await graph.buffer_ioc(
                ioc_type=ioc_type,
                value=entity_value,
                confidence=confidence,
                observed_at=observed_at,
            )
            return True

        except Exception as e:
            logger.debug("[MmapDeltaIndex] Patch application failed: %s", e)
            return False

    def get_sprint_ids(self) -> list[str]:
        """Return list of registered sprint IDs."""
        return list(self._bundle_map.keys())

    def get_bundle_path(self, sprint_id: str) -> Path | None:
        """Get bundle path for a sprint."""
        return self._bundle_map.get(sprint_id)

    def clear(self) -> None:
        """Clear all loaded index data."""
        self._index.clear()
        self._bundle_map.clear()
        self._mmap_cache.clear()
        self._stats = {k: 0 for k in self._stats}

    def memory_usage(self) -> dict[str, Any]:
        """Estimate memory usage for M1 8GB monitoring."""
        entry_count = len(self._index)
        est_bytes = entry_count * 200  # ~200 bytes per entry average
        return {
            "entities": entry_count,
            "estimated_bytes": est_bytes,
            "estimated_mb": round(est_bytes / (1024 * 1024), 2),
            "bundles": len(self._bundle_map),
        }


# ── Singletons ───────────────────────────────────────────────────────────────

_delta_engine: DeltaSyncEngine | None = None
_sprint_delta_index: SprintDeltaIndex | None = None
_index_lock = asyncio.Lock()


def get_delta_sync_engine() -> DeltaSyncEngine:
    """Get the singleton DeltaSyncEngine instance."""
    global _delta_engine
    if _delta_engine is None:
        _delta_engine = DeltaSyncEngine()
    return _delta_engine


async def get_sprint_delta_index(duckdb_store: Any | None = None) -> SprintDeltaIndex:
    """Get the singleton SprintDeltaIndex instance (async-compatible)."""
    global _sprint_delta_index
    async with _index_lock:
        if _sprint_delta_index is None:
            _sprint_delta_index = SprintDeltaIndex(duckdb_store=duckdb_store)
        elif duckdb_store is not None:
            _sprint_delta_index._duckdb_store = duckdb_store
        return _sprint_delta_index


def get_sprint_delta_index_sync() -> SprintDeltaIndex:
    """Get the singleton SprintDeltaIndex instance (sync version)."""
    global _sprint_delta_index
    if _sprint_delta_index is None:
        _sprint_delta_index = SprintDeltaIndex(duckdb_store=None)
    return _sprint_delta_index


# ── MmapDeltaIndex Singleton ─────────────────────────────────────────────────

_mmap_delta_index: MmapDeltaIndex | None = None


def get_mmap_delta_index() -> MmapDeltaIndex:
    """
    Get the singleton MmapDeltaIndex instance.
    
    [NEXTGEN-04]: Provides zero-latency delta index for sprint bundles.
    Load bundles via register_bundle() after initialization.
    
    Returns:
        MmapDeltaIndex singleton
    """
    global _mmap_delta_index
    if _mmap_delta_index is None:
        _mmap_delta_index = MmapDeltaIndex()
    return _mmap_delta_index


def reset_mmap_delta_index() -> None:
    """Reset MmapDeltaIndex singleton (for testing or memory reclaim)."""
    global _mmap_delta_index
    if _mmap_delta_index is not None:
        _mmap_delta_index.clear()
    _mmap_delta_index = None


__all__ = [
    "EntityRef",
    "KnownGoodCache",
    "DeltaSyncEngine",
    "SprintDeltaIndex",
    "MmapDeltaIndex",
    "get_delta_sync_engine",
    "get_sprint_delta_index",
    "get_sprint_delta_index_sync",
    "get_mmap_delta_index",
    "reset_mmap_delta_index",
]
