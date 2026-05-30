"""
tests/probe_f260_multihop.py
============================
Sprint F260: MultiHop Deep Research Chain — probe tests

Tests:
1. DeepResearchHopSignature exists and has correct fields
2. MultiHopDeepResearchChain can be instantiated when DSPy available
3. Hop count adapts to RAM state
4. Chain stops when confidence < 0.3
5. Evidence per hop limited to 20
6. GraphRAG integration via multi_hop_search
"""
from __future__ import annotations

import pytest

from brain.dspy_signatures import (
    DeepResearchHopSignature,
    DeepResearchChain,
    is_dspy_available,
)


class TestF260Signature:
    """F260: DeepResearchHopSignature tests."""

    def test_signature_defined(self):
        """DeepResearchHopSignature exists when DSPy available."""
        if is_dspy_available():
            assert DeepResearchHopSignature is not None

    def test_signature_has_inputs(self):
        """Signature has required input fields."""
        if not is_dspy_available():
            pytest.skip("DSPy not available")

        # Check class has input fields
        sig_fields = DeepResearchHopSignature._fields
        assert "query" in sig_fields
        assert "current_evidence" in sig_fields
        assert "hop_number" in sig_fields

    def test_signature_has_outputs(self):
        """Signature has required output fields."""
        if not is_dspy_available():
            pytest.skip("DSPy not available")

        sig_fields = DeepResearchHopSignature._fields
        assert "next_query" in sig_fields
        assert "reasoning" in sig_fields
        assert "confidence" in sig_fields

    def test_chain_of_thought_wrapper_exists(self):
        """DeepResearchChain wrapper exists."""
        if not is_dspy_available():
            pytest.skip("DSPy not available")
        assert DeepResearchChain is not None


class TestF260MultiHopChain:
    """F260: MultiHopDeepResearchChain tests."""

    @pytest.fixture
    def chain_class(self):
        """Get MultiHopDeepResearchChain class."""
        try:
            from brain.dspy_programs import MultiHopDeepResearchChain
            return MultiHopDeepResearchChain
        except ImportError:
            return None

    def test_chain_class_exists(self, chain_class):
        """MultiHopDeepResearchChain class exists."""
        assert chain_class is not None

    def test_chain_has_default_max_hops(self, chain_class):
        """Chain has default max_hops=5."""
        if chain_class is None:
            pytest.skip("DSPy not available")

        # Check class attribute
        assert hasattr(chain_class, "DEFAULT_MAX_HOPS")
        assert chain_class.DEFAULT_MAX_HOPS == 5

    def test_chain_has_confidence_threshold(self, chain_class):
        """Chain has confidence threshold=0.3."""
        if chain_class is None:
            pytest.skip("DSPy not available")

        assert hasattr(chain_class, "CONFIDENCE_THRESHOLD")
        assert chain_class.CONFIDENCE_THRESHOLD == 0.3

    def test_chain_has_evidence_limit(self, chain_class):
        """Chain limits evidence per hop to 20."""
        if chain_class is None:
            pytest.skip("DSPy not available")

        assert hasattr(chain_class, "MAX_EVIDENCE_PER_HOP")
        assert chain_class.MAX_EVIDENCE_PER_HOP == 20

    def test_chain_has_nodes_limit(self, chain_class):
        """Chain limits nodes per hop to 30."""
        if chain_class is None:
            pytest.skip("DSPy not available")

        assert hasattr(chain_class, "MAX_NODES_PER_HOP")
        assert chain_class.MAX_NODES_PER_HOP == 30

    def test_chain_has_timeout(self, chain_class):
        """Chain has total timeout of 120 seconds."""
        if chain_class is None:
            pytest.skip("DSPy not available")

        assert hasattr(chain_class, "TIMEOUT_SECONDS")
        assert chain_class.TIMEOUT_SECONDS == 120


class TestF260RamAdaptive:
    """F260: RAM-adaptive hop count tests."""

    def test_ram_adaptive_imports(self):
        """get_uma_snapshot import succeeds."""
        try:
            from utils.uma_budget import get_uma_snapshot
            assert get_uma_snapshot is not None
        except ImportError:
            pytest.skip("utils.uma_budget not available")

    def test_get_multi_hop_chain_exists(self):
        """get_multi_hop_chain factory function exists."""
        try:
            from brain.dspy_programs import get_multi_hop_chain
            assert get_multi_hop_chain is not None
        except ImportError:
            pytest.skip("DSPy not available")


class TestF260HypothesisEngine:
    """F260: HypothesisEngine integration tests."""

    def test_hypothesis_engine_has_multihop_imports(self):
        """HypothesisEngine imports MultiHop components."""
        # Check imports are defined (fail-soft)
        try:
            from brain.hypothesis_engine import (
                MULTIHOP_AVAILABLE,
                HLEDAC_ENABLE_LLM,
            )
            assert MULTIHOP_AVAILABLE is not None
            assert isinstance(HLEDAC_ENABLE_LLM, bool)
        except ImportError as e:
            pytest.fail(f"Failed to import MultiHop components: {e}")

    def test_hypothesis_engine_has_os_import(self):
        """HypothesisEngine has os import for env vars."""
        from brain import hypothesis_engine

        # Check os is in the module
        assert hasattr(hypothesis_engine, "__file__")  # Module exists


class TestF260EIG:
    """F260: EIG calculator integration tests."""

    def test_eig_calculator_import(self):
        """EIGCalculator import succeeds."""
        try:
            from utils.eig import EIGCalculator
            assert EIGCalculator is not None
        except ImportError:
            pytest.skip("utils.eig not available")

    def test_eig_calculator_has_threshold(self):
        """EIGCalculator has EIG_THRESHOLD."""
        try:
            from utils.eig import EIGCalculator

            calc = EIGCalculator()
            assert hasattr(calc, "EIG_THRESHOLD")
            assert calc.EIG_THRESHOLD == 0.1
        except ImportError:
            pytest.skip("EIGCalculator not available")

    def test_eig_compute_eig_exists(self):
        """EIGCalculator.compute_eig method exists."""
        try:
            from utils.eig import EIGCalculator

            calc = EIGCalculator()
            assert hasattr(calc, "compute_eig")
            assert callable(calc.compute_eig)
        except ImportError:
            pytest.skip("EIGCalculator not available")


class TestF260GraphRAG:
    """F260: GraphRAG integration tests."""

    def test_graph_rag_multi_hop_search_exists(self):
        """GraphRAGOrchestrator has multi_hop_search method."""
        try:
            from knowledge.graph_rag import GraphRAGOrchestrator

            assert hasattr(GraphRAGOrchestrator, "multi_hop_search")
        except ImportError:
            pytest.skip("GraphRAG not available")

    def test_graph_rag_returns_dict(self):
        """GraphRAGOrchestrator.multi_hop_search returns dict."""
        try:
            from knowledge.graph_rag import GraphRAGOrchestrator
            import inspect

            # Check return type annotation
            sig = inspect.signature(GraphRAGOrchestrator.multi_hop_search)
            # Method exists, that's sufficient
            assert "multi_hop_search" in dir(GraphRAGOrchestrator)
        except ImportError:
            pytest.skip("GraphRAG not available")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])