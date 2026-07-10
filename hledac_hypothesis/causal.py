"""
Hypothesis Engine — Causal Reasoning (C4 Tier-5 Extraction)
============================================================

Extracted from :mod:`brain.hypothesis_engine_engine` to break the remaining 3 126 LOC
orchestrator into focused modules. This module hosts :class:`CausalReasoner`
— the self-contained causal inference component responsible for entity
extraction, temporal sequencing, co-occurrence matrix computation, anomaly
detection, and causal hypothesis generation building blocks.

Design rationale (Sprint F259 → Tier-5):
- Originally, the causal pipeline was 10+ methods spread across
  ``HypothesisEngine`` (``extract_causal_entities``, ``build_temporal_sequences``,
  ``compute_co_occurrence_matrix``, ``get_co_occurrence``,
  ``detect_causal_anomalies``, ``generate_causal_hypotheses``,
  ``_extract_iocs_from_text``, ``_is_valid_ip``,
  ``_calculate_causal_confidence``, ``_generate_causal_statement``).
- These methods share a tight storage subset (``_causal_entities``,
  ``_co_occurrence_matrix``, ``_entity_id_to_idx``, ``_idx_to_entity_id``,
  ``_temporal_sequences``, ``_anomaly_signals``, ``_source_types``) and
  operate ONLY on that subset + standard library / numpy (optional).
- They have zero dependency on adversarial verification, hypothesis
  ranking, MLX inference, or any other engine subsystem.
- Extracted as :class:`CausalReasoner` so the engine retains a thin facade
  that delegates; full state isolation between engine ranking tests and
  causal entity storage.

GHOST_INVARIANTS:
- All public HypothesisEngine causal methods (``extract_causal_entities``,
  ``build_temporal_sequences``, ``compute_co_occurrence_matrix``,
  ``get_co_occurrence``, ``detect_causal_anomalies``,
  ``generate_causal_hypotheses``) keep their original signatures and
  behaviour — existing call sites and tests remain unchanged.
- ``CausalReasoner`` is **standalone** — no dependency on
  :mod:`brain.hypothesis_engine_engine`. The engine wraps it as a private
  delegate (``self._causal_reasoner``).
- ``generate_causal_hypotheses`` stays a facade on the engine class because
  it coordinates 3+ causal methods AND needs the engine's
  ``_source_types`` aggregate. The implementation lives on
  :class:`CausalReasoner` as :meth:`CausalReasoner.generate_hypotheses` and
  the engine facade delegates to it.
- M1 8GB UMA bounds preserved: ``MAX_CAUSAL_ENTITIES``,
  ``MAX_CAUSAL_FINDINGS``, ``MAX_CAUSAL_HYPOTHESES``,
  ``MAX_CO_OCCURRENCE_MATRIX_SIZE``, ``CO_OCCURRENCE_FP16``.
- The ``defaultdict`` import required by ``compute_co_occurrence_matrix``
  is local to that method (moved with the code).
- ``time`` import was implicit in the original (``time.time()`` call site)
  — added explicit local import in :meth:`extract_entities` (was relying
  on module-level time; we replicate that surface).

M1 8GB Optimizations preserved:
- FP16 matrix dtype (CO_OCCURRENCE_FP16=True)
- Bounded entity storage with deterministic eviction via MAX_CAUSAL_ENTITIES
- np.zeros lazy alloc; falls back to None on ImportError
"""
from __future__ import annotations


import logging
import re
import time
from collections import defaultdict
from typing import Any

from ._types import (
    CO_OCCURRENCE_FP16,
    MAX_CAUSAL_ENTITIES,
    MAX_CAUSAL_FINDINGS,
    MAX_CAUSAL_HYPOTHESES,
    MAX_CO_OCCURRENCE_MATRIX_SIZE,
    AnomalySignal,
    CausalEntity,
    CausalHypothesis,
    TemporalSequence,
)

logger = logging.getLogger(__name__)


