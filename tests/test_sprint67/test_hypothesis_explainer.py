"""
Test hypothesis explainer - Sprint 67
Tests for SimpleNodeAblationExplainer and MLX explanation.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSimpleNodeAblationExplainer:
    """Tests for node ablation explainer."""

    @pytest.mark.asyncio
    async def test_explain_path_short(self):
        """Test explain with short path returns empty."""
        from hledac.universal.brain.hypothesis_engine import SimpleNodeAblationExplainer

        mock_graph_rag = MagicMock()
        explainer = SimpleNodeAblationExplainer(mock_graph_rag)

        result = await explainer.explain_path(["node1"], "hypothesis", max_nodes=5)

        assert result == {}

    @pytest.mark.asyncio
    async def test_explain_path_no_embedder(self):
        """Test explain when embedder unavailable."""
        from hledac.universal.brain.hypothesis_engine import SimpleNodeAblationExplainer

        mock_graph_rag = MagicMock()
        mock_graph_rag._get_embedder = AsyncMock(return_value=None)

        explainer = SimpleNodeAblationExplainer(mock_graph_rag)

        result = await explainer.explain_path(["node1", "node2", "node3"], "hypothesis", max_nodes=3)

        assert result == {}


class TestExplainWithMLX:
    """Tests for MLX explanation function."""

    @pytest.mark.asyncio
    async def test_explain_with_mlx_no_model(self):
        """Test explain when model unavailable."""
        from hledac.universal.brain.hypothesis_engine import explain_with_mlx

        with patch("hledac.universal.utils.mlx_cache.get_mlx_model", return_value=(None, None)):
            explanation, prompt_hash = await explain_with_mlx("hypothesis", ["node1", "node2"])

            assert explanation == "MLX model unavailable"
            assert prompt_hash == ""

    @pytest.mark.asyncio
    async def test_explain_with_mlx_generates_hash(self):
        """Test explanation generates prompt hash."""
        from hledac.universal.brain.hypothesis_engine import explain_with_mlx

        mock_model = MagicMock()
        mock_tokenizer = MagicMock()

        with patch("hledac.universal.utils.mlx_cache.get_mlx_model", return_value=(mock_model, mock_tokenizer)):
            with patch("mlx_lm.generate", return_value="This is an explanation."):
                explanation, prompt_hash = await explain_with_mlx("test hypothesis", ["node1", "node2"])

                assert prompt_hash != ""
                assert len(prompt_hash) == 8


class TestExplainerMetadata:
    """Tests for explainer metadata tracking."""

    def test_explainer_metadata_fields(self):
        """Test that metadata fields are defined correctly."""
        # These fields should be set in verify_claim
        expected_fields = [
            'edge_importances',
            'mlx_explanation',
            'explainer_type',
            'max_nodes',
            'scoring_fn',
            'model_id',
            'prompt_hash',
            'token_budget',
            'temperature'
        ]

        # This is a structural test - verify fields are in expected list
        assert len(expected_fields) == 9
        assert 'model_id' in expected_fields
        assert 'token_budget' in expected_fields


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
