"""Re-export stub — canonical source is legacy.atomic_storage."""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "hledac.universal.knowledge.atomic_storage is DEPRECATED. "
    "Use hledac.universal.knowledge.duckdb_store instead.",
    DeprecationWarning,
    stacklevel=2,
)

from legacy.atomic_storage import (
    ZSTD_AVAILABLE,
    AtomicJSONKnowledgeGraph,
    Claim,
    ClaimCluster,
    ClaimClusterIndex,
    EvidencePacket,
    EvidencePacketStorage,
    KnowledgeEntry,
    PatternStats,
    PatternStatsManager,
    ShardCache,
    SnapshotEntry,
    SnapshotStorage,
    SourceQualityScorer,
    StanceScorer,
    VeracityPriorCalculator,
    clear_storage_cache,
    get_atomic_storage,
)

__all__ = [
    "AtomicJSONKnowledgeGraph",
    "KnowledgeEntry",
    "ShardCache",
    "get_atomic_storage",
    "clear_storage_cache",
    "SnapshotEntry",
    "SnapshotStorage",
    "EvidencePacket",
    "Claim",
    "ClaimCluster",
    "ClaimClusterIndex",
    "EvidencePacketStorage",
    "PatternStats",
    "PatternStatsManager",
    "SourceQualityScorer",
    "VeracityPriorCalculator",
    "StanceScorer",
    "ZSTD_AVAILABLE",
]

