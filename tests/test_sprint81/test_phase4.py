"""
Tests for Sprint 81 - Fáze 4: ModernBERT MLX & Cutting-Edge
===========================================================

Tests for ModernBERT MLX embedder migration, fallback chain,
Arrow streaming, and hybrid search.
"""

import asyncio

import pytest


class TestModernBERTMLXEmbedder:
    """Test ModernBERT MLX embedder integration."""

    def test_mlx_embeddings_import(self):
        """Test MLXEmbeddingManager can be imported."""
        from hledac.core.mlx_embeddings import MLXEmbeddingManager
        assert MLXEmbeddingManager is not None

    def test_mlx_embedding_manager_creation(self):
        """Test MLXEmbeddingManager can be created."""
        from hledac.core.mlx_embeddings import MLXEmbeddingManager
        # lazy_load=True to avoid actual model loading in test
        manager = MLXEmbeddingManager(lazy_load=True)
        assert manager is not None
        assert manager.DEFAULT_MODEL is not None


class TestLanceDBEmbedderMigration:
    """Test LanceDB embedder migration to MLX."""

    def test_lancedb_embedder_type_init(self):
        """Test LanceDBIdentityStore has embedder_type attribute."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore()
        assert hasattr(store, '_embedder_type')
        assert hasattr(store, '_mlx_embed_manager')
        assert hasattr(store, '_fallback_dim')

    def test_lancedb_numpy_fallback_available(self):
        """Test numpy fallback is available for embedder."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore()
        # Set numpy fallback mode
        store._embedder_type = 'numpy_fallback'
        store._fallback_dim = 768

        # Test single embedding
        result = asyncio.get_event_loop().run_until_complete(
            store._embed_single("test text")
        )
        assert isinstance(result, list)
        assert len(result) == 768


class TestDeduplicationMLX:
    """Test deduplication MLX integration."""

    def test_deduplication_imports(self):
        """Test deduplication module can be imported."""
        from hledac.universal.utils.deduplication import ContentDeduplicator
        assert ContentDeduplicator is not None


class TestHybridSearch:
    """Test hybrid search functionality."""

    def test_hybrid_search_method_exists(self):
        """Test hybrid search method exists in LanceDB."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore()
        # Check methods exist
        assert hasattr(store, 'search_similar')
        assert hasattr(store, 'ensure_index')
        assert hasattr(store, '_detect_query_type')


class TestArrowStreaming:
    """Test Arrow streaming (placeholder for future implementation)."""

    def test_lancedb_has_arrow_compatibility(self):
        """Test LanceDB store has Arrow-compatible methods."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore()
        # LanceDB natively supports Arrow via to_arrow() method
        # This test verifies the store is Arrow-compatible
        assert hasattr(store, '_table') or store.db is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
