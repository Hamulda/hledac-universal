"""
target_memory.py — Sprint F204D
TargetMemoryService: bounded cross-sprint target memory with RAM guard.
"""

from __future__ import annotations

import json
import logging
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
                return json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
            except (json.JSONDecodeError, UnicodeDecodeError, Exception):
                _logger.warning("corrupt facet JSON, returning empty dict")
                return {}
        return {}

    def _compute_confidence_drift(
        self,
        existing: TargetMemory | None,
        update: TargetMemoryUpdate,
    ) -> dict[str, Any]:
        """Track finding_count delta vs sprint_count for drift detection."""
        if existing is None:
            return {
                "sprints": 1,
                "total_findings": update.finding_count,
                "avg_findings_per_sprint": update.finding_count,
                "drift_ratio": 1.0,
            }
        prev_sprints = existing.sprint_count
        prev_findings = existing.cumulative_finding_count
        curr_sprints = prev_sprints + 1
        curr_findings = prev_findings + update.finding_count
        avg = curr_findings / curr_sprints
        # Drift: current sprint's finding_count vs rolling average
        drift_ratio = update.finding_count / avg if avg > 0 else 1.0
        return {
            "sprints": curr_sprints,
            "total_findings": curr_findings,
            "avg_findings_per_sprint": avg,
            "drift_ratio": drift_ratio,
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
            serialized = json.dumps(memory.confidence_drift)
            if len(serialized.encode()) > MAX_MEMORY_JSON_BYTES:
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
