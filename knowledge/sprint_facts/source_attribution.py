"""
from __future__ import annotations
Source Attribution — Sprint Facts Tiers 1 & 3
=============================================




Sprint scorecard and source hit log for sprint-level analytics.

MIGRATION NOTE (Issue #2):
    Extracted from knowledge/duckdb_store.py to enable independent testing
    and reduce monolith size. These are read-heavy, append-only tables.
"""
from __future__ import annotations

from typing import Any

import msgspec


class SourceHitLog(msgspec.Struct, frozen=True, gc=False):
    """
    Per-sprint source attribution record.

    Fields:
        sprint_id:       Sprint identifier
        source_type:     Source type (e.g., "web", "document", "synthetic")
        hit_rate:        Fraction of queries that returned findings [0.0, 1.0]
        total_queries:   Number of queries to this source
        findings_count:  Number of findings from this source
    """

    sprint_id: str
    source_type: str
    hit_rate: float
    total_queries: int
    findings_count: int


class SprintScorecard(msgspec.Struct, frozen=True, gc=False):
    """
    Per-sprint aggregated scores.

    Fields:
        sprint_id:           Sprint identifier
        query:               Research query text
        duration_s:           Sprint duration in seconds
        fpm:                 Findings per minute
        ioc_density:         IOC density score [0.0, 1.0]
        synthesis_confidence: Synthesis confidence score [0.0, 1.0]
        new_findings:        Number of new findings in this sprint
        dedup_hits:          Number of deduplication hits
        ioc_nodes:           Number of IOC nodes processed
    """

    sprint_id: str
    query: str
    duration_s: float
    fpm: float
    ioc_density: float
    synthesis_confidence: float
    new_findings: int
    dedup_hits: int
    ioc_nodes: int


class SprintDelta(msgspec.Struct, frozen=True, gc=False):
    """
    Per-sprint delta metrics.

    Fields:
        sprint_id:       Sprint identifier
        query:           Research query text
        duration_s:      Sprint duration in seconds
        new_findings:   Number of new findings in this sprint
        dedup_hits:     Number of deduplication hits
        ioc_nodes:      Number of IOC nodes processed
    """

    sprint_id: str
    query: str
    duration_s: float
    new_findings: int
    dedup_hits: int
    ioc_nodes: int
