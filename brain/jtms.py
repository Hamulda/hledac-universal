"""
JTMS — Justification-based Truth Maintenance System.

Provides belief revision with dependency tracking for multi-source evidence fusion.
When a source is retracted, all facts derived from it are revised automatically.

Architecture:
    Justification: frozen dataclass tracking source_ids + inference_rule + timestamps
    EvidenceRecord: Immutable log entry for Dempster-Shafer revision
    JTMS: Core coordinator managing justifications and dependency graphs

Usage:
    from hledac.universal.brain.jtms import JTMS, Justification

    jtms = JTMS()
    fact_id = jtms.add_fact(
        ioc_id="ip:abc123",
        source_ids=["source_a", "source_b"],
        inference_rule="dst_fusion",
        confidence=0.85,
        timestamp=time.time()
    )

    # Retract a source — all dependent facts are revised
    revised_count = jtms.retract_source("source_a")
"""

import math
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Justification:
    """
    Immutable justification for a belief — tracks WHY a fact is believed.

    Attributes:
        fact_id: Unique identifier for the fact being justified
        source_ids: Tuple of source identifiers that support this fact
        inference_rule: Name of the rule/algorithm that derived this fact
                       (e.g., "dst_fusion", "beta_binomial", "manual")
        timestamp: Unix timestamp when this justification was created
        source_reliability: Aggregate reliability score of sources (0..1)
    """
    fact_id: str
    source_ids: tuple[str, ...]
    inference_rule: str
    timestamp: float
    source_reliability: float = 1.0

    def depends_on(self, source_id: str) -> bool:
        """Check if this justification depends on a specific source."""
        return source_id in self.source_ids


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """
    Immutable evidence log entry for Dempster-Shafer revision.

    Used by SHAFER-2 incremental revision operator to support O(1) per-hypothesis
    retract without full recompute.

    Attributes:
        evidence_id: Unique identifier for this evidence piece
        hypothesis: Target hypothesis this evidence supports
        mass: Evidence mass (0..1)
        source_weight: Source reliability weight (0..1)
        source_id: Identifier of the source providing this evidence
        timestamp: Unix timestamp when evidence was added
    """
    evidence_id: str
    hypothesis: str
    mass: float
    source_weight: float
    source_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class BetaEvidenceRecord:
    """
    Immutable evidence record for Beta-Binomial with temporal decay.

    Attributes:
        evidence_id: Unique identifier
        weight: Evidence weight (positive = support, negative = contradict)
        source_id: Source identifier
        timestamp: Unix timestamp when evidence was added
    """
    evidence_id: str
    weight: float
    source_id: str
    timestamp: float


