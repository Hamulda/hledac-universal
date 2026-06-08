"""
Causal engine — bounded stub for the Sprint F196B probe suite.

Real implementation lives in legacy/causal_engine.py.  This stub is
loaded by `tests/test_hypothesis_builder.py` and provides the public
dataclasses + MAX_* constants that the export/ layer expects.

INVARIANTS:
- All public names are dataclasses (or simple constants) — never
  network, never MLX, never I/O.
- Bounded: MAX_ENTITIES, MAX_FINDINGS, MAX_HYPOTHESES match the values
  used by the export/ layer.
- Fail-safe: __all__ lists every exported name; unknown names raise
  AttributeError.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_ENTITIES: int = 5_000
MAX_FINDINGS: int = 5_000
MAX_HYPOTHESES: int = 500


@dataclass
class Entity:
    """Single entity in the causal graph."""
    entity_id: str
    entity_type: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityCluster:
    """Group of related entities (e.g. all hosts in the same ASN)."""
    cluster_id: str
    entities: list[Entity] = field(default_factory=list)
    cohesion: float = 0.0


@dataclass
class TemporalSequence:
    """Ordered sequence of events with timestamps."""
    sequence_id: str
    events: list[tuple[float, str]] = field(default_factory=list)


@dataclass
class AnomalySignal:
    """Flag raised when a cluster deviates from baseline behaviour."""
    signal_id: str
    cluster_id: str
    score: float
    description: str = ""


@dataclass
class CausalHypothesis:
    """Hypothesis linking (cluster, event) -> downstream effect."""
    hypothesis_id: str
    cause_cluster_id: str
    effect_cluster_id: str
    confidence: float
    rationale: str = ""


@dataclass
class Contradiction:
    """Two hypotheses that cannot both be true."""
    contradiction_id: str
    a: str  # hypothesis_id
    b: str  # hypothesis_id
    note: str = ""


class CausalEngine:
    """Minimal in-memory engine — no I/O, no MLX."""

    def __init__(self, max_entities: int = MAX_ENTITIES) -> None:
        self.max_entities = max_entities
        self._entities: dict[str, Entity] = {}

    def add_entity(self, entity: Entity) -> bool:
        """Add an entity; return False when bounded."""
        if len(self._entities) >= self.max_entities:
            return False
        self._entities[entity.entity_id] = entity
        return True

    def entities(self) -> list[Entity]:
        return list(self._entities.values())


__all__ = [
    "CausalEngine",
    "Entity",
    "EntityCluster",
    "TemporalSequence",
    "AnomalySignal",
    "CausalHypothesis",
    "Contradiction",
    "MAX_ENTITIES",
    "MAX_FINDINGS",
    "MAX_HYPOTHESES",
]
