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
        from compat.core_mlx_embeddings import MLXEmbeddingManager
        assert MLXEmbeddingManager is not None

    def test_mlx_embedding_manager_creation(self):
        """Test MLXEmbeddingManager can be created."""
        from compat.core_mlx_embeddings import MLXEmbeddingManager
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
        # Sprint F259: MRL canonical dim is 256, but legacy test uses 768
        # for backward compat with pre-MRL LanceDB vectors. The numpy fallback
        # honours whatever _fallback_dim is set, so both values are valid.
        store._fallback_dim = 256  # MRL canonical (Sprint F259)

        # Test single embedding using asyncio.run() — Python 3.12+ safe
        # (asyncio.get_event_loop() is deprecated and raises RuntimeError
        # when no event loop exists in the main thread).
        result = asyncio.run(store._embed_single("test text"))
        assert isinstance(result, list)
        assert len(result) == 256
        # Verify L2 normalization (numpy fallback normalizes embeddings)
        import math
        norm = math.sqrt(sum(x * x for x in result))
        assert abs(norm - 1.0) < 1e-5, (
            f"numpy fallback should produce L2-normalized embedding, got norm={norm}"
        )


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


class TestKeywordBootstrap3_3:
    """Tests for 3.3 Public Discovery Bootstrap — keyword-based search engine fallback."""

    @pytest.mark.asyncio
    async def test_keyword_bootstrap_empty_query_returns_empty(self):
        """Empty query returns empty list."""
        from hledac.universal.pipeline.live_public_pipeline import generate_keyword_bootstrap_urls
        result = await generate_keyword_bootstrap_urls("")
        assert result == []

    @pytest.mark.asyncio
    async def test_keyword_bootstrap_whitespace_query_returns_empty(self):
        """Whitespace-only query returns empty list."""
        from hledac.universal.pipeline.live_public_pipeline import generate_keyword_bootstrap_urls
        result = await generate_keyword_bootstrap_urls("   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_keyword_bootstrap_max_urls_respected(self):
        """Returns at most max_urls hits."""
        from hledac.universal.pipeline.live_public_pipeline import (
            generate_keyword_bootstrap_urls,
        )
        from hledac.universal.pipeline import live_public_pipeline as lpp
        fake_hits = [{"title": f"t{i}", "url": f"http://x{i}.com", "snippet": f"s{i}"} for i in range(20)]
        orig = lpp._search_multi_engine_bootstrap
        # type: ignore[assignment] — intentional mock
        lpp._search_multi_engine_bootstrap = lambda q, max_results: fake_hits  # type: ignore[assignment]
        try:
            result = await generate_keyword_bootstrap_urls("test query", max_urls=5)
            assert len(result) <= 5
        finally:
            lpp._search_multi_engine_bootstrap = orig

    @pytest.mark.asyncio
    async def test_keyword_bootstrap_discovery_hit_fields(self):
        """DiscoveryHit has correct fields: reason, source, score."""
        from hledac.universal.pipeline.live_public_pipeline import (
            generate_keyword_bootstrap_urls,
        )
        from hledac.universal.pipeline import live_public_pipeline as lpp
        fake_hits = [{"title": "Test", "url": "http://test.com", "snippet": "test snippet"}]
        orig = lpp._search_multi_engine_bootstrap

        async def fake_multi(q, max_results):
            return fake_hits

        lpp._search_multi_engine_bootstrap = fake_multi  # type: ignore[assignment]
        try:
            result = await generate_keyword_bootstrap_urls("test query")
            assert len(result) == 1
            hit = result[0]
            assert hit.reason is not None and hit.reason.startswith("keyword_bootstrap_")
            assert hit.source == "duckduckgo"  # first engine in multi_engine
            assert hit.score == 0.75
            assert hit.url == "http://test.com"
            assert hit.query == "test query"
        finally:
            lpp._search_multi_engine_bootstrap = orig

    @pytest.mark.asyncio
    async def test_keyword_bootstrap_all_engines_fail_returns_empty(self):
        """All engines throw → returns empty list."""
        from hledac.universal.pipeline.live_public_pipeline import generate_keyword_bootstrap_urls
        from hledac.universal.pipeline import live_public_pipeline as lpp

        async def fake_fail(q, max_results):
            raise RuntimeError("network error")

        orig = lpp._search_multi_engine_bootstrap
        lpp._search_multi_engine_bootstrap = fake_fail  # type: ignore[assignment]
        try:
            result = await generate_keyword_bootstrap_urls("test query")
            assert result == []
        finally:
            lpp._search_multi_engine_bootstrap = orig


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