class JTMS:
    """
    Justification-based Truth Maintenance System.

    Manages a dependency graph of facts → justifications → sources.
    When a source is retracted, all facts justified by that source are
    automatically revised or removed.

    Memory-efficient for M1 8GB:
        - Justifications stored in dict (O(1) lookup)
        - Source → fact index for fast retract (O(k) where k = facts per source)
        - No external DB required — pure in-memory with optional persistence
    """

    __slots__ = ('_facts', '_justifications', '_source_index', '_fact_counter')

    def __init__(self) -> None:
        self._facts: dict[str, dict[str, Any]] = {}
        self._justifications: dict[str, Justification] = {}
        self._source_index: dict[str, set[str]] = {}  # source_id → set of fact_ids
        self._fact_counter: int = 0

    def add_fact(
        self,
        ioc_id: str,
        source_ids: list[str] | tuple[str, ...],
        inference_rule: str,
        confidence: float,
        timestamp: float | None = None,
        source_reliability: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Add a fact with justification tracking.

        Args:
            ioc_id: IOC identifier this fact pertains to
            source_ids: List of source identifiers supporting this fact
            inference_rule: Algorithm/rule that derived this fact
            confidence: Confidence score (0..1)
            timestamp: Unix timestamp (defaults to now)
            source_reliability: Aggregate source reliability (0..1)
            metadata: Optional additional metadata

        Returns:
            fact_id: Unique identifier for the created fact
        """
        self._fact_counter += 1
        fact_id = f"fact_{self._fact_counter:08d}"

        if timestamp is None:
            timestamp = time.time()

        source_tuple = tuple(source_ids)
        justification = Justification(
            fact_id=fact_id,
            source_ids=source_tuple,
            inference_rule=inference_rule,
            timestamp=timestamp,
            source_reliability=source_reliability,
        )

        self._facts[fact_id] = {
            'ioc_id': ioc_id,
            'confidence': confidence,
            'justification': justification,
            'metadata': metadata or {},
            'active': True,
        }
        self._justifications[fact_id] = justification

        # Update source index
        for source_id in source_ids:
            if source_id not in self._source_index:
                self._source_index[source_id] = set()
            self._source_index[source_id].add(fact_id)

        return fact_id

    def retract_source(self, source_id: str) -> int:
        """
        Retract all facts justified by a specific source.

        Finds all facts where source_id ∈ justification.source_ids,
        marks them inactive, and removes them from the source index.

        Args:
            source_id: Source identifier to retract

        Returns:
            revised_count: Number of facts retracted
        """
        if source_id not in self._source_index:
            return 0

        affected_fact_ids = self._source_index[source_id].copy()
        revised_count = 0

        for fact_id in affected_fact_ids:
            if fact_id not in self._facts:
                continue

            fact = self._facts[fact_id]
            if not fact['active']:
                continue

            # Mark fact as inactive
            fact['active'] = False
            revised_count += 1

            # Remove from all source indices
            justification = fact['justification']
            for sid in justification.source_ids:
                if sid in self._source_index:
                    self._source_index[sid].discard(fact_id)

        # Clean up source index
        if source_id in self._source_index:
            del self._source_index[source_id]

        return revised_count

    def get_fact(self, fact_id: str) -> dict[str, Any] | None:
        """Retrieve a fact by ID (returns None if not found or inactive)."""
        fact = self._facts.get(fact_id)
        if fact and fact['active']:
            return fact
        return None

    def get_justification(self, fact_id: str) -> Justification | None:
        """Retrieve the justification for a fact."""
        return self._justifications.get(fact_id)

    def get_facts_by_source(self, source_id: str) -> list[str]:
        """Get all active fact IDs justified by a source."""
        if source_id not in self._source_index:
            return []
        return [fid for fid in self._source_index[source_id] if self._facts[fid]['active']]

    def get_facts_by_ioc(self, ioc_id: str) -> list[dict[str, Any]]:
        """Get all active facts for a specific IOC."""
        return [
            fact for fact in self._facts.values()
            if fact['active'] and fact['ioc_id'] == ioc_id
        ]

    def stats(self) -> dict[str, int]:
        """Return JTMS statistics."""
        active_facts = sum(1 for f in self._facts.values() if f['active'])
        return {
            'total_facts': len(self._facts),
            'active_facts': active_facts,
            'inactive_facts': len(self._facts) - active_facts,
            'tracked_sources': len(self._source_index),
        }


def apply_temporal_decay(
    base_confidence: float,
    timestamp: float,
    decay_lambda: float = 0.01,
    current_time: float | None = None,
) -> float:
    """
    Apply exponential temporal decay to a confidence score.

    effective_conf = base_conf * exp(-λ * Δt_hours)

    Args:
        base_confidence: Original confidence score (0..1)
        timestamp: Unix timestamp when the fact was created
        decay_lambda: Decay rate per hour (default 0.01 = 1% per hour)
        current_time: Current time (defaults to now)

    Returns:
        Decayed confidence score (0..1)
    """
    if current_time is None:
        current_time = time.time()

    delta_hours = (current_time - timestamp) / 3600.0
    if delta_hours < 0:
        delta_hours = 0

    decay_factor = math.exp(-decay_lambda * delta_hours)
    return base_confidence * decay_factor


__all__ = [
    'JTMS',
    'Justification',
    'EvidenceRecord',
    'BetaEvidenceRecord',
    'apply_temporal_decay',
]
