"""
target_memory.py — Sprint F204D
TargetMemoryService: bounded cross-sprint target memory with RAM guard.
"""

from __future__ import annotations

import logging
import orjson
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import psutil

if TYPE_CHECKING:
    from collections.abc import Mapping

# Bounds
MAX_MEMORY_ENTITIES = 500
MAX_MEMORY_EXPOSURES = 500
MAX_MEMORY_PIVOTS = 100
MAX_MEMORY_JSON_BYTES = 65536

# Sprint F206H: Drift explainability bounds
MAX_DRIFT_REASONS = 8
MAX_DRIFT_DELTA_KEYS = 20

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TargetMemoryUpdate:
    target_id: str
    sprint_id: str
    finding_count: int
    entity_facets: dict[str, Any]
    exposure_facets: dict[str, Any]
    pivot_facets: dict[str, Any]
    observed_ts: float


@dataclass(frozen=True)
class TargetMemory:
    target_id: str
    first_seen_ts: float
    last_seen_ts: float
    sprint_count: int
    cumulative_finding_count: int
    entity_facets: dict[str, Any]
    exposure_facets: dict[str, Any]
    pivot_facets: dict[str, Any]
    confidence_drift: dict[str, Any]
    updated_by_sprint_id: str


class TargetMemoryService:
    """Cross-sprint target memory with bounded facets and RAM guard."""

    def __init__(self) -> None:
        self._cache: dict[str, TargetMemory] = {}

    def _enforce_facet_bound(
        self,
        facets: Mapping[str, Any],
        max_size: int,
        facet_type: str,
    ) -> dict[str, Any]:
        """Fail-soft: truncate facets to max_size, log warning."""
        if len(facets) <= max_size:
            return dict(facets)
        _logger.warning(
            "target_id=%s %s_facets exceeds bound %d, truncating to %d",
            getattr(self, "_last_target_id", "?"),
            facet_type,
            max_size,
            max_size,
        )
        return dict(list(facets.items())[:max_size])

    def _safe_parse_facets(self, raw: Any) -> dict[str, Any]:
        """Fail-soft: corrupt JSON/None → empty dict."""
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, (str, bytes)):
            try:
                # GHOST_INVARIANT: fail-soft, corrupt JSON → empty facet
                return orjson.loads(raw)
            except (orjson.JSONDecodeError, Exception):
                _logger.warning("corrupt facet JSON, returning empty dict")
                return {}
        return {}

    def _compute_facet_delta(
        self,
        existing: dict[str, Any],
        update: dict[str, Any],
        max_keys: int,
    ) -> dict[str, Any]:
        """
        Compute bounded key-level delta between existing and new facets.

        Returns dict with:
          - added: count of new keys not in existing
          - removed: count of keys in existing not in update
          - stable: count of keys in both (any value change is a churn signal)
          - total_prev: total keys in existing (capped)
          - total_curr: total keys in update (capped)
          - top_added: up to 5 added keys by value score (descending)
          - top_removed: up to 5 removed keys by value score
        """
        existing_keys = set(existing.keys())
        update_keys = set(update.keys())
        added_keys = update_keys - existing_keys
        removed_keys = existing_keys - update_keys
        stable_keys = update_keys & existing_keys

        added_list = sorted(
            added_keys,
            key=lambda k: update.get(k, 0),
            reverse=True,
        )
        removed_list = sorted(
            removed_keys,
            key=lambda k: existing.get(k, 0),
            reverse=True,
        )

        return {
            "added": len(added_keys),
            "removed": len(removed_keys),
            "stable": len(stable_keys),
            "total_prev": min(len(existing_keys), max_keys),
            "total_curr": min(len(update_keys), max_keys),
            "top_added": added_list[:5],
            "top_removed": removed_list[:5],
        }

    def _compute_drift_reasons(
        self,
        drift_ratio: float,
        entity_delta: dict[str, Any],
        exposure_delta: dict[str, Any],
        pivot_delta: dict[str, Any],
    ) -> list[str]:
        """
        Sprint F206H: Compute deterministic, bounded list of drift reason strings.

        Merge is pure Python, deterministic. Falls back to drift_ratio if
        existing memory does not have the new delta keys (backwards-compatible).
        """
        reasons: list[str] = []

        # Finding rate drift
        if drift_ratio > 1.5:
            reasons.append(f"finding_rate_high:ratio={drift_ratio:.2f}")
        elif 0.0 < drift_ratio < 0.5:
            reasons.append(f"finding_rate_low:ratio={drift_ratio:.2f}")

        # Entity type shift
        if entity_delta["added"] > 5:
            reasons.append(f"entity_new_types:{entity_delta['added']}_added")
        if entity_delta["removed"] > 3:
            reasons.append(f"entity_dropped_types:{entity_delta['removed']}_removed")
        if entity_delta["total_curr"] > entity_delta["total_prev"] * 1.5:
            reasons.append("entity_expansion:high_churn")
        elif entity_delta["total_curr"] < entity_delta["total_prev"] * 0.5:
            reasons.append("entity_contraction:sharp_decline")

        # Exposure type shift
        if exposure_delta["added"] > 5:
            reasons.append(f"exposure_new_types:{exposure_delta['added']}_added")
        if exposure_delta["removed"] > 3:
            reasons.append(f"exposure_dropped_types:{exposure_delta['removed']}_removed")

        # Pivot type shift
        if pivot_delta["added"] > 3:
            reasons.append(f"pivot_new_types:{pivot_delta['added']}_added")
        if pivot_delta["removed"] > 2:
            reasons.append(f"pivot_dropped_types:{pivot_delta['removed']}_removed")

        # Top added entity types
        for key in entity_delta.get("top_added", [])[:3]:
            reasons.append(f"new_entity:{key}")

        return reasons[:MAX_DRIFT_REASONS]

    def _compute_confidence_drift(
        self,
        existing: TargetMemory | None,
        update: TargetMemoryUpdate,
    ) -> dict[str, Any]:
        """
        Sprint F206H: Track finding_count delta + explainable facet deltas.

        Extends F204D drift_ratio with bounded entity_delta, exposure_delta,
        pivot_delta, and drift_reasons. Falls back to legacy drift_ratio
        when existing memory lacks new keys (backwards-compatible).
        """
        if existing is None:
            return {
                "sprints": 1,
                "total_findings": update.finding_count,
                "avg_findings_per_sprint": update.finding_count,
                "drift_ratio": 1.0,
                "entity_delta": {},
                "exposure_delta": {},
                "pivot_delta": {},
                "drift_reasons": [],
            }

        prev_sprints = existing.sprint_count
        prev_findings = existing.cumulative_finding_count
        curr_sprints = prev_sprints + 1
        curr_findings = prev_findings + update.finding_count
        avg = curr_findings / curr_sprints
        drift_ratio = update.finding_count / avg if avg > 0 else 1.0

        # Compute facet deltas (bounded)
        entity_delta = self._compute_facet_delta(
            existing.entity_facets, update.entity_facets, MAX_DRIFT_DELTA_KEYS
        )
        exposure_delta = self._compute_facet_delta(
            existing.exposure_facets, update.exposure_facets, MAX_DRIFT_DELTA_KEYS
        )
        pivot_delta = self._compute_facet_delta(
            existing.pivot_facets, update.pivot_facets, MAX_DRIFT_DELTA_KEYS
        )

        drift_reasons = self._compute_drift_reasons(
            drift_ratio, entity_delta, exposure_delta, pivot_delta
        )

        return {
            "sprints": curr_sprints,
            "total_findings": curr_findings,
            "avg_findings_per_sprint": avg,
            "drift_ratio": drift_ratio,
            "entity_delta": entity_delta,
            "exposure_delta": exposure_delta,
            "pivot_delta": pivot_delta,
            "drift_reasons": drift_reasons,
        }

    def merge_update(self, update: TargetMemoryUpdate) -> TargetMemory:
        """
        Merge update into existing target memory or create new.
        RAM guard: skip merge if RSS > high_water.
        Bounds enforcement for entity_facets, exposure_facets, pivot_facets.
        """
        # RAM guard: GHOST_INVARIANT — skip merge if virtual_memory percent >= 90%
        try:
            current_pct = psutil.virtual_memory().percent
        except Exception:
            current_pct = 0.0
        if current_pct >= 90.0:
            _logger.warning(
                "target_id=%s RAM guard active (mem_pct=%.1f%%), returning existing or empty",
                update.target_id,
                current_pct,
            )
            return self._cache.get(
                update.target_id,
                TargetMemory(
                    target_id=update.target_id,
                    first_seen_ts=update.observed_ts,
                    last_seen_ts=update.observed_ts,
                    sprint_count=0,
                    cumulative_finding_count=0,
                    entity_facets={},
                    exposure_facets={},
                    pivot_facets={},
                    confidence_drift={},
                    updated_by_sprint_id="",
                ),
            )

        existing = self._cache.get(update.target_id)

        # Bounds-enforced facet merge
        entity_facets = self._enforce_facet_bound(
            update.entity_facets, MAX_MEMORY_ENTITIES, "entity"
        )
        exposure_facets = self._enforce_facet_bound(
            update.exposure_facets, MAX_MEMORY_EXPOSURES, "exposure"
        )
        pivot_facets = self._enforce_facet_bound(
            update.pivot_facets, MAX_MEMORY_PIVOTS, "pivot"
        )

        if existing is None:
            memory = TargetMemory(
                target_id=update.target_id,
                first_seen_ts=update.observed_ts,
                last_seen_ts=update.observed_ts,
                sprint_count=1,
                cumulative_finding_count=update.finding_count,
                entity_facets=entity_facets,
                exposure_facets=exposure_facets,
                pivot_facets=pivot_facets,
                confidence_drift=self._compute_confidence_drift(None, update),
                updated_by_sprint_id=update.sprint_id,
            )
        else:
            memory = TargetMemory(
                target_id=update.target_id,
                first_seen_ts=existing.first_seen_ts,
                last_seen_ts=max(existing.last_seen_ts, update.observed_ts),
                sprint_count=existing.sprint_count + 1,
                cumulative_finding_count=existing.cumulative_finding_count + update.finding_count,
                entity_facets=entity_facets,
                exposure_facets=exposure_facets,
                pivot_facets=pivot_facets,
                confidence_drift=self._compute_confidence_drift(existing, update),
                updated_by_sprint_id=update.sprint_id,
            )

        # JSON size bound enforcement
        try:
            serialized = orjson.dumps(memory.confidence_drift)
            if len(serialized) > MAX_MEMORY_JSON_BYTES:
                _logger.warning(
                    "target_id=%s confidence_drift exceeds %d bytes, truncating",
                    update.target_id,
                    MAX_MEMORY_JSON_BYTES,
                )
        except Exception:
            pass  # fail-soft

        self._cache[update.target_id] = memory
        return memory

    def get(self, target_id: str) -> TargetMemory | None:
        """Return cached memory for target_id."""
        return self._cache.get(target_id)

    def clear(self) -> None:
        """Clear in-memory cache."""
        self._cache.clear()

    @property
    def cache_size(self) -> int:
        """Number of cached targets."""
        return len(self._cache)
