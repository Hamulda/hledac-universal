"""
[META]-014: Entity Confirmation Service
========================================

Replicates the RouteGraphService.is_known_good() pattern for IOC entities.
An entity becomes "confirmed" after ≥3 distinct source types report it
with MAX(confidence) > 0.7.

This prevents redundant re-fetching of entities already confirmed by
multiple independent sources across sprints, saving fetch budget.

Pattern mirrored from:
- RouteGraphService.is_known_good() — routes require ≥3 observations + >50% success
- RouteEdge.is_known_good property (lines 110-120 in proxy_routes.py)

ARCHITECTURE:
  - DuckDB entity_observations table as primary store
  - Batch query with COUNT(DISTINCT source_type) + MAX(confidence)
  - TTL-bounded LRU cache (256 entries, 5-min TTL)
  - Fail-soft: any error -> not confirmed (never blocks sprint)

WIRE: FetchCoordinator._do_step() -> EntityConfirmationService.is_confirmed_batch()

Feature flag: HLEDAC_ENABLE_ENTITY_CONFIRMATION=1 (default ON)
Opt-out: 0 disables, falls back to "not confirmed"
"""

from __future__ import annotations

import asyncio
import logging
import os
import time as _time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature flag & bounds
# ---------------------------------------------------------------------------
_ENTITY_CONFIRMATION_ENABLED: bool = (
    os.getenv("HLEDAC_ENABLE_ENTITY_CONFIRMATION", "1").lower()
    in ("1", "true", "yes", "on")
)

# Confirmation thresholds — mirrors RouteGraphService pattern
_MIN_DISTINCT_SOURCES: int = 3  # COUNT(DISTINCT source_type) >= 3
_MIN_MAX_CONFIDENCE: float = 0.7  # MAX(confidence) > 0.7

# M1 8GB safety bounds
_CACHE_MAX_ENTRIES: int = 256
_CACHE_TTL_SECONDS: float = 300.0  # 5 minutes
_MAX_BATCH_SIZE: int = 500  # Max entities per batch query


# ---------------------------------------------------------------------------
# Result DTO
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EntityConfirmation:
    """Immutable confirmation result for a single entity."""

    entity_value: str
    entity_type: str
    is_confirmed: bool
    distinct_sources: int
    max_confidence: float
    avg_confidence: float
    observation_count: int
    source_types: tuple[str, ...]
    sprint_ids: tuple[str, ...]
    last_observed_ts: float

    @property
    def confirmation_score(self) -> float:
        """Composite score combining source diversity and confidence.
        
        Returns 0.0-1.0 where higher = more confirmed.
        Used for prioritization when confirmation is borderline.
        """
        source_score = min(self.distinct_sources / 5.0, 1.0)  # 5 sources = max
        conf_score = self.max_confidence
        return (source_score * 0.4 + conf_score * 0.6)

    @property
    def confidence_margin(self) -> float:
        """How far above the threshold the entity is.
        
        Positive = above threshold, Negative = below threshold.
        """
        return self.max_confidence - _MIN_MAX_CONFIDENCE


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

