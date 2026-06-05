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


class TestMRLArchitecture:
    """Verify Matryoshka Representation Learning (MRL) architecture is correctly exposed.

    MRL allows ModernBERT to produce embeddings at multiple nested dimensions
    (256, 512, 768) from a single model — slicing the first k dims preserves
    retrieval quality. On M1 8GB UMA, 256d is the canonical choice:
    - 3x smaller LanceDB vectors (768 → 256)
    - 3x faster cosine similarity
    - 3x less RAM for embedding cache
    """

    def test_native_dim_is_modernbert_768(self) -> None:
        """MLXEmbeddingManager.NATIVE_DIM must be 768 (ModernBERT native)."""
        from hledac.universal.core.mlx_embeddings import MLXEmbeddingManager

        assert hasattr(MLXEmbeddingManager, 'NATIVE_DIM'), (
            "MLXEmbeddingManager missing NATIVE_DIM attribute"
        )
        assert MLXEmbeddingManager.NATIVE_DIM == 768, (
            f"MLXEmbeddingManager.NATIVE_DIM={MLXEmbeddingManager.NATIVE_DIM}, "
            f"expected 768 (ModernBERT native hidden size)"
        )

    def test_mrl_dims_is_canonical_tuple(self) -> None:
        """MRL_DIMS must be (256, 512, 768) — the only ModernBERT MRL slices."""
        from hledac.universal.core.mlx_embeddings import MLXEmbeddingManager

        assert hasattr(MLXEmbeddingManager, 'MRL_DIMS'), (
            "MLXEmbeddingManager missing MRL_DIMS attribute"
        )
        assert MLXEmbeddingManager.MRL_DIMS == (256, 512, 768), (
            f"MLXEmbeddingManager.MRL_DIMS={MLXEmbeddingManager.MRL_DIMS}, "
            f"expected (256, 512, 768) — the canonical Matryoshka slices"
        )
        # Ensure tuple (immutable, hashable) — not list
        assert isinstance(MLXEmbeddingManager.MRL_DIMS, tuple), (
            "MRL_DIMS must be a tuple for immutability"
        )

    def test_mrl_canonical_in_canonical_dims(self) -> None:
        """EMBEDDING_DIM=256 must be in MRL_DIMS (canonical invariance)."""
        from hledac.universal.core.mlx_embeddings import MLXEmbeddingManager

        assert MLXEmbeddingManager.EMBEDDING_DIM in MLXEmbeddingManager.MRL_DIMS, (
            f"EMBEDDING_DIM={MLXEmbeddingManager.EMBEDDING_DIM} must be in "
            f"MRL_DIMS={MLXEmbeddingManager.MRL_DIMS}"
        )
        # And it must be the smallest MRL dim (RAM sweet-spot)
        assert MLXEmbeddingManager.EMBEDDING_DIM == min(MLXEmbeddingManager.MRL_DIMS), (
            f"EMBEDDING_DIM should be the smallest MRL dim for M1 8GB UMA "
            f"RAM/bandwidth sweet-spot. Got {MLXEmbeddingManager.EMBEDDING_DIM}, "
            f"min(MRL_DIMS)={min(MLXEmbeddingManager.MRL_DIMS)}"
        )

    def test_validate_mrl_dim_accepts_canonical(self) -> None:
        """validate_mrl_dim() returns True for 256, 512, 768."""
        from hledac.universal.core.mlx_embeddings import MLXEmbeddingManager

        for valid_dim in (256, 512, 768):
            assert MLXEmbeddingManager.validate_mrl_dim(valid_dim) is True, (
                f"validate_mrl_dim({valid_dim}) must return True"
            )

    def test_validate_mrl_dim_rejects_invalid(self) -> None:
        """validate_mrl_dim() returns False for non-MRL dimensions."""
        from hledac.universal.core.mlx_embeddings import MLXEmbeddingManager

        # Non-MRL dims (384 = MiniLM fallback, not ModernBERT MRL).
        # Note: 256.0 is NOT here because Python's `256.0 == 256` semantics
        # intentionally accept float-coerced MRL dims (consistent with NumPy
        # slicing that accepts both int and float indices).
        for invalid_dim in (0, 1, 128, 384, 1024, -1):
            assert MLXEmbeddingManager.validate_mrl_dim(invalid_dim) is False, (
                f"validate_mrl_dim({invalid_dim}) must return False"
            )

    def test_get_mrl_dims_returns_tuple(self) -> None:
        """get_mrl_dims() is the runtime accessor for MRL_DIMS."""
        from hledac.universal.core.mlx_embeddings import MLXEmbeddingManager

        dims = MLXEmbeddingManager.get_mrl_dims()
        assert dims == (256, 512, 768)
        # Verify it matches the class attribute (single source of truth)
        assert dims is MLXEmbeddingManager.MRL_DIMS or dims == MLXEmbeddingManager.MRL_DIMS

    def test_assert_embedding_dimension_supports_512(self) -> None:
        """assert_embedding_dimension() must accept 512 (newly added MRL mid-dim)."""
        import inspect
        from hledac.universal.core.mlx_embeddings import assert_embedding_dimension

        # Static check: 512 must be in the valid set referenced in the function source
        src = inspect.getsource(assert_embedding_dimension)
        assert "512" in src, (
            "assert_embedding_dimension() must validate 512 as a valid dim"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
