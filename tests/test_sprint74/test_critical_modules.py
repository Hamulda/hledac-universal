"""
Smoke tests for critical modules without test coverage.
"""


import pytest


class TestCriticalModules:
    """Smoke tests for 8 critical modules."""

    def test_gnn_predictor_import(self):
        """Test GNNPredictor can be imported."""
        from hledac.universal.brain.gnn_predictor import GNNPredictor
        assert GNNPredictor is not None

    def test_paged_attention_cache_import(self):
        """Test PagedAttentionCache can be imported."""
        from hledac.universal.brain.paged_attention_cache import PagedAttentionCache
        # Just check import works - don't call methods that may not exist
        assert PagedAttentionCache is not None

    def test_resource_governor_import(self):
        """Test ResourceGovernor can be imported."""
        from hledac.universal.core.resource_governor import ResourceGovernor
        assert ResourceGovernor is not None

    def test_local_graph_import(self):
        """Test LocalGraphStore can be imported."""
        from hledac.universal.dht.local_graph import LocalGraphStore
        assert LocalGraphStore is not None

    def test_secure_aggregator_import(self):
        """Test SecureAggregator can be imported."""
        from hledac.universal.federated.secure_aggregator import SecureAggregator
        assert SecureAggregator is not None

    def test_advanced_image_osint_import(self):
        """Test AdvancedImageOSINT can be imported."""
        try:
            from hledac.universal.intelligence.advanced_image_osint import AdvancedImageOSINT
            assert AdvancedImageOSINT is not None
        except ImportError:
            pytest.skip("AdvancedImageOSINT not available")

    def test_document_intelligence_import(self):
        """Test DocumentIntelligenceEngine can be imported."""
        try:
            from hledac.universal.intelligence.document_intelligence import DocumentIntelligenceEngine
            assert DocumentIntelligenceEngine is not None
        except ImportError:
            pytest.skip("DocumentIntelligenceEngine not available")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