class CausalReasoner:
    """
    Self-contained causal reasoning component.

    Owns its own entity / sequence / matrix / anomaly storage and provides
    the full causal pipeline. The :class:`HypothesisEngine` wraps a single
    :class:`CausalReasoner` instance and delegates to it for backward
    compatibility.

    Storage fields (all instance-local, no engine coupling):
        - _causal_entities: dict[str, CausalEntity] — extracted entities
        - _co_occurrence_matrix: numpy array | None — co-occurrence scores
        - _entity_id_to_idx / _idx_to_entity_id: index maps
        - _temporal_sequences: list[TemporalSequence]
        - _anomaly_signals: list[AnomalySignal]
        - _source_types: set[str] — observed source_type values
    """

    def __init__(self) -> None:
        self._causal_entities: dict[str, CausalEntity] = {}
        self._co_occurrence_matrix: Any | None = None
        self._entity_id_to_idx: dict[str, int] = {}
        self._idx_to_entity_id: dict[int, str] = {}
        self._temporal_sequences: list[TemporalSequence] = []
        self._anomaly_signals: list[AnomalySignal] = []
        self._source_types: set[str] = set()

    # -------------------------------------------------------------------------
    # Entity extraction
    # -------------------------------------------------------------------------

    def extract_entities(self, findings: list[Any]) -> list[CausalEntity]:
        """
        Extract entities from findings for causal reasoning.

        Args:
            findings: List of CanonicalFinding or finding-like objects
                (duck-typed: ``payload_text``, ``source_type``,
                ``finding_id``, ``ts``).

        Returns:
            List of newly added CausalEntity objects (excluding merged
            duplicates).
        """
        import time

        entities: list[CausalEntity] = []
        seen_values: dict[str, str] = {}

        for i, finding in enumerate(findings):
            if i >= MAX_CAUSAL_FINDINGS:
                break

            payload = getattr(finding, "payload_text", "") or ""
            source_type = getattr(finding, "source_type", "unknown")
            finding_id = getattr(finding, "finding_id", f"finding_{i}")
            ts = getattr(finding, "ts", time.time())

            self._source_types.add(source_type)
            extracted = self._extract_iocs_from_text(payload, source_type, finding_id, ts)

            for entity in extracted:
                if entity.value not in seen_values:
                    seen_values[entity.value] = entity.entity_id
                    if len(self._causal_entities) < MAX_CAUSAL_ENTITIES:
                        self._causal_entities[entity.entity_id] = entity
                        entities.append(entity)
                else:
                    existing_id = seen_values[entity.value]
                    if existing_id in self._causal_entities:
                        existing = self._causal_entities[existing_id]
                        new_sources = existing.source_findings + entity.source_findings
                        self._causal_entities[existing_id] = CausalEntity(
                            entity_id=existing.entity_id,
                            entity_type=existing.entity_type,
                            value=existing.value,
                            source_findings=new_sources[:100],
                            first_seen=min(existing.first_seen, entity.first_seen),
                            last_seen=max(existing.last_seen, entity.last_seen),
                        )

        logger.info(
            f"CausalReasoner: extracted {len(entities)} entities from "
            f"{len(findings)} findings"
        )
        return entities

    def _extract_iocs_from_text(
        self,
        text: str,
        source_type: str,
        finding_id: str,
        ts: float,
    ) -> list[CausalEntity]:
        """Extract IOCs (IP, domain, email, URL) from text."""
        entities = []

        # IP patterns
        ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
        for match in re.findall(ip_pattern, text):
            if self._is_valid_ip(match):
                entities.append(CausalEntity(
                    entity_id=f"ip_{match}",
                    entity_type="ip",
                    value=match,
                    source_findings=(finding_id,),
                    first_seen=ts,
                    last_seen=ts,
                ))

        # Domain patterns
        domain_pattern = r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b'
        for match in re.findall(domain_pattern, text):
            if len(match) > 4 and match not in ("example.com", "test.com", "localhost"):
                entities.append(CausalEntity(
                    entity_id=f"domain_{match}",
                    entity_type="domain",
                    value=match,
                    source_findings=(finding_id,),
                    first_seen=ts,
                    last_seen=ts,
                ))

        # Email patterns
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        for match in re.findall(email_pattern, text):
            entities.append(CausalEntity(
                entity_id=f"email_{match}",
                entity_type="email",
                value=match,
                source_findings=(finding_id,),
                first_seen=ts,
                last_seen=ts,
            ))

        # URL patterns
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        for match in re.findall(url_pattern, text):
            entities.append(CausalEntity(
                entity_id=f"url_{match[:100]}",
                entity_type="url",
                value=match[:200],
                source_findings=(finding_id,),
                first_seen=ts,
                last_seen=ts,
            ))

        return entities

    @staticmethod
    def _is_valid_ip(ip: str) -> bool:
        """Validate IP address."""
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        try:
            return all(0 <= int(p) <= 255 for p in parts)
        except ValueError:
            return False

    # -------------------------------------------------------------------------
    # Temporal sequencing
    # -------------------------------------------------------------------------

    def build_temporal_sequences(self, gap_threshold: float = 3600.0) -> list[TemporalSequence]:
        """
        Build temporal sequences from entity timestamps.

        Args:
            gap_threshold: Seconds within which entities are in same
                sequence (default: 1 hour).

        Returns:
            List of TemporalSequence objects.
        """
        by_time: list[tuple[float, CausalEntity]] = [
            (e.last_seen, e) for e in self._causal_entities.values() if e.last_seen > 0
        ]
        by_time.sort(key=lambda x: x[0])

        sequences: list[TemporalSequence] = []
        current_seq: list[str] = []
        current_ts: list[float] = []
        current_findings: set[str] = set()

        for ts, entity in by_time:
            if not current_seq:
                current_seq.append(entity.entity_id)
                current_ts.append(ts)
                current_findings.update(entity.source_findings)
            else:
                if ts - current_ts[-1] <= gap_threshold:
                    current_seq.append(entity.entity_id)
                    current_ts.append(ts)
                    current_findings.update(entity.source_findings)
                else:
                    if len(current_seq) >= 2:
                        sequences.append(TemporalSequence(
                            sequence_id=f"seq_{len(sequences)}",
                            entities=current_seq,
                            timestamps=current_ts,
                            source_findings=tuple(current_findings),
                            confidence=min(1.0, len(current_seq) / 5.0),
                        ))
                    current_seq = [entity.entity_id]
                    current_ts = [ts]
                    current_findings = set(entity.source_findings)

        if len(current_seq) >= 2:
            sequences.append(TemporalSequence(
                sequence_id=f"seq_{len(sequences)}",
                entities=current_seq,
                timestamps=current_ts,
                source_findings=tuple(current_findings),
                confidence=min(1.0, len(current_seq) / 5.0),
            ))

        self._temporal_sequences = sequences
        logger.info(f"CausalReasoner: built {len(sequences)} temporal sequences")
        return sequences

    # -------------------------------------------------------------------------
    # Co-occurrence matrix
    # -------------------------------------------------------------------------

    def compute_co_occurrence_matrix(self) -> Any | None:
        """
        Compute co-occurrence matrix using numpy float16 for M1 RAM savings.

        Returns:
            numpy array or None if too many entities / numpy unavailable.
        """
        try:
            import numpy as np

            entities = list(self._causal_entities.values())
            n = len(entities)

            if n > MAX_CO_OCCURRENCE_MATRIX_SIZE or n == 0:
                return None

            self._entity_id_to_idx = {e.entity_id: i for i, e in enumerate(entities)}
            self._idx_to_entity_id = {i: e.entity_id for i, e in enumerate(entities)}

            dtype = np.float16 if CO_OCCURRENCE_FP16 else np.float32
            matrix = np.zeros((n, n), dtype=dtype)

            finding_to_entities: dict[str, set[str]] = defaultdict(set)
            for entity in entities:
                for fid in entity.source_findings:
                    finding_to_entities[fid].add(entity.entity_id)

            for fid, entity_ids in finding_to_entities.items():  # noqa: B007
                entity_list = list(entity_ids)
                for e1 in entity_list:
                    for e2 in entity_list:
                        if e1 in self._entity_id_to_idx and e2 in self._entity_id_to_idx:
                            idx1 = self._entity_id_to_idx[e1]
                            idx2 = self._entity_id_to_idx[e2]
                            matrix[idx1, idx2] += 1

            self._co_occurrence_matrix = matrix
            logger.info(
                f"CausalReasoner: computed {n}x{n} co-occurrence matrix "
                f"(dtype={dtype.__name__})"
            )
            return matrix

        except ImportError:
            logger.warning("CausalReasoner: numpy not available, skipping co-occurrence")
            return None
        except Exception as e:
            logger.error(f"CausalReasoner: co-occurrence failed: {e}")
            return None

    def get_co_occurrence(self, entity_a: str, entity_b: str) -> float:
        """Get co-occurrence score between two entities."""
        if self._co_occurrence_matrix is None:
            return 0.0

        idx_a = self._entity_id_to_idx.get(entity_a)
        idx_b = self._entity_id_to_idx.get(entity_b)

        if idx_a is None or idx_b is None:
            return 0.0

        return float(self._co_occurrence_matrix[idx_a, idx_b])

    # -------------------------------------------------------------------------
    # Anomaly detection
    # -------------------------------------------------------------------------

    def detect_anomalies(self, findings: list[Any]) -> list[AnomalySignal]:
        """
        Detect anomalies from unexpected source combinations.

        Args:
            findings: List of findings for source analysis (kept for
                forward-compat with future per-finding anomaly detection;
                current implementation scans stored entities only).

        Returns:
            List of AnomalySignal objects.
        """
        del findings  # Reserved for future per-finding logic
        anomalies: list[AnomalySignal] = []

        for entity in self._causal_entities.values():
            sources = list(entity.source_findings)

            source_domains = set()
            for source in sources:
                if any(kw in source.lower() for kw in ["dark", "tor", "i2p"]):
                    source_domains.add("dark_web")
                elif any(kw in source.lower() for kw in ["paste", "bin"]):
                    source_domains.add("paste")
                elif any(kw in source.lower() for kw in ["cert", "ct", "transparency"]):
                    source_domains.add("cert_log")
                elif any(kw in source.lower() for kw in ["github", "gitlab"]):
                    source_domains.add("code_repo")
                else:
                    source_domains.add("other")

            if len(source_domains) >= 3:
                anomalies.append(AnomalySignal(
                    anomaly_type="cross_domain",
                    entities=(entity.entity_id,),
                    expected_sources=(),
                    actual_sources=tuple(source_domains),
                    score=min(1.0, len(source_domains) / 5.0),
                    description=f"Entity {entity.value} found across "
                                f"{len(source_domains)} different source domains",
                ))

        self._anomaly_signals = anomalies
        logger.info(f"CausalReasoner: detected {len(anomalies)} anomalies")
        return anomalies

    # -------------------------------------------------------------------------
    # Causal hypothesis generation (orchestrator within reasoner)
    # -------------------------------------------------------------------------

    def generate_hypotheses(
        self,
        findings: list[Any],
        max_hypotheses: int = MAX_CAUSAL_HYPOTHESES,
    ) -> list[CausalHypothesis]:
        """
        Generate causal hypotheses from entity relationships.

        Pipeline (coordinated by this method, all sub-operations live on
        this class):
        1. ``extract_entities`` — populate causal entity storage
        2. ``build_temporal_sequences`` — temporal ordering
        3. ``compute_co_occurrence_matrix`` — pair-wise co-occurrence
        4. Build unique entity-pair relationships
        5. Score each pair with :meth:`_calculate_confidence`
        6. Render human-readable statement via :meth:`_generate_statement`

        Args:
            findings: List of findings to reason over.
            max_hypotheses: Maximum number of hypotheses to return.

        Returns:
            List of CausalHypothesis objects, sorted by confidence desc.
        """
        # Step 1-3: pipeline fills internal storage
        self.extract_entities(findings)
        self.build_temporal_sequences()
        self.compute_co_occurrence_matrix()

        # Step 4: collect unique relationships
        relationships: list[tuple[str, str, float]] = []

        if self._co_occurrence_matrix is not None:
            n = self._co_occurrence_matrix.shape[0]
            for i in range(n):
                for j in range(i + 1, n):
                    score = float(self._co_occurrence_matrix[i, j])
                    if score >= 2:
                        e1 = self._idx_to_entity_id.get(i, "")
                        e2 = self._idx_to_entity_id.get(j, "")
                        if e1 and e2:
                            relationships.append((e1, e2, score))

        for seq in self._temporal_sequences:
            for ent_a, ent_b in zip(seq.entities, seq.entities[1:]):
                relationships.append((ent_a, ent_b, 1.0))

        # Deduplicate (ordered pair -> unordered)
        seen_pairs: set[tuple[str, str]] = set()
        unique_relationships = []
        for e1, e2, score in relationships:
            pair = (e1, e2) if e1 < e2 else (e2, e1)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                unique_relationships.append((e1, e2, score))

        # Step 5-6: build hypotheses
        hypotheses: list[CausalHypothesis] = []
        for e1, e2, co_score in unique_relationships[:max_hypotheses]:
            entity1 = self._causal_entities.get(e1)
            entity2 = self._causal_entities.get(e2)

            if entity1 is None or entity2 is None:
                continue

            source_count = len(set(entity1.source_findings + entity2.source_findings))
            source_diversity = len(self._source_types)
            temporal_consistent = any(
                e1 in s.entities and e2 in s.entities for s in self._temporal_sequences
            )

            confidence = self._calculate_confidence(
                source_count=source_count,
                source_diversity=source_diversity,
                co_occurrence_score=co_score,
                temporal_consistent=temporal_consistent,
            )

            statement = self._generate_statement(entity1, entity2, confidence)

            hypotheses.append(CausalHypothesis(
                hypothesis_id=f"hyp_{len(hypotheses)}",
                source_entity=e1,
                target_entity=e2,
                hypothesis_type="causal" if temporal_consistent else "correlative",
                statement=statement,
                confidence=confidence,
                source_count=source_count,
                source_diversity=source_diversity,
                temporal_consistent=temporal_consistent,
                supporting_findings=entity1.source_findings + entity2.source_findings,
            ))

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        logger.info(f"CausalReasoner: generated {len(hypotheses)} causal hypotheses")
        return hypotheses[:max_hypotheses]

    @staticmethod
    def _calculate_confidence(
        source_count: int,
        source_diversity: int,
        co_occurrence_score: float,
        temporal_consistent: bool,
    ) -> float:
        """Calculate hypothesis confidence score (composite 4-factor)."""
        source_factor = min(1.0, source_count / 10.0)
        diversity_factor = min(1.0, source_diversity / 5.0)
        co_occurrence_factor = min(1.0, co_occurrence_score / 5.0)
        temporal_factor = 1.0 if temporal_consistent else 0.3

        confidence = (
            0.25 * source_factor +
            0.25 * diversity_factor +
            0.25 * co_occurrence_factor +
            0.25 * temporal_factor
        )
        return round(confidence, 3)

    @staticmethod
    def _generate_statement(
        entity1: CausalEntity,
        entity2: CausalEntity,
        confidence: float,
    ) -> str:
        """Generate human-readable causal hypothesis statement."""
        if entity1.entity_type == entity2.entity_type:
            return (
                f"Entities of type '{entity1.entity_type}' at values '{entity1.value}' "
                f"and '{entity2.value}' appear together with confidence {confidence:.1%}"
            )
        return (
            f"Entity '{entity1.value}' ({entity1.entity_type}) is associated with "
            f"'{entity2.value}' ({entity2.entity_type}) with confidence {confidence:.1%}"
        )

    # -------------------------------------------------------------------------
    # Read-side accessors (for engine facade to expose state)
    # -------------------------------------------------------------------------

    @property
    def source_types(self) -> set[str]:
        """Read-only access to observed source_type set."""
        return set(self._source_types)

    @property
    def entity_count(self) -> int:
        """Number of stored causal entities."""
        return len(self._causal_entities)

    @property
    def sequence_count(self) -> int:
        """Number of stored temporal sequences."""
        return len(self._temporal_sequences)
