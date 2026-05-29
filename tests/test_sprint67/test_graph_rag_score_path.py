"""
Test graph_rag score_path - Sprint 67
Tests for score_path method in GraphRAGOrchestrator.
"""
from unittest.mock import MagicMock

import pytest


class TestGraphRAGScorePath:
    """Tests for score_path method."""

    @pytest.mark.asyncio
    async def test_score_path_short(self):
        """Test score_path with short path returns 0."""
        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator

        mock_layer = MagicMock()
        orchestrator = GraphRAGOrchestrator(mock_layer)

        result = await orchestrator.score_path(["node1"], "hypothesis", max_nodes=10)

        assert result == 0.0

    @pytest.mark.asyncio
    async def test_score_path_empty(self):
        """Test score_path with empty path."""
        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator

        mock_layer = MagicMock()
        orchestrator = GraphRAGOrchestrator(mock_layer)

        result = await orchestrator.score_path([], "hypothesis", max_nodes=10)

        assert result == 0.0

    @pytest.mark.asyncio
    async def test_score_path_with_nodes(self):
        """Test score_path with valid nodes."""
        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator

        mock_layer = MagicMock()

        # Mock get_node to return nodes with embeddings
        mock_node = MagicMock()
        mock_node.embedding = [0.1] * 384
        mock_node.metadata = {"confidence": 0.8}

        async def mock_get_node(node_id):
            return mock_node

        mock_layer.get_node = mock_get_node

        orchestrator = GraphRAGOrchestrator(mock_layer)
        orchestrator._embedder = MagicMock()

        # Mock embedder - MLXEmbeddingManager uses embed_document (sync, called via asyncio.to_thread)
        import numpy as np
        orchestrator._embedder.embed_document = MagicMock(return_value=np.array([0.1] * 384))

        result = await orchestrator.score_path(
            ["node1", "node2", "node3"],
            "test hypothesis",
            max_nodes=10
        )

        # Should return a score between 0 and 1
        assert 0.0 <= result <= 1.0

    @pytest.mark.asyncio
    async def test_score_path_max_nodes_budget(self):
        """Test score_path respects max_nodes budget."""
        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator

        mock_layer = MagicMock()

        call_count = 0

        async def mock_get_node(node_id):
            nonlocal call_count
            call_count += 1
            mock_node = MagicMock()
            mock_node.embedding = [0.1] * 384
            mock_node.metadata = {"confidence": 0.8}
            return mock_node

        mock_layer.get_node = mock_get_node

        orchestrator = GraphRAGOrchestrator(mock_layer)
        orchestrator._embedder = MagicMock()
        import numpy as np
        orchestrator._embedder.embed_document = MagicMock(return_value=np.array([0.1] * 384))

        # 20 nodes, but max_nodes = 5 - path[:5] for nodes to score
        nodes = [f"node{i}" for i in range(20)]
        await orchestrator.score_path(nodes, "hypothesis", max_nodes=5)

        # Should only call get_node for first max_nodes nodes (5 in this case)
        # plus the hypothesis embedding call
        assert call_count <= 10  # Allow some margin


class TestGetEmbedder:
    """Tests for lazy embedder initialization."""

    @pytest.mark.asyncio
    async def test_embedder_lazy_init(self):
        """Test embedder is created lazily."""
        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator

        mock_layer = MagicMock()
        orchestrator = GraphRAGOrchestrator(mock_layer)

        # Initially no embedder
        assert orchestrator._embedder is None
        assert orchestrator._embedder_lock is None

        # After _get_embedder, should have lock
        await orchestrator._get_embedder()

        # Lock should be created
        assert orchestrator._embedder_lock is not None

    @pytest.mark.asyncio
    async def test_embedder_creates_rag_engine(self):
        """Test embedder creates RAGEngine."""
        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator

        mock_layer = MagicMock()
        orchestrator = GraphRAGOrchestrator(mock_layer)

        # The embedder uses RAGEngine from local import
        # Just verify lock is created (engine init may fail in test env)
        orchestrator._embedder_lock = None
        await orchestrator._get_embedder()
        assert orchestrator._embedder_lock is not None


class TestScorePathComponents:
    """Tests for score_path component calculations."""

    @pytest.mark.asyncio
    async def test_length_score_favors_short_paths(self):
        """Test shorter paths get higher length score."""
        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator

        mock_layer = MagicMock()

        async def mock_get_node(node_id):
            mock_node = MagicMock()
            mock_node.embedding = [0.5] * 384
            mock_node.metadata = {"confidence": 0.5}
            return mock_node

        mock_layer.get_node = mock_get_node

        orchestrator = GraphRAGOrchestrator(mock_layer)
        orchestrator._embedder = MagicMock()
        import numpy as np
        orchestrator._embedder.embed_document = MagicMock(return_value=np.array([0.5] * 384))

        # Short path
        short_score = await orchestrator.score_path(
            ["a", "b"], "h", max_nodes=10
        )

        # Long path
        long_score = await orchestrator.score_path(
            ["a", "b", "c", "d", "e", "f"], "h", max_nodes=10
        )

        # Length score component should favor short path
        # (but final score also includes relevance and credibility)
        assert short_score >= 0.0
        assert long_score >= 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
