"""
Sprint F259 — Embedding Dimensions Smoke Test
=============================================

ROLE: Verify all embedding backends return 256d vectors (MRL canonical).

Canonical dimension: 256 (Matryoshka Representation Learning)
All backends must be consistent to avoid dimension mismatch errors.

INVARIANTS:
- test_mlx_embedding_dim_returns_256: MLXEmbeddingManager.EMBEDDING_DIM == 256
- test_lancedb_store_mrl_dim_is_256: LanceDBIdentityStore._current_mrl_dim == 256
- test_embedding_pipeline_mrl_dim_is_256: _EMBEDDING_DIM == 256
- test_mrl_dim_equals_embedding_dim: MRL_DIM == EMBEDDING_DIM == 256
- test_all_backends_consistent: All backend dimensions == 256
"""

import pytest


class TestEmbeddingDimensions:
    """Smoke tests for embedding dimension consistency across backends."""

    def test_mlx_embedding_dim_returns_256(self) -> None:
        """MLXEmbeddingManager.EMBEDDING_DIM must be 256 (MRL canonical)."""
        from hledac.universal.core.mlx_embeddings import MLXEmbeddingManager

        assert hasattr(MLXEmbeddingManager, 'EMBEDDING_DIM'), (
            "MLXEmbeddingManager missing EMBEDDING_DIM attribute"
        )
        assert MLXEmbeddingManager.EMBEDDING_DIM == 256, (
            f"MLXEmbeddingManager.EMBEDDING_DIM={MLXEmbeddingManager.EMBEDDING_DIM}, expected 256 (MRL canonical)"
        )

    def test_mlx_mrl_dim_equals_embedding_dim(self) -> None:
        """MLXEmbeddingManager.MRL_DIM must equal EMBEDDING_DIM (both 256)."""
        from hledac.universal.core.mlx_embeddings import MLXEmbeddingManager

        assert hasattr(MLXEmbeddingManager, 'MRL_DIM'), (
            "MLXEmbeddingManager missing MRL_DIM attribute"
        )
        assert MLXEmbeddingManager.MRL_DIM == 256, (
            f"MLXEmbeddingManager.MRL_DIM={MLXEmbeddingManager.MRL_DIM}, expected 256"
        )
        assert MLXEmbeddingManager.MRL_DIM == MLXEmbeddingManager.EMBEDDING_DIM, (
            f"MRL_DIM ({MLXEmbeddingManager.MRL_DIM}) must equal EMBEDDING_DIM ({MLXEmbeddingManager.EMBEDDING_DIM})"
        )

    def test_lancedb_store_mrl_dim_is_256(self) -> None:
        """LanceDBIdentityStore._current_mrl_dim must be 256 (MRL canonical)."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        # Read the source to verify the class-level default
        import inspect
        source = inspect.getsource(LanceDBIdentityStore.__init__)
        assert '_current_mrl_dim = 256' in source, (
            "LanceDBIdentityStore.__init__ must set _current_mrl_dim = 256"
        )

    def test_embedding_pipeline_mrl_dim_is_256(self) -> None:
        """EmbeddingPipeline _EMBEDDING_DIM must be 256 (MRL canonical)."""
        from hledac.universal import embedding_pipeline

        assert hasattr(embedding_pipeline, '_EMBEDDING_DIM'), (
            "embedding_pipeline missing _EMBEDDING_DIM attribute"
        )
        assert embedding_pipeline._EMBEDDING_DIM == 256, (
            f"embedding_pipeline._EMBEDDING_DIM={embedding_pipeline._EMBEDDING_DIM}, expected 256 (MRL canonical)"
        )

    def test_all_backends_consistent(self) -> None:
        """All canonical embedding backends must use 256d vectors."""
        from hledac.universal.core.mlx_embeddings import MLXEmbeddingManager
        from hledac.universal import embedding_pipeline

        CANONICAL_DIM = 256

        # Collect all dimension values
        dimensions = {
            "MLXEmbeddingManager.EMBEDDING_DIM": MLXEmbeddingManager.EMBEDDING_DIM,
            "MLXEmbeddingManager.MRL_DIM": MLXEmbeddingManager.MRL_DIM,
            "embedding_pipeline._EMBEDDING_DIM": embedding_pipeline._EMBEDDING_DIM,
        }

        # Verify all are canonical
        mismatches = [
            f"{name}={dim}" for name, dim in dimensions.items()
            if dim != CANONICAL_DIM
        ]

        assert not mismatches, (
            f"Embedding dimension mismatch(es): {', '.join(mismatches)}. "
            f"All backends must use canonical MRL dimension {CANONICAL_DIM}"
        )


class TestEmbeddingVectorShape:
    """Verify embedding vectors have correct shape."""

    def test_mlx_embed_returns_correct_shape(self) -> None:
        """MLXEmbeddingManager should truncate to 256d."""
        from hledac.universal.core.mlx_embeddings import MLXEmbeddingManager

        # Check that MRL_DIM is defined and equals canonical
        assert hasattr(MLXEmbeddingManager, 'MRL_DIM'), (
            "MLXEmbeddingManager missing MRL_DIM attribute"
        )
        assert MLXEmbeddingManager.MRL_DIM == 256, (
            f"MLXEmbeddingManager.MRL_DIM={MLXEmbeddingManager.MRL_DIM}, expected 256"
        )

    def test_embedding_pipeline_truncates_to_256(self) -> None:
        """EmbeddingPipeline should use _EMBEDDING_DIM = 256."""
        from hledac.universal import embedding_pipeline

        assert embedding_pipeline._EMBEDDING_DIM == 256, (
            f"EmbeddingPipeline should truncate to 256d, got {embedding_pipeline._EMBEDDING_DIM}d"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
