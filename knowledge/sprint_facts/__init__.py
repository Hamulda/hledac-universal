"""
sprint_facts — Sprint Facts Package
===================================

Canonical package for sprint-level facts and derived analytics.

Sub-modules:
- canonical_finding: CanonicalFinding DTO, FindingQualityDecision, ActivationResult
- source_attribution: SourceHitLog, SprintScorecard, SprintDelta
- migration: schema migration runner

TIER 1 -- SPRINT FACTS (DuckDB, durable):
    sprint_delta       -- per-sprint metrics: query, duration, new_findings, dedup_hits, ioc_nodes
    sprint_scorecard   -- per-sprint aggregated scores: fpm, ioc_density, synthesis_confidence
    source_hit_log     -- per-sprint source attribution: source_type, hit_rate

TIER 2 -- SHADOW FINDINGS (DuckDB, durable):
    canonical_findings    -- finding-level records forwarded from EvidenceLog.append()

TIER 3 -- CROSS-SPRINT (DuckDB, append-only, pruneable):
    temporal_events    -- time-indexed events for temporal archaeology
"""

import logging

logger = logging.getLogger(__name__)

from .canonical_finding import (  # noqa: E402
    ActivationResult,
    CanonicalFinding,
    FindingQualityDecision,
)
from .source_attribution import (  # noqa: E402
    SourceHitLog,
    SprintDelta,
    SprintScorecard,
)

__all__ = [
    "CanonicalFinding",
    "FindingQualityDecision",
    "ActivationResult",
    "SourceHitLog",
    "SprintScorecard",
    "SprintDelta",
]
