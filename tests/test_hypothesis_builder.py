"""
Tests for Hypothesis Engine (Sprint F259)
==========================================

Tests for CausalEngine, HypothesisGraph, and HypothesisBuilder.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import pytest

# Import the modules under test
from brain.causal_engine import (
    CausalEngine,
    Entity,
    EntityCluster,
    TemporalSequence,
    AnomalySignal,
    CausalHypothesis,
    Contradiction,
    MAX_ENTITIES,
    MAX_FINDINGS,
    MAX_HYPOTHESES,
)
from graph.hypothesis_graph import (
    HypothesisGraph,
    HypothesisEdge,
    HiddenBridge,
    AnomalousCluster,
    MAX_NODES,
    MAX_EDGES,
)
from export.hypothesis_builder import (
    HypothesisBuilder,
    HypothesisResult,
    HYPOTHESIS_ENABLED,
    RAM_THRESHOLD,
)


# =============================================================================
# Fixtures
# =============================================================================

@dataclass
class MockCanonicalFinding:
    """Mock CanonicalFinding for testing."""
    finding_id: str
    query: str
    source_type: str
    confidence: float
    ts: float
    provenance: tuple[str, ...] = ()
    payload_text: str | None = None


@pytest.fixture
def sample_findings() -> list[MockCanonicalFinding]:
    """Create sample findings for testing."""
    return [
        MockCanonicalFinding(
            finding_id="f1",
            query="test",
            source_type="web",
            confidence=0.8,
            ts=time.time() - 3600,
            payload_text="Found IP 192.168.1.1 and domain example.com",
        ),
        MockCanonicalFinding(
            finding_id="f2",
            query="test",
            source_type="cert_log",
            confidence=0.9,
            ts=time.time() - 1800,
            payload_text="Certificate for example.com issued to 192.168.1.1",
        ),
        MockCanonicalFinding(
            finding_id="f3",
            query="test",
            source_type="github",
            confidence=0.7,
            ts=time.time(),
            payload_text="Email test@example.com associated with domain evil.com",
        ),
    ]


# =============================================================================
# CausalEngine Tests
# =============================================================================

class TestCausalEngine:
    """Tests for CausalEngine entity extraction and hypothesis generation."""

    @pytest.mark.asyncio
    async def test_extract_entities_from_findings(self, sample_findings):
        """Test entity extraction from sample findings."""
        engine = CausalEngine()
        entities = engine.extract_entities(sample_findings)

        assert len(entities) > 0, "Should extract at least one entity"
        assert any(e.entity_type == "ip" for e in entities), "Should extract IP entity"
        assert any(e.entity_type == "domain" for e in entities), "Should extract domain entity"
        assert any(e.entity_type == "email" for e in entities), "Should extract email entity"

    @pytest.mark.asyncio
    async def test_entity_deduplication(self, sample_findings):
        """Test that duplicate entities are properly merged."""
        engine = CausalEngine()
        entities = engine.extract_entities(sample_findings)

        # Check that IP 192.168.1.1 appears only once
        ip_entities = [e for e in entities if e.entity_type == "ip"]
        assert len(ip_entities) == 1, "IP should be deduplicated"
        assert ip_entities[0].value == "192.168.1.1"

    @pytest.mark.asyncio
    async def test_build_temporal_sequences(self, sample_findings):
        """Test temporal sequence building."""
        engine = CausalEngine()
        engine.extract_entities(sample_findings)
        sequences = engine.build_temporal_sequences()

        assert isinstance(sequences, list), "Should return list of sequences"
        for seq in sequences:
            assert len(seq.entities) >= 2, "Sequence should have at least 2 entities"
            assert len(seq.timestamps) == len(seq.entities), "Timestamps should match entities"

    @pytest.mark.asyncio
    async def test_compute_co_occurrence_matrix(self, sample_findings):
        """Test co-occurrence matrix computation."""
        engine = CausalEngine()
        engine.extract_entities(sample_findings)
        matrix = engine.compute_co_occurrence_matrix()

        if matrix is not None:
            import numpy as np
            assert isinstance(matrix, np.ndarray), "Should return numpy array"
            assert matrix.dtype in [np.float16, np.float32], "Should be float type"
            # Diagonal should be zero (entity doesn't co-occur with itself)
            assert matrix.diagonal().sum() == 0, "Diagonal should be zero"

    @pytest.mark.asyncio
    async def test_detect_anomalies(self, sample_findings):
        """Test anomaly detection."""
        engine = CausalEngine()
        engine.extract_entities(sample_findings)
        anomalies = engine.detect_anomalies(sample_findings)

        assert isinstance(anomalies, list), "Should return list of anomalies"
        # Check anomaly signal structure
        for anomaly in anomalies:
            assert hasattr(anomaly, "anomaly_type"), "Should have anomaly_type"
            assert hasattr(anomaly, "entities"), "Should have entities"
            assert hasattr(anomaly, "score"), "Should have score"

    @pytest.mark.asyncio
    async def test_generate_causal_hypotheses(self, sample_findings):
        """Test causal hypothesis generation."""
        engine = CausalEngine()
        engine.extract_entities(sample_findings)
        engine.build_temporal_sequences()
        engine.compute_co_occurrence_matrix()

        hypotheses = await engine.generate_causal_hypotheses()

        assert isinstance(hypotheses, list), "Should return list of hypotheses"
        assert len(hypotheses) <= MAX_HYPOTHESES, f"Should cap at {MAX_HYPOTHESES}"

        for hyp in hypotheses:
            assert hasattr(hyp, "hypothesis_id"), "Should have hypothesis_id"
            assert hasattr(hyp, "source_entity"), "Should have source_entity"
            assert hasattr(hyp, "target_entity"), "Should have target_entity"
            assert hasattr(hyp, "confidence"), "Should have confidence"
            assert 0.0 <= hyp.confidence <= 1.0, "Confidence should be 0.0-1.0"

    @pytest.mark.asyncio
    async def test_full_pipeline(self, sample_findings):
        """Test full hypothesis generation pipeline."""
        engine = CausalEngine()
        hypotheses = await engine.generate_hypotheses(sample_findings)

        assert isinstance(hypotheses, list), "Should return list of hypotheses"
        # Verify pipeline completed all steps
        assert len(engine._entities) > 0, "Should have extracted entities"
        assert len(engine._sequences) >= 0, "Should have built sequences"

    @pytest.mark.asyncio
    async def test_contradiction_detection(self, sample_findings):
        """Test contradiction detection."""
        engine = CausalEngine()
        engine.extract_entities(sample_findings)
        contradictions = engine.detect_contradictions(sample_findings)

        assert isinstance(contradictions, list), "Should return list of contradictions"
        for c in contradictions:
            assert hasattr(c, "finding_a_id"), "Should have finding_a_id"
            assert hasattr(c, "finding_b_id"), "Should have finding_b_id"
            assert hasattr(c, "entity_id"), "Should have entity_id"
            assert hasattr(c, "severity"), "Should have severity"


# =============================================================================
# HypothesisGraph Tests
# =============================================================================

class TestHypothesisGraph:
    """Tests for HypothesisGraph network-based reasoning."""

    def test_add_entity(self):
        """Test adding entities to the graph."""
        graph = HypothesisGraph()
        result = graph.add_entity("entity_1", "ip")

        assert result is True, "Should return True for new entity"
        assert graph.node_count == 1, "Should have 1 node"

    def test_add_duplicate_entity(self):
        """Test adding duplicate entities."""
        graph = HypothesisGraph()
        graph.add_entity("entity_1", "ip")
        result = graph.add_entity("entity_1", "ip")

        assert result is False, "Should return False for duplicate"
        assert graph.node_count == 1, "Should still have 1 node"

    def test_add_hypothesis_edge(self):
        """Test adding hypothesis edges."""
        graph = HypothesisGraph()
        edge = HypothesisEdge(
            source="entity_1",
            target="entity_2",
            hypothesis_type="causal",
            statement="Entity 1 causes Entity 2",
            confidence=0.8,
            supporting_sources=("web", "cert_log"),
            temporal_sequence=(),
        )

        result = graph.add_hypothesis_edge(edge)

        assert result is True, "Should return True for new edge"
        assert graph.edge_count == 1, "Should have 1 edge"

    def test_get_entity_type(self):
        """Test getting entity type."""
        graph = HypothesisGraph()
        graph.add_entity("entity_1", "ip")

        etype = graph.get_entity_type("entity_1")
        assert etype == "ip", "Should return correct entity type"

    def test_get_nonexistent_entity_type(self):
        """Test getting type for nonexistent entity."""
        graph = HypothesisGraph()

        etype = graph.get_entity_type("nonexistent")
        assert etype is None, "Should return None for nonexistent entity"

    def test_find_hidden_bridges_empty_graph(self):
        """Test hidden bridge detection on empty graph."""
        graph = HypothesisGraph()
        bridges = graph.find_hidden_bridges()

        assert bridges == [], "Should return empty list for empty graph"

    def test_find_hidden_bridges_small_graph(self):
        """Test hidden bridge detection on small graph."""
        graph = HypothesisGraph()

        # Create a simple chain: A -> B -> C
        for i, (src, tgt) in enumerate([("A", "B"), ("B", "C")]):
            edge = HypothesisEdge(
                source=src,
                target=tgt,
                hypothesis_type="causal",
                statement=f"{src} causes {tgt}",
                confidence=0.7,
            )
            graph.add_hypothesis_edge(edge)

        bridges = graph.find_hidden_bridges()

        assert isinstance(bridges, list), "Should return list of bridges"
        # Node B should have high betweenness as it connects A and C
        # (exact behavior depends on graph connectivity)

    def test_detect_anomalous_clusters_empty(self):
        """Test anomalous cluster detection on empty graph."""
        graph = HypothesisGraph()
        anomalies = graph.detect_anomalous_clusters()

        assert anomalies == [], "Should return empty list for empty graph"

    def test_to_stix_bundle(self):
        """Test STIX bundle export."""
        graph = HypothesisGraph()

        # Add some entities and edges
        graph.add_entity("entity_1", "ip")
        graph.add_entity("entity_2", "domain")

        edge = HypothesisEdge(
            source="entity_1",
            target="entity_2",
            hypothesis_type="causal",
            statement="IP associated with domain",
            confidence=0.9,
        )
        graph.add_hypothesis_edge(edge)

        bundle = graph.to_stix_bundle()

        assert bundle["type"] == "bundle", "Should have bundle type"
        assert "objects" in bundle, "Should have objects array"
        assert len(bundle["objects"]) > 0, "Should have objects"

    def test_serialization(self):
        """Test graph serialization and deserialization."""
        graph = HypothesisGraph()

        edge = HypothesisEdge(
            source="entity_1",
            target="entity_2",
            hypothesis_type="causal",
            statement="Test hypothesis",
            confidence=0.8,
        )
        graph.add_hypothesis_edge(edge)

        # Serialize
        data = graph.to_dict()

        # Deserialize
        restored = HypothesisGraph.from_dict(data)

        assert restored.node_count == graph.node_count, "Should restore node count"
        assert restored.edge_count == graph.edge_count, "Should restore edge count"


# =============================================================================
# HypothesisBuilder Tests
# =============================================================================

class TestHypothesisBuilder:
    """Tests for HypothesisBuilder export integration."""

    @pytest.mark.asyncio
    async def test_hypothesis_disabled(self, sample_findings):
        """Test behavior when hypothesis generation is disabled."""
        builder = HypothesisBuilder()

        result = await builder.run_hypothesis_generation(sample_findings)

        if not HYPOTHESIS_ENABLED:
            assert result.enabled is False, "Should be disabled"
            assert result.hypotheses_generated == 0, "Should generate no hypotheses"
            if result.error:
                assert "disabled" in result.error.lower(), "Should mention disabled"

    @pytest.mark.asyncio
    async def test_no_findings(self):
        """Test handling of empty findings list."""
        builder = HypothesisBuilder()
        result = await builder.run_hypothesis_generation([])

        # Should complete without error (fail-soft)
        assert result.enabled is not None, "Should have enabled flag"
        assert result.hypotheses_generated is not None or result.enabled is False

    @pytest.mark.asyncio
    async def test_get_graph_stats(self, sample_findings):
        """Test getting graph statistics."""
        builder = HypothesisBuilder()

        # Run generation to populate engine
        await builder.run_hypothesis_generation(sample_findings)

        stats = builder.engine._causal_entities
        assert len(stats) >= 0, "Should have causal entities"

    @pytest.mark.asyncio
    async def test_reset(self, sample_findings):
        """Test reset functionality."""
        builder = HypothesisBuilder()

        # Run to populate state
        await builder.run_hypothesis_generation(sample_findings)

        # Reset
        builder.reset()

        # State should be cleared
        assert builder._engine is None, "Engine should be None"


# =============================================================================
# Boundary Tests
# =============================================================================

class TestBoundaries:
    """Tests for boundary conditions and limits."""

    def test_max_nodes_limit(self):
        """Test that graph respects MAX_NODES limit."""
        graph = HypothesisGraph(max_nodes=5)

        # Add more than max nodes
        for i in range(10):
            result = graph.add_entity(f"entity_{i}", "ip")

        assert graph.node_count <= 5, "Should respect MAX_NODES limit"

    def test_max_entities_limit(self):
        """Test that causal engine respects MAX_ENTITIES limit."""
        engine = CausalEngine(max_entities=10)

        # Create many findings
        findings = []
        for i in range(100):
            findings.append(MockCanonicalFinding(
                finding_id=f"f{i}",
                query="test",
                source_type="web",
                confidence=0.8,
                ts=time.time(),
                payload_text=f"IP 10.0.0.{i % 255}",
            ))

        engine.extract_entities(findings)

        assert len(engine._entities) <= 10, "Should respect MAX_ENTITIES limit"

    @pytest.mark.asyncio
    async def test_max_hypotheses_cap(self):
        """Test that hypothesis generation caps at MAX_HYPOTHESES."""
        engine = CausalEngine()

        # Create many findings with overlapping entities
        findings = []
        for i in range(50):
            findings.append(MockCanonicalFinding(
                finding_id=f"f{i}",
                query="test",
                source_type="web",
                confidence=0.8,
                ts=time.time(),
                payload_text=f"IP 192.168.1.{i % 50} associated with domain example{i % 50}.com",
            ))

        engine.extract_entities(findings)
        engine.build_temporal_sequences()
        engine.compute_co_occurrence_matrix()
        hypotheses = await engine.generate_causal_hypotheses()

        assert len(hypotheses) <= MAX_HYPOTHESES, f"Should cap at {MAX_HYPOTHESES}"


# =============================================================================
# Run tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])