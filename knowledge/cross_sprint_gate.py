"""
META-001: Cross-Sprint Pre-Fetch Gate
=====================================




Queries entity_observations DuckDB table AND SprintDeltaIndex to gate
URL frontier selection BEFORE fetching. Prevents redundant fetches for entities
already confirmed by multiple independent sources across sprints.

[DETA]-001 EXTENSION: SprintDeltaIndex provides O(1) DuckDB lookups for
cross-sprint entity confirmation.

ROLE: Pre-fetch decision engine — injects cross-sprint entity knowledge
into FetchCoordinator's URL frontier selection so that:
  - "known-good" domains (>=2 distinct sources, >=1 sprint) are skipped
  - "novel" domains (never seen) get priority boost
  - "contradicted" entities (different sources disagree) trigger re-fetch

ARCHITECTURE:
  - SprintDeltaIndex: Fast O(1) DuckDB lookup (primary path)
  - DuckDB entity_observations: Deep query (fallback path)
  - Single async query per entity batch (not N queries for N URLs)
  - Bounded: MAX_GATE_ENTITIES=500, MAX_OBSERVATIONS_PER_ENTITY=50
  - Fail-soft: any error -> "allow all" (never blocks sprint execution)
  - M1 8GB safe: bounded queries, TTL cache ~1000 entries

WIRE: FetchCoordinator._do_step() -> CrossSprintGate.should_skip_batch()
  -> SprintDeltaIndex.is_known_good_batch() (DuckDB fast path)
  -> DuckDB async_get_entity_observations_by_entity() (deep path)

Feature flag: HLEDAC_ENABLE_CROSS_SPRINT_GATE=1 (default ON)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time as _time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from _core import aclose

logger = logging.getLogger(__name__)

# -- Bounds (M1 8GB safe) -----------------------------------------------------
MAX_GATE_ENTITIES: int = 500
MAX_OBSERVATIONS_PER_ENTITY: int = 50
MIN_SOURCES_FOR_CONFIRMED: int = 2
MIN_SPRINTS_FOR_CONFIRMED: int = 1
MAX_CONFIDENCE_FOR_SKIP: float = 0.75
CONTRADICTION_CONFIDENCE_DELTA: float = 0.4  # >=40% confidence gap = contradiction
STALE_DAYS: int = 30  # Entities not seen in 30 days are "stale"

# [DETA]-001: SprintDeltaIndex integration
_MIN_SOURCES_FOR_DELTA_INDEX: int = 2  # Use DuckDB fast path if >=2 sources

# Env gate
_ENABLE_CROSS_SPRINT_GATE: bool = (
    os.environ.get("HLEDAC_ENABLE_CROSS_SPRINT_GATE", "1").lower()
    in ("1", "true", "yes", "on")
)


@dataclass
class EntityFreshness:
    """Freshness assessment for a single entity."""
    entity_value: str
    entity_type: str = ""
    freshness: str = "novel"  # novel | seen | confirmed | stale
    distinct_sources: int = 0
    distinct_sprints: int = 0
    avg_confidence: float = 0.0
    last_seen_ts: float = 0.0
    last_confirmed_ts: float = 0.0  # [DETA]-001: from SprintDeltaIndex
    observations_count: int = 0
    source_types: list[str] = field(default_factory=list)
    sprint_ids: list[str] = field(default_factory=list)
    should_skip: bool = False
    skip_reason: str = ""


@dataclass
class ContradictionSignal:
    """Contradiction detected between sources for the same entity."""
    entity_value: str
    entity_type: str
    severity: float = 0.0  # 0.0-1.0
    conflicting_sources: list[str] = field(default_factory=list)
    confidence_gap: float = 0.0
    description: str = ""


class CrossSprintGate:
    """Pre-fetch gate using cross-sprint entity_observations + SprintDeltaIndex.

    [DETA]-001: Two-tier lookup:
      1. SprintDeltaIndex DuckDB (fast path): O(1) lookups
      2. DuckDB entity_observations (deep path): Full historical query

    [NEXTGEN-04]: Three-tier with MmapDeltaIndex for zero-latency bundle lookups.

    Thread safety: skip_cache access under asyncio.Lock.
    Fail-soft: any DuckDB error -> "allow all" (returns empty skip set).
    """

    __slots__ = (
        "_duckdb_store",
        "_delta_index",
        "_mmap_delta_index",  # [NEXTGEN-04]: MmapDeltaIndex for zero-latency
        "_enabled",
        "_lock",
        "_skip_cache",
        "_skip_cache_ttl",
        "_stats",
    )

    def __init__(self, duckdb_store: Any | None = None) -> None:
        self._duckdb_store: Any = duckdb_store
        self._delta_index: Any = None  # [DETA]-001: SprintDeltaIndex reference
        self._mmap_delta_index: Any = None  # [NEXTGEN-04]: MmapDeltaIndex reference
        self._enabled: bool = _ENABLE_CROSS_SPRINT_GATE
        self._lock: asyncio.Lock = asyncio.Lock()
        self._skip_cache: dict[str, tuple[bool, float]] = {}  # entity_value -> (should_skip, ts)
        self._skip_cache_ttl: float = 300.0  # 5 minutes
        self._stats: dict[str, int] = {
            "queries": 0,
            "entities_checked": 0,
            "skipped": 0,
            "allowed": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "delta_index_skips": 0,  # [DETA]-001: DuckDB fast path skips
            "mmap_delta_skips": 0,  # [NEXTGEN-04]: MmapDeltaIndex skips
        }

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def inject_duckdb_store(self, store: Any) -> None:
        """Inject DuckDBShadowStore reference (called after initialization)."""
        self._duckdb_store = store

    async def inject_delta_index(self) -> None:
        """[DETA]-001: Inject SprintDeltaIndex singleton (async initialization)."""
        if self._delta_index is None:
            try:
                from hledac.universal.knowledge.sprint_delta_index import (
                    get_sprint_delta_index,
                )
                self._delta_index = await get_sprint_delta_index(
                    duckdb_store=self._duckdb_store,
                )
            except Exception as e:
                logger.debug("[CrossSprintGate] SprintDeltaIndex init failed: %s", e)
                self._delta_index = None

    def inject_mmap_delta_index(self) -> None:
        """[NEXTGEN-04]: Inject MmapDeltaIndex singleton for zero-latency bundle lookups."""
        if self._mmap_delta_index is None:
            try:
                from hledac.universal.knowledge.sprint_delta_index import (
                    get_mmap_delta_index,
                )
                self._mmap_delta_index = get_mmap_delta_index()
            except Exception as e:
                logger.debug("[CrossSprintGate] MmapDeltaIndex init failed: %s", e)
                self._mmap_delta_index = None

    async def should_skip_batch(
        self,
        entities: list[dict[str, str]],
    ) -> tuple[set[str], list[EntityFreshness]]:
        """Check a batch of entities for skip eligibility.

        [NEXTGEN-04] Four-tier lookup (true zero-latency path):
          1. MmapDeltaIndex.is_fresh_batch() — bundle-based, O(1), zero network I/O
          2. In-memory skip cache — previously evaluated entities
          3. SprintDeltaIndex.is_known_good_batch() — DuckDB-based, O(1) via index
          4. DuckDB async_get_entity_observations_by_entity() — deep query, full history

        Args:
            entities: List of dicts with 'entity_value' (required) and
                      optional 'entity_type' (defaults to 'domain').

        Returns:
            (skip_set, freshness_list):
              - skip_set: entity_values that should be skipped (already confirmed)
              - freshness_list: full freshness assessment for each entity
        """
        if not self._enabled or not entities:
            return (set(), [])

        self._stats["queries"] += 1
        self._stats["entities_checked"] += len(entities)

        now = _time.time()
        all_freshness: list[EntityFreshness] = []
        skip_set: set[str] = set()
        
        # [NEXTGEN-04] TIER 1: MmapDeltaIndex zero-latency bundle check
        mmap_skip_set, mmap_fresh_results, uncached_for_tier2 = await self._query_mmap_delta_index(entities)
        skip_set.update(mmap_skip_set)
        
        # Build freshness entries for tier 1 skipped entities
        for ev, ioc_type, entry in mmap_fresh_results:
            freshness = EntityFreshness(
                entity_value=ev,
                entity_type=ioc_type,
                freshness="confirmed",
                distinct_sources=entry.get("source_count", 1),
                distinct_sprints=1,
                avg_confidence=0.75,
                last_confirmed_ts=entry.get("last_confirmed_ts", 0.0),
                last_seen_ts=entry.get("first_seen_ts", 0.0),
                observations_count=entry.get("source_count", 1),
                source_types=entry.get("sources", []),
                sprint_ids=[entry.get("_sprint_id", "")],
                should_skip=True,
                skip_reason="delta_bundle_confirmed",
            )
            all_freshness.append(freshness)
        
        # TIER 2: In-memory skip cache for previously evaluated entities
        cache_skip, uncached, _ = await self._check_cache(uncached_for_tier2)
        skip_set.update(cache_skip)
        
        if not uncached:
            await self._evict_stale_cache(now)
            return (skip_set, all_freshness)
        
        # TIER 3: SprintDeltaIndex DuckDB fast path
        delta_index_results = await self._query_delta_index(uncached)
        
        # TIER 4: DuckDB deep query (full historical)
        freshness_map = await self._query_duckdb(uncached)
        
        # Evaluate remaining entities and build freshness list
        tier2_4_freshness = await self._evaluate_entities(
            skip_set, uncached, freshness_map, delta_index_results, now,
        )
        all_freshness.extend(tier2_4_freshness)
        
        await self._evict_stale_cache(now)

        return (skip_set, all_freshness)
    
    async def _query_mmap_delta_index(
        self,
        entities: list[dict[str, str]],
    ) -> tuple[set[str], list[tuple[str, str, dict[str, Any]]], list[dict[str, str]]]:
        """
        [NEXTGEN-04] TIER 1: MmapDeltaIndex zero-latency bundle lookup.
        
        Checks if entities are fresh based on bundle-registered entity data.
        Returns (skip_set, fresh_entries, uncached_entities) for downstream tiers.
        
        Zero-latency because:
        - Data is pre-loaded from bundles at sprint start
        - Uses dict hash table O(1) lookup
        - No network I/O (bundles are local or in disk cache)
        
        Args:
            entities: List of entity dicts
            
        Returns:
            - skip_set: entity_values confirmed by bundle index
            - fresh_entries: list of (ev, ioc_type, index_entry) for confirmed entities
            - uncached: entities not found in bundle index
        """
        if self._mmap_delta_index is None or not self._mmap_delta_index.enabled:
            return (set(), [], entities)
        
        skip_set: set[str] = set()
        fresh_entries: list[tuple[str, str, dict[str, Any]]] = []
        uncached: list[dict[str, str]] = []
        
        try:
            # Build entity tuples for batch lookup
            entity_tuples: list[tuple[str, str]] = [
                (ent["entity_value"], ent.get("entity_type", "domain"))
                for ent in entities
            ]
            
            # Batch O(1) freshness check via MmapDeltaIndex
            fresh_results = self._mmap_delta_index.is_fresh_batch(entity_tuples)
            
            # Separate fresh (skip) from non-fresh (continue to tier 2)
            for ent in entities:
                ev = ent["entity_value"]
                ioc_type = ent.get("entity_type", "domain")
                idx_key = f"{ioc_type}:{ev}"
                
                if fresh_results.get(idx_key, False):
                    skip_set.add(ev)
                    # Get the full index entry for freshness metadata
                    full_entry = self._mmap_delta_index.get_entry(ev, ioc_type) or {}
                    fresh_entries.append((ev, ioc_type, full_entry))
                    self._stats["mmap_delta_skips"] += 1
                else:
                    uncached.append(ent)
            
            if skip_set:
                logger.debug(
                    "[CrossSprintGate] MmapDeltaIndex tier 1: %d/%d entities skipped (fresh)",
                    len(skip_set), len(entities),
                )
            
        except Exception as e:
            logger.debug("[CrossSprintGate] MmapDeltaIndex tier 1 failed: %s", e)
            # Fail-soft: continue to tier 2
            uncached = entities
        
        return (skip_set, fresh_entries, uncached)

    async def _check_cache(self, entities: list[dict[str, str]]) -> tuple[set[str], list[dict[str, str]], float]:
        """Check cache for entities, return (skip_set, uncached, now)."""
        skip_set: set[str] = set()
        uncached: list[dict[str, str]] = []
        now = _time.time()

        async with self._lock:
            for ent in entities:
                ev = ent["entity_value"]
                if ev in self._skip_cache:
                    cached_skip, cached_ts = self._skip_cache[ev]
                    if now - cached_ts < self._skip_cache_ttl:
                        self._stats["cache_hits"] += 1
                        if cached_skip:
                            skip_set.add(ev)
                        continue
                self._stats["cache_misses"] += 1
                uncached.append(ent)
        return skip_set, uncached, now

    async def _query_delta_index(self, uncached: list[dict[str, str]]) -> dict[str, tuple[bool, Any]]:
        """Query DeltaIndex fast path for entities."""
        if self._delta_index is None or not self._delta_index.enabled:
            return {}
        try:
            entity_tuples = [
                (ent["entity_value"], ent.get("entity_type", "domain"))
                for ent in uncached
            ]
            return await self._delta_index.is_known_good_batch(entity_tuples, current_sprint_id="current")
        except Exception as e:
            logger.debug("[CrossSprintGate] DeltaIndex batch lookup failed: %s", e)
            return {}

    async def _query_duckdb(self, uncached: list[dict[str, str]]) -> dict[str, EntityFreshness]:
        """Query DuckDB for entity freshness."""
        try:
            return await self._query_entity_batch(uncached)
        except Exception as e:
            logger.debug("[CrossSprintGate] Batch query failed (fail-soft -> allow all): %s", e)
            return {}

    async def _evaluate_entities(
        self,
        skip_set: set[str],
        uncached: list[dict[str, str]],
        freshness_map: dict[str, EntityFreshness],
        delta_index_results: dict[str, tuple[bool, Any]],
        now: float,
    ) -> list[EntityFreshness]:
        """Evaluate entities and build freshness list."""
        all_freshness: list[EntityFreshness] = []
        for ent in uncached:
            ev = ent["entity_value"]
            ioc_type = ent.get("entity_type", "domain")
            delta_key = f"{ioc_type}:{ev}"

            freshness = freshness_map.get(ev) or EntityFreshness(entity_value=ev, entity_type=ioc_type)
            self._boost_with_delta_index(freshness, delta_key, delta_index_results)
            self._decide_skip(skip_set, ev, freshness)
            async with self._lock:
                self._skip_cache[ev] = (freshness.should_skip, now)
            all_freshness.append(freshness)
        return all_freshness

    def _boost_with_delta_index(self, freshness: EntityFreshness, delta_key: str, delta_results: dict) -> None:
        """Apply DeltaIndex boost to freshness if available."""
        if delta_key not in delta_results:
            return
        is_good, ref = delta_results[delta_key]
        if not is_good or ref is None:
            return

        freshness.freshness = "confirmed"
        freshness.distinct_sources = max(freshness.distinct_sources, getattr(ref, "source_count", 1))
        freshness.avg_confidence = max(freshness.avg_confidence, 0.75)
        freshness.last_confirmed_ts = getattr(ref, "last_confirmed_ts", 0.0)
        last_sprint = getattr(ref, "last_confirmed_sprint", "")
        if last_sprint and last_sprint not in freshness.sprint_ids:
            freshness.sprint_ids.append(last_sprint)
        confirmed_sources = getattr(ref, "confirmed_sources", None)
        if confirmed_sources:
            for src in confirmed_sources:
                if src not in freshness.source_types:
                    freshness.source_types.append(src)
        self._stats["delta_index_skips"] += 1

    def _decide_skip(self, skip_set: set[str], ev: str, freshness: EntityFreshness) -> None:
        """Decide whether entity should be skipped and update stats."""
        if (
            freshness.distinct_sources >= MIN_SOURCES_FOR_CONFIRMED
            and freshness.distinct_sprints >= MIN_SPRINTS_FOR_CONFIRMED
            and freshness.avg_confidence >= MAX_CONFIDENCE_FOR_SKIP
        ):
            freshness.should_skip = True
            freshness.skip_reason = (
                f"confirmed by {freshness.distinct_sources} sources "
                f"across {freshness.distinct_sprints} sprints "
                f"(avg confidence={freshness.avg_confidence:.2f})"
            )
            skip_set.add(ev)
            self._stats["skipped"] += 1
        else:
            self._stats["allowed"] += 1

    async def _evict_stale_cache(self, now: float) -> None:
        """Evict stale cache entries if cache is too large."""
        async with self._lock:
            if len(self._skip_cache) > 1000:
                cutoff = now - self._skip_cache_ttl
                stale_keys = [k for k, (_, ts) in self._skip_cache.items() if ts < cutoff]
                for k in stale_keys:
                    del self._skip_cache[k]

    async def get_contradiction_flags(
        self,
        entities: list[dict[str, str]],
    ) -> list[ContradictionSignal]:
        """Check for contradictions in entity_observations across sources.

        A contradiction exists when different source_types report significantly
        different confidence values for the same entity (confidence gap >=40%).

        Args:
            entities: List of dicts with 'entity_value' and optional 'entity_type'.

        Returns:
            List of ContradictionSignal for entities with detected contradictions.
        """
        if not self._enabled or not entities:
            return []

        signals: list[ContradictionSignal] = []
        try:
            store = self._duckdb_store
            if store is None:
                return []

            for ent in entities[:MAX_GATE_ENTITIES]:
                ev = ent["entity_value"]
                et = ent.get("entity_type", "domain")
                try:
                    obs = await store.async_get_entity_observations_by_entity(
                        ev, limit=MAX_OBSERVATIONS_PER_ENTITY
                    )
                except Exception:
                    continue

                if len(obs) < 2:
                    continue

                # Group by source_type, compute per-source avg confidence
                source_confidences: dict[str, list[float]] = defaultdict(list)
                for o in obs:
                    source_confidences[o.get("source_type", "unknown")].append(
                        o.get("confidence", 0.0)
                    )

                if len(source_confidences) < 2:
                    continue

                source_avgs = {
                    src: sum(confs) / len(confs)
                    for src, confs in source_confidences.items()
                }

                max_src, max_conf = max(source_avgs.items(), key=lambda x: x[1])
                min_src, min_conf = min(source_avgs.items(), key=lambda x: x[1])

                gap = max_conf - min_conf
                if gap >= CONTRADICTION_CONFIDENCE_DELTA:
                    severity = min(gap / CONTRADICTION_CONFIDENCE_DELTA, 1.0)
                    signals.append(
                        ContradictionSignal(
                            entity_value=ev,
                            entity_type=et,
                            severity=severity,
                            conflicting_sources=[max_src, min_src],
                            confidence_gap=gap,
                            description=(
                                f"Confidence gap {gap:.2f} between {max_src} "
                                f"({max_conf:.2f}) and {min_src} ({min_conf:.2f})"
                            ),
                        )
                    )

        except Exception as e:
            logger.debug("[CrossSprintGate] contradiction check failed: %s", e)

        return signals

    async def _query_entity_batch(
        self,
        entities: list[dict[str, str]],
    ) -> dict[str, EntityFreshness]:
        """Query entity_observations for a batch of entity_values.

        Uses individual queries via async_get_entity_observations_by_entity()
        (the canonical read path). For hot-path optimization, entities that
        share the same value are deduplicated before querying.
        """
        store = self._duckdb_store
        if store is None:
            return {}

        # Deduplicate entity_values
        unique_values: set[str] = set()
        for ent in entities[:MAX_GATE_ENTITIES]:
            unique_values.add(ent["entity_value"])

        freshness_map: dict[str, EntityFreshness] = {}
        now = _time.time()

        for ev in unique_values:
            try:
                observations = await store.async_get_entity_observations_by_entity(
                    ev, limit=MAX_OBSERVATIONS_PER_ENTITY
                )
            except Exception:
                continue

            if not observations:
                freshness_map[ev] = EntityFreshness(
                    entity_value=ev,
                    freshness="novel",
                )
                continue

            # Compute aggregate metrics
            sources: set[str] = set()
            sprints: set[str] = set()
            confidences: list[float] = []
            source_types: list[str] = []
            max_ts: float = 0.0

            for obs in observations:
                src = obs.get("source_type", "unknown")
                sid = obs.get("sprint_id", "unknown")
                conf = obs.get("confidence", 0.0)
                ts = obs.get("ts", 0.0)

                sources.add(src)
                sprints.add(sid)
                confidences.append(conf)
                if src not in source_types:
                    source_types.append(src)
                if ts > max_ts:
                    max_ts = ts

            avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
            distinct_sources = len(sources)
            distinct_sprints = len(sprints)
            days_since_last = (now - max_ts) / 86400.0 if max_ts > 0 else float("inf")

            # Determine freshness level
            if (
                distinct_sources >= MIN_SOURCES_FOR_CONFIRMED
                and distinct_sprints >= MIN_SPRINTS_FOR_CONFIRMED
            ):
                if days_since_last > STALE_DAYS:
                    freshness = "stale"
                else:
                    freshness = "confirmed"
            elif distinct_sources >= 1:
                freshness = "seen"
            else:
                freshness = "novel"

            freshness_map[ev] = EntityFreshness(
                entity_value=ev,
                entity_type=(
                    observations[0].get("entity_type", "domain")
                    if observations
                    else ""
                ),
                freshness=freshness,
                distinct_sources=distinct_sources,
                distinct_sprints=distinct_sprints,
                avg_confidence=avg_conf,
                last_seen_ts=max_ts,
                observations_count=len(observations),
                source_types=source_types,
                sprint_ids=list(sprints),
            )

        return freshness_map

    def get_stats(self) -> dict[str, int]:
        """Return telemetry counters."""
        return dict(self._stats)

    def reset(self) -> None:
        """Clear skip cache (called on sprint shutdown)."""
        self._skip_cache.clear()
        self._stats = {k: 0 for k in self._stats}


# -- Singleton accessor --------------------------------------------------------
_cross_sprint_gate: CrossSprintGate | None = None


def get_cross_sprint_gate() -> CrossSprintGate:
    """Return the shared CrossSprintGate singleton."""
    global _cross_sprint_gate
    if _cross_sprint_gate is None:
        _cross_sprint_gate = CrossSprintGate()
    return _cross_sprint_gate