class _ConfirmationCache:
    """TTL-bounded LRU cache for entity confirmation results. M1 8GB: 256 entries max."""

    __slots__ = ("_data", "_max_entries", "_ttl_s")

    def __init__(self, max_entries: int = 256, ttl_s: float = 300.0) -> None:
        self._data: dict[str, tuple[float, EntityConfirmation]] = {}
        self._max_entries = max_entries
        self._ttl_s = ttl_s

    def get(self, key: str) -> EntityConfirmation | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        insert_ts, result = entry
        if _time.monotonic() - insert_ts > self._ttl_s:
            del self._data[key]
            return None
        return result

    def put(self, key: str, result: EntityConfirmation) -> None:
        self._data[key] = (_time.monotonic(), result)
        if len(self._data) > self._max_entries:
            oldest = min(self._data, key=lambda k: self._data[k][0])
            del self._data[oldest]

    def invalidate(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


# ---------------------------------------------------------------------------
# EntityConfirmationService
# ---------------------------------------------------------------------------

class EntityConfirmationService:
    """Async service for IOC entity confirmation checks.

    Mirrors RouteGraphService.is_known_good() pattern:
      - DuckDB entity_observations as primary store
      - Batch queries with aggregation (COUNT DISTINCT + MAX)
      - TTL-bounded in-memory cache for hot-path
      - Fail-soft: any error -> not confirmed

    Confirmation criteria:
      - COUNT(DISTINCT source_type) >= 3
      - MAX(confidence) > 0.7

    Usage:
      service = EntityConfirmationService(duckdb_store=store)
      confirmed, result = await service.is_confirmed("example.com", "domain")
      results = await service.is_confirmed_batch([("example.com", "domain"), ...])
    """

    __slots__ = (
        "_store",
        "_enabled",
        "_cache",
        "_lock",
        "_stats",
    )

    def __init__(
        self,
        store: "DuckDBShadowStore | None" = None,
    ) -> None:
        """Initialize EntityConfirmationService.

        Args:
            store: DuckDBShadowStore instance for persistence.
                   None = cache-only mode (no persistence checks).
        """
        self._store: "DuckDBShadowStore | None" = store
        self._enabled: bool = _ENTITY_CONFIRMATION_ENABLED and store is not None
        self._cache: _ConfirmationCache = _ConfirmationCache(
            max_entries=_CACHE_MAX_ENTRIES,
            ttl_s=_CACHE_TTL_SECONDS,
        )
        self._lock: asyncio.Lock = asyncio.Lock()
        self._stats: dict[str, int] = {
            "queries": 0,
            "entities_checked": 0,
            "confirmed": 0,
            "not_confirmed": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }

    @property
    def enabled(self) -> bool:
        """Whether entity confirmation is enabled."""
        return self._enabled

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def is_confirmed(
        self,
        entity_value: str,
        entity_type: str = "domain",
    ) -> tuple[bool, EntityConfirmation | None]:
        """Check if a single entity is confirmed.

        Args:
            entity_value: The entity value (e.g., "example.com", "1.2.3.4").
            entity_type: The IOC type (e.g., "domain", "ip", "url", "hash").

        Returns:
            (is_confirmed, EntityConfirmation):
              - is_confirmed: True if entity meets confirmation criteria
              - EntityConfirmation: Full confirmation details, or None if error
        """
        results = await self.is_confirmed_batch([(entity_value, entity_type)])
        key = self._make_key(entity_value, entity_type)
        result = results.get(key)
        if result is None:
            return (False, None)
        return (result.is_confirmed, result)

    async def is_confirmed_batch(
        self,
        entity_tuples: list[tuple[str, str]],
    ) -> dict[str, EntityConfirmation]:
        """Batch check entity confirmation status.

        Args:
            entity_tuples: List of (entity_value, entity_type) pairs.

        Returns:
            Dict mapping "entity_type:entity_value" -> EntityConfirmation.
            Uncached or novel entities are queried from DuckDB.
            Errors return empty dict (fail-soft invariant).
        """
        if not entity_tuples:
            return {}

        self._stats["queries"] += 1
        self._stats["entities_checked"] += len(entity_tuples)

        # Check cache first — build uncached list (stats updated after DB query)
        uncached: list[tuple[str, str]] = []
        results: dict[str, EntityConfirmation] = {}

        async with self._lock:
            for ev, et in entity_tuples:
                key = self._make_key(ev, et)
                cached = self._cache.get(key)
                if cached is not None:
                    results[key] = cached
                else:
                    uncached.append((ev, et))

        # Update cache and stats after DB query
        if uncached:
            self._stats["cache_misses"] += len(uncached)

        if not uncached:
            self._stats["cache_hits"] += len(entity_tuples)
            return results

        # Query DuckDB for uncached entities
        if self._enabled and self._store is not None:
            try:
                db_results = await self._query_confirmation_batch(uncached)
                results.update(db_results)

                # Update cache and stats (all under lock to prevent race)
                async with self._lock:
                    for key, confirmation in db_results.items():
                        self._cache.put(key, confirmation)
                        # Track confirmed vs not_confirmed
                        if confirmation.is_confirmed:
                            self._stats["confirmed"] += 1
                        else:
                            self._stats["not_confirmed"] += 1

            except Exception as e:
                logger.debug(
                    "[EntityConfirmation] Batch query failed (fail-soft): %s", e
                )
                # Fail-soft: return cached results only + not-confirmed placeholders
                for ev, et in uncached:
                    key = self._make_key(ev, et)
                    results[key] = EntityConfirmation(
                        entity_value=ev,
                        entity_type=et,
                        is_confirmed=False,
                        distinct_sources=0,
                        max_confidence=0.0,
                        avg_confidence=0.0,
                        observation_count=0,
                        source_types=(),
                        sprint_ids=(),
                        last_observed_ts=0.0,
                    )

        return results

    async def get_confirmation_details(
        self,
        entity_value: str,
        entity_type: str = "domain",
    ) -> EntityConfirmation | None:
        """Get full confirmation details for an entity.

        Unlike is_confirmed(), this always queries DuckDB for fresh data
        (bypasses cache) and returns all aggregation metrics.

        Returns:
            EntityConfirmation with full details, or None if entity not found.
        """
        if not self._enabled or self._store is None:
            return None

        try:
            observations = await self._store.async_get_entity_observations_by_entity(
                entity_value, limit=50
            )
        except Exception:
            return None

        if not observations:
            return None

        return self._aggregate_observations(entity_value, entity_type, observations)

    async def invalidate(self, entity_value: str, entity_type: str = "domain") -> None:
        """Invalidate cached confirmation for an entity.

        Call this after a sprint completes to force re-check on next access.
        """
        key = self._make_key(entity_value, entity_type)
        self._cache.invalidate(key)

    async def invalidate_all(self) -> None:
        """Clear the entire confirmation cache."""
        self._cache.clear()

    def get_stats(self) -> dict[str, int]:
        """Return telemetry counters."""
        return dict(self._stats)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_key(entity_value: str, entity_type: str) -> str:
        """Create cache key from entity value and type."""
        return f"{entity_type}:{entity_value}"

    async def _query_confirmation_batch(
        self,
        entity_tuples: list[tuple[str, str]],
    ) -> dict[str, EntityConfirmation]:
        """Query DuckDB for confirmation metrics on a batch of entities."""
        if not entity_tuples or self._store is None:
            return {}

        results: dict[str, EntityConfirmation] = {}

        # Process in chunks to avoid overwhelming the DB
        for chunk_start in range(0, len(entity_tuples), _MAX_BATCH_SIZE):
            chunk = entity_tuples[chunk_start:chunk_start + _MAX_BATCH_SIZE]
            try:
                chunk_results = await self._query_chunk(chunk)
                results.update(chunk_results)
            except Exception as e:
                logger.debug(
                    "[EntityConfirmation] Chunk query failed: %s", e
                )
                # Continue with next chunk

        return results

    async def _query_chunk(
        self,
        entity_tuples: list[tuple[str, str]],
    ) -> dict[str, EntityConfirmation]:
        """Query a single chunk of entities in parallel using asyncio.gather."""
        if not entity_tuples:
            return {}

        results: dict[str, EntityConfirmation] = {}

        # Process all entities in parallel — each query is independent
        async def _query_single(ev: str, et: str) -> tuple[str, EntityConfirmation]:
            try:
                observations = await self._store.async_get_entity_observations_by_entity(
                    ev, limit=50
                )
                if not observations:
                    return (self._make_key(ev, et), EntityConfirmation(
                        entity_value=ev,
                        entity_type=et,
                        is_confirmed=False,
                        distinct_sources=0,
                        max_confidence=0.0,
                        avg_confidence=0.0,
                        observation_count=0,
                        source_types=(),
                        sprint_ids=(),
                        last_observed_ts=0.0,
                    ))
                return (self._make_key(ev, et), self._aggregate_observations(ev, et, observations))
            except Exception as e:
                logger.debug(
                    "[EntityConfirmation] Single entity query failed for %s: %s", ev, e
                )
                return (self._make_key(ev, et), EntityConfirmation(
                    entity_value=ev,
                    entity_type=et,
                    is_confirmed=False,
                    distinct_sources=0,
                    max_confidence=0.0,
                    avg_confidence=0.0,
                    observation_count=0,
                    source_types=(),
                    sprint_ids=(),
                    last_observed_ts=0.0,
                ))

        # Execute all queries in parallel using asyncio.gather
        gathered = await asyncio.gather(*[_query_single(ev, et) for ev, et in entity_tuples], return_exceptions=True)

        for item in gathered:
            if isinstance(item, Exception):
                logger.debug("[EntityConfirmation] gather item exception: %s", item)
                continue
            key, confirmation = item
            results[key] = confirmation

        return results

    def _aggregate_observations(
        self,
        entity_value: str,
        entity_type: str,
        observations: list[dict[str, Any]],
    ) -> EntityConfirmation:
        """Aggregate observations into EntityConfirmation.

        Applies confirmation criteria:
          - COUNT(DISTINCT source_type) >= 3
          - MAX(confidence) > 0.7
        """
        sources: set[str] = set()
        sprints: set[str] = set()
        confidences: list[float] = []
        max_ts: float = 0.0

        for obs in observations:
            src = obs.get("source_type", "unknown")
            sid = obs.get("sprint_id", "unknown")
            conf = obs.get("confidence", 0.0)
            ts = obs.get("ts", 0.0)

            sources.add(src)
            sprints.add(sid)
            confidences.append(conf)
            if ts > max_ts:
                max_ts = ts

        distinct_sources = len(sources)
        max_confidence = max(confidences) if confidences else 0.0
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        # Apply confirmation criteria (mirrors RouteEdge.is_known_good pattern)
        is_confirmed = (
            distinct_sources >= _MIN_DISTINCT_SOURCES
            and max_confidence > _MIN_MAX_CONFIDENCE
        )

        return EntityConfirmation(
            entity_value=entity_value,
            entity_type=entity_type,
            is_confirmed=is_confirmed,
            distinct_sources=distinct_sources,
            max_confidence=max_confidence,
            avg_confidence=avg_confidence,
            observation_count=len(observations),
            source_types=tuple(sorted(sources)),
            sprint_ids=tuple(sorted(sprints)),
            last_observed_ts=max_ts,
        )


# -- Singleton accessor --------------------------------------------------------

_entity_confirmation_service: EntityConfirmationService | None = None
_service_lock = asyncio.Lock()


async def get_entity_confirmation_service(
    store: "DuckDBShadowStore | None" = None,
) -> EntityConfirmationService:
    """Get the singleton EntityConfirmationService instance."""
    global _entity_confirmation_service
    async with _service_lock:
        if _entity_confirmation_service is None:
            _entity_confirmation_service = EntityConfirmationService(store=store)
        elif store is not None:
            _entity_confirmation_service._store = store
        return _entity_confirmation_service


def get_entity_confirmation_service_sync() -> EntityConfirmationService:
    """Get the singleton EntityConfirmationService instance (sync version).

    Note: For async initialization with store, use get_entity_confirmation_service().
    """
    global _entity_confirmation_service
    if _entity_confirmation_service is None:
        _entity_confirmation_service = EntityConfirmationService(store=None)
    return _entity_confirmation_service
