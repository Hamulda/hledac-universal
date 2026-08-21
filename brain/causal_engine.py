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

import time
from typing import Any

import msgspec

from compat.msgspec_gc_compat import Struct

MAX_ENTITIES: int = 5000
MAX_FINDINGS: int = 5000
MAX_HYPOTHESES: int = 500
_TYPE_MAP: dict[str, str] = {"ipv4": "ip", "ipv6": "ip"}


class Entity(Struct):
    """Single entity in the causal graph."""

    entity_id: str
    entity_type: str
    value: str = ""
    attributes: dict[str, Any] = msgspec.field(default_factory=dict)


class EntityCluster(Struct, frozen=True):
    """Group of related entities (e.g. all hosts in the same ASN)."""

    cluster_id: str
    entities: list[Entity] = msgspec.field(default_factory=list)
    cohesion: float = 0.0


class TemporalSequence(Struct, frozen=True):
    """Ordered sequence of events with timestamps."""

    sequence_id: str
    events: list[tuple[float, str]] = msgspec.field(default_factory=list)

    @property
    def entities(self) -> list[str]:
        """Entity IDs in order."""
        return [e for _, e in self.events]

    @property
    def timestamps(self) -> list[float]:
        """Timestamps in order."""
        return [ts for ts, _ in self.events]


class AnomalySignal(Struct, frozen=True):
    """Flag raised when a cluster deviates from baseline behaviour."""

    signal_id: str
    cluster_id: str
    score: float
    description: str = ""
    anomaly_type: str = ""
    entities: tuple[str, ...] = ()


class CausalHypothesis(Struct, frozen=True):
    """Hypothesis linking (cluster, event) -> downstream effect."""

    hypothesis_id: str
    cause_cluster_id: str
    effect_cluster_id: str
    confidence: float
    rationale: str = ""
    source_entity: str = ""
    target_entity: str = ""


class Contradiction(Struct, frozen=True):
    """Two hypotheses that cannot both be true."""

    contradiction_id: str
    a: str
    b: str
    note: str = ""


class CausalEngine:
    """Minimal in-memory engine — no I/O, no MLX."""

    __slots__ = ("__sequences", "_entities", "max_entities")

    def __init__(self, max_entities: int = MAX_ENTITIES) -> None:
        self.max_entities = max_entities
        self._entities: dict[str, Entity] = {}
        self.__sequences: list[list[str]] = []

    def add_entity(self, entity: Entity) -> bool:
        """Add an entity; return False when bounded."""
        if len(self._entities) >= self.max_entities:
            return False
        self._entities[entity.entity_id] = entity
        return True

    def entities(self) -> list[Entity]:
        return list(self._entities.values())

    def extract_entities(self, findings: list[Any]) -> list[Entity]:
        """Extrahuje entity z findings pro grafovou analýzu."""
        from hledac.universal.brain.ner_engine import _ioc_type_to_entity_type, extract_iocs_from_text

        entities: list[Entity] = []
        seen: dict[str, Entity] = {}
        for finding in findings:
            payload = getattr(finding, "payload_text", "") or ""
            if not payload:
                continue
            iocs = extract_iocs_from_text(payload)
            for ioc in iocs:
                raw_type = ioc.get("ioc_type", "")
                entity_type = _TYPE_MAP.get(raw_type) or _ioc_type_to_entity_type(raw_type)
                value = ioc["value"]
                entity_id = f"{entity_type}:{value}"
                if entity_id not in seen and len(entities) < self.max_entities:
                    entity = Entity(
                        entity_id=entity_id,
                        entity_type=entity_type,
                        value=value,
                        attributes={"confidence": ioc.get("confidence", 0.5)},
                    )
                    seen[entity_id] = entity
                    entities.append(entity)
                    self._entities[entity_id] = entity
        return entities

    def build_temporal_sequences(self) -> list[TemporalSequence]:
        """Sestaví časové sekvence z extrahovaných entit."""
        if not self._entities:
            return []
        ts = time.time()
        seq = TemporalSequence(sequence_id="seq_0", events=[(ts, e.entity_id) for e in self.entities()])
        return [seq]

    def compute_co_occurrence_matrix(self) -> dict[str, dict[str, int]] | None:
        """Compute co-occurrence matrix. Stub returns None (skip numpy dep)."""
        return None

    async def generate_causal_hypotheses(self) -> list[CausalHypothesis]:
        """Generate causal hypotheses."""
        if not self._entities:
            return []
        entities = self.entities()
        if len(entities) < 2:
            return []
        hyp = CausalHypothesis(
            hypothesis_id="hyp_0",
            cause_cluster_id=entities[0].entity_id,
            effect_cluster_id=entities[1].entity_id,
            confidence=0.5,
            source_entity=entities[0].entity_id,
            target_entity=entities[1].entity_id,
        )
        return [hyp]

    async def generate_hypotheses(self, findings: list[Any]) -> list[CausalHypothesis]:
        """Full pipeline: extract + generate."""
        self.extract_entities(findings)
        return await self.generate_causal_hypotheses()

    def detect_anomalies(self, findings: list[Any]) -> list[AnomalySignal]:
        """Detect anomalies. Stub returns empty list."""
        return []

    def detect_contradictions(self, findings: list[Any]) -> list[Contradiction]:
        """Detect contradictions. Stub returns empty list."""
        return []

    @property
    def _sequences(self) -> list[list[str]]:
        return self.__sequences


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
