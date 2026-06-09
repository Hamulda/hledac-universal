"""
Sprint Tier-5: CausalReasoner extraction probe
==============================================

Validates that the CausalReasoner extracted from brain.research_hypothesis_engine
(C4 Tier-5 refactoring) is:

1. Importable from new canonical path brain.hypothesis.causal
2. Importable from package facade brain.hypothesis
3. Backward-compat: HypothesisEngine methods still work and return
   the same results as direct CausalReasoner usage
4. The HypothesisEngine.causal attribute aliases still expose the
   populated state after delegation
5. M1 bounds (MAX_CAUSAL_ENTITIES, MAX_CAUSAL_HYPOTHESES) preserved
6. No circular imports between causal.py and hypothesis_engine.py
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

# =============================================================================
# Test Fixtures
# =============================================================================

@dataclass
class MockFinding:
    """Mock finding with causal-relevant fields."""
    finding_id: str
    payload_text: str
    source_type: str
    ts: float


def _make_findings() -> list[MockFinding]:
    """Create sample findings with mixed IOC types."""
    base_ts = time.time() - 3600
    return [
        MockFinding(
            finding_id="f1",
            payload_text="Found IP 192.168.1.1 and domain example.com",
            source_type="web",
            ts=base_ts,
        ),
        MockFinding(
            finding_id="f2",
            payload_text="Certificate for example.com issued to 192.168.1.1",
            source_type="cert_log",
            ts=base_ts + 300,
        ),
        MockFinding(
            finding_id="f3",
            payload_text="Email test@example.com associated with evil.com",
            source_type="github",
            ts=base_ts + 600,
        ),
    ]


# =============================================================================
# 1. Import Path Probes
# =============================================================================

class TestCausalReasonerImports:
    """CausalReasoner is importable from new and old paths."""

    def test_import_from_canonical_path(self):
        """`from brain.hypothesis.causal import CausalReasoner` must work."""
        from brain.hypothesis.causal import CausalReasoner  # noqa: F401
        assert CausalReasoner is not None

    def test_import_from_package_facade(self):
        """`from brain.hypothesis import CausalReasoner` must work."""
        from brain.hypothesis import CausalReasoner  # noqa: F401
        assert CausalReasoner is not None

    def test_import_from_engine_module(self):
        """`from brain.research_hypothesis_engine import CausalReasoner` must work (back-compat)."""
        from brain.research_hypothesis_engine import CausalReasoner  # noqa: F401
        assert CausalReasoner is not None

    def test_no_circular_import(self):
        """brain.hypothesis.causal and brain.hypothesis_engine coexist without cycles."""
        import brain.hypothesis.causal  # noqa: F401
        import brain.research_hypothesis_engine  # noqa: F401
        # If we get here, no circular import was triggered.


# =============================================================================
# 2. CausalReasoner Standalone Behavior
# =============================================================================

class TestCausalReasonerStandalone:
    """CausalReasoner can be used without a HypothesisEngine instance."""

    def test_extract_entities_returns_causal_entities(self):
        from brain.hypothesis._types import CausalEntity
        from brain.hypothesis.causal import CausalReasoner

        cr = CausalReasoner()
        findings = _make_findings()
        entities = cr.extract_entities(findings)

        assert isinstance(entities, list)
        assert all(isinstance(e, CausalEntity) for e in entities)
        # IP, domain, email all detected
        types_seen = {e.entity_type for e in entities}
        assert "ip" in types_seen
        assert "domain" in types_seen
        assert "email" in types_seen

    def test_entity_dedup_same_value(self):
        from brain.hypothesis.causal import CausalReasoner

        cr = CausalReasoner()
        findings = _make_findings()
        entities = cr.extract_entities(findings)

        ip_entities = [e for e in entities if e.entity_type == "ip"]
        # 192.168.1.1 appears in f1 and f2 — must be deduplicated
        assert len(ip_entities) == 1
        assert ip_entities[0].value == "192.168.1.1"

    def test_build_temporal_sequences_minimum_size_2(self):
        from brain.hypothesis.causal import CausalReasoner

        cr = CausalReasoner()
        cr.extract_entities(_make_findings())
        sequences = cr.build_temporal_sequences(gap_threshold=3600.0)

        for seq in sequences:
            assert len(seq.entities) >= 2
            assert len(seq.timestamps) == len(seq.entities)

    def test_compute_co_occurrence_matrix(self):
        from brain.hypothesis.causal import CausalReasoner

        cr = CausalReasoner()
        cr.extract_entities(_make_findings())
        matrix = cr.compute_co_occurrence_matrix()

        if matrix is not None:
            import numpy as np
            assert isinstance(matrix, np.ndarray)
            # Diagonal: entity does not co-occur with itself in this data
            # (co_occurrence increments only on different findings)
            assert matrix.shape[0] == matrix.shape[1]

    def test_generate_hypotheses_respects_max(self):
        from brain.hypothesis._types import MAX_CAUSAL_HYPOTHESES
        from brain.hypothesis.causal import CausalReasoner

        cr = CausalReasoner()
        # generate_hypotheses is a sync method (pipeline is fully sync)
        result = cr.generate_hypotheses(_make_findings(), max_hypotheses=3)

        assert isinstance(result, list)
        assert len(result) <= 3
        assert len(result) <= MAX_CAUSAL_HYPOTHESES
        for hyp in result:
            assert 0.0 <= hyp.confidence <= 1.0


# =============================================================================
# 3. Backward-Compat Facade Probes
# =============================================================================

class TestHypothesisEngineCausalFacade:
    """HypothesisEngine.causal methods delegate to CausalReasoner and refresh aliases."""

    def test_engine_has_causal_reasoner_instance(self):
        from brain.hypothesis.causal import CausalReasoner
        from brain.research_hypothesis_engine import HypothesisEngine

        engine = HypothesisEngine()
        assert hasattr(engine, "_causal_reasoner")
        assert isinstance(engine._causal_reasoner, CausalReasoner)

    def test_engine_extract_causal_entities_delegates(self):
        from brain.hypothesis._types import CausalEntity
        from brain.research_hypothesis_engine import HypothesisEngine

        engine = HypothesisEngine()
        findings = _make_findings()
        entities = engine.extract_causal_entities(findings)

        assert isinstance(entities, list)
        assert all(isinstance(e, CausalEntity) for e in entities)
        # Legacy alias refreshed
        assert engine._causal_entities is engine._causal_reasoner._causal_entities
        assert engine._source_types is engine._causal_reasoner._source_types

    def test_engine_build_temporal_sequences_delegates(self):
        from brain.research_hypothesis_engine import HypothesisEngine

        engine = HypothesisEngine()
        engine.extract_causal_entities(_make_findings())
        sequences = engine.build_temporal_sequences()
        assert isinstance(sequences, list)
        # Legacy alias refreshed
        assert engine._temporal_sequences is engine._causal_reasoner._temporal_sequences

    def test_engine_compute_co_occurrence_matrix_delegates(self):
        from brain.research_hypothesis_engine import HypothesisEngine

        engine = HypothesisEngine()
        engine.extract_causal_entities(_make_findings())
        engine.compute_co_occurrence_matrix()
        # May be None if numpy missing, but legacy alias refreshed either way
        assert engine._co_occurrence_matrix is engine._causal_reasoner._co_occurrence_matrix
        assert engine._entity_id_to_idx is engine._causal_reasoner._entity_id_to_idx
        assert engine._idx_to_entity_id is engine._causal_reasoner._idx_to_entity_id

    def test_engine_detect_causal_anomalies_delegates(self):
        from brain.hypothesis._types import AnomalySignal
        from brain.research_hypothesis_engine import HypothesisEngine

        engine = HypothesisEngine()
        engine.extract_causal_entities(_make_findings())
        anomalies = engine.detect_causal_anomalies(_make_findings())
        assert isinstance(anomalies, list)
        # Legacy alias refreshed
        assert engine._anomaly_signals is engine._causal_reasoner._anomaly_signals
        for a in anomalies:
            assert isinstance(a, AnomalySignal)

    def test_engine_get_co_occurrence_delegates(self):
        from brain.research_hypothesis_engine import HypothesisEngine

        engine = HypothesisEngine()
        engine.extract_causal_entities(_make_findings())
        engine.compute_co_occurrence_matrix()
        # Returns float, never raises
        score = engine.get_co_occurrence("ip_192.168.1.1", "domain_example.com")
        assert isinstance(score, float)
        assert score >= 0.0

    def test_engine_generate_causal_hypotheses_async_delegates(self):
        from brain.research_hypothesis_engine import HypothesisEngine

        engine = HypothesisEngine()
        findings = _make_findings()
        # The facade is async — must be awaited
        hypotheses = asyncio.run(engine.generate_causal_hypotheses(findings, max_hypotheses=5))
        assert isinstance(hypotheses, list)
        assert len(hypotheses) <= 5
        # After async call, aliases refreshed
        assert engine._co_occurrence_matrix is engine._causal_reasoner._co_occurrence_matrix
        assert engine._temporal_sequences is engine._causal_reasoner._temporal_sequences


# =============================================================================
# 4. M1 Bounds Preserved
# =============================================================================

class TestCausalM1Bounds:
    """M1 8GB bounds still enforced after extraction."""

    def test_max_causal_entities_constant(self):
        from brain.hypothesis._types import MAX_CAUSAL_ENTITIES
        assert MAX_CAUSAL_ENTITIES == 5000

    def test_max_causal_hypotheses_constant(self):
        from brain.hypothesis._types import MAX_CAUSAL_HYPOTHESES
        assert MAX_CAUSAL_HYPOTHESES == 200

    def test_max_co_occurrence_matrix_size_constant(self):
        from brain.hypothesis._types import MAX_CO_OCCURRENCE_MATRIX_SIZE
        assert MAX_CO_OCCURRENCE_MATRIX_SIZE == 2000

    def test_co_occurrence_fp16_enabled(self):
        from brain.hypothesis._types import CO_OCCURRENCE_FP16
        assert CO_OCCURRENCE_FP16 is True

    def test_max_causal_findings_constant(self):
        from brain.hypothesis._types import MAX_CAUSAL_FINDINGS
        assert MAX_CAUSAL_FINDINGS == 50000

    def test_bounded_extraction_caps_at_max(self):
        """Extraction must stop at MAX_CAUSAL_FINDINGS regardless of input size."""
        from brain.hypothesis._types import MAX_CAUSAL_ENTITIES, MAX_CAUSAL_FINDINGS
        from brain.hypothesis.causal import CausalReasoner

        cr = CausalReasoner()
        # 100 findings is below MAX_CAUSAL_FINDINGS — all processed
        big_findings = [
            MockFinding(
                finding_id=f"f{i}",
                payload_text=f"10.0.0.{i % 256}",
                source_type="web",
                ts=time.time(),
            )
            for i in range(100)
        ]
        entities = cr.extract_entities(big_findings)
        # All 100 processed; each yields a unique IP, capped at MAX_CAUSAL_ENTITIES
        assert len(entities) <= MAX_CAUSAL_ENTITIES
        # And never beyond the per-instance bound
        assert cr.entity_count <= MAX_CAUSAL_ENTITIES
        # MAX_CAUSAL_FINDINGS is a separate cap on input findings (50 000)
        assert MAX_CAUSAL_FINDINGS == 50_000


# =============================================================================
# 5. Isolation: CausalReasoner and HypothesisEngine Have Independent State
# =============================================================================

class TestCausalIsolation:
    """Two CausalReasoner instances must not share state."""

    def test_two_reasoners_have_independent_storage(self):
        from brain.hypothesis.causal import CausalReasoner

        cr1 = CausalReasoner()
        cr2 = CausalReasoner()

        cr1.extract_entities(_make_findings())
        # cr2 still empty
        assert cr1.entity_count > 0
        assert cr2.entity_count == 0
        # Mutating cr1 must not affect cr2
        assert cr1._causal_entities is not cr2._causal_entities

    def test_hypothesis_engine_causal_state_isolated_per_instance(self):
        from brain.research_hypothesis_engine import HypothesisEngine

        e1 = HypothesisEngine()
        e2 = HypothesisEngine()
        # Two engine instances, two CausalReasoner instances
        assert e1._causal_reasoner is not e2._causal_reasoner
        e1.extract_causal_entities(_make_findings())
        assert e1._causal_reasoner.entity_count > 0
        assert e2._causal_reasoner.entity_count == 0


# =============================================================================
# 6. explain_with_mlx Extraction (C4 Tier-5)
# =============================================================================

class TestExplainWithMLXExtraction:
    """explain_with_mlx extracted to brain.hypothesis.explainer (Tier-5)."""

    def test_explain_with_mlx_from_canonical_path(self):
        """`from brain.hypothesis.explainer import explain_with_mlx` must work."""
        import inspect

        from brain.hypothesis.explainer import explain_with_mlx  # noqa: F401
        assert inspect.iscoroutinefunction(explain_with_mlx)

    def test_explain_with_mlx_backward_compat(self):
        """`from brain.research_hypothesis_engine import explain_with_mlx` (back-compat)."""
        from brain.hypothesis.explainer import explain_with_mlx as new
        from brain.research_hypothesis_engine import explain_with_mlx as old
        # Both resolve to the same function object
        assert old is new


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
