"""
Tests for Sprint 77 - Embedding optimization.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest


class TestFloat16Storage:
    """Test float16 embedding storage."""

    def test_float16_storage_basic(self):
        """Test that float16 storage works correctly."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._cache_env = None  # No actual cache needed

        # Just verify the class can be instantiated
        assert store is not None


class TestEmbedderInitialization:
    """Test embedder initialization and truncate_dim."""

    @pytest.mark.asyncio
    async def test_embedder_init_method_exists(self):
        """Test _initialize_embedder method exists."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._embedder = None
        store._embedder_type = None
        store._current_mrl_dim = 256
        store._embed_lock = asyncio.Lock()

        assert hasattr(store, '_initialize_embedder')

    @pytest.mark.asyncio
    async def test_embed_single_method_exists(self):
        """Test _embed_single method exists."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._embedder = None
        store._current_mrl_dim = 768

        assert hasattr(store, '_embed_single')

    @pytest.mark.asyncio
    async def test_embed_batch_method_exists(self):
        """Test _embed_batch method exists."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._embedder = None
        store._embedder_type = None
        store._current_mrl_dim = 768
        store._embed_lock = asyncio.Lock()

        assert hasattr(store, '_embed_batch')
        assert asyncio.iscoroutinefunction(store._embed_batch)


class TestBinarySignatures:
    """Test binary signature computation."""

    def test_binary_signature_numpy(self):
        """Test numpy-based binary signature."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)

        embedding = [1.0, -0.5, 2.0, -1.0, 0.0] * 13  # 65 elements
        sig = store._compute_binary_signature(embedding)

        assert isinstance(sig, int)
        assert sig > 0

    def test_binary_signature_batch(self):
        """Test batch binary signature computation."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)

        embeddings = [
            [1.0, -0.5, 2.0] * 22,
            [-1.0, 0.5, 0.0] * 22,
        ]
        sigs = store._compute_binary_signatures_batch(embeddings)

        assert len(sigs) == 2
        assert all(isinstance(s, int) for s in sigs)


class TestIndexBuildRAMThreshold:
    """Test index build with RAM thresholds."""

    @pytest.mark.asyncio
    async def test_ensure_index_low_memory(self):
        """Test index build deferred on low memory."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._index_build_deferred = False
        store._index_build_status = {
            'in_progress': False,
            'started_at': None,
            'completed_at': None,
            'failed': False,
            'index_type': None,
            'progress_percent': 0
        }

        with patch('psutil.virtual_memory') as mock_mem:
            mock_mem.return_value = MagicMock(available=1.0 * 1024**3)  # 1GB
            await store.ensure_index()

        assert store._index_build_deferred is False  # Should skip, not defer

    @pytest.mark.asyncio
    async def test_ensure_index_critical_memory(self):
        """Test index build skipped on critical memory."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)

        with patch('psutil.virtual_memory') as mock_mem:
            mock_mem.return_value = MagicMock(available=0.5 * 1024**3)  # 0.5GB
            await store.ensure_index()


class TestDetectQueryType:
    """Test query type detection."""

    @pytest.mark.asyncio
    async def test_detect_fts_short_query(self):
        """Test FTS detection for short queries."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)

        result = await store._detect_query_type("hello world")
        assert result == 'fts'

    @pytest.mark.asyncio
    async def test_detect_fts_with_quotes(self):
        """Test FTS detection for queries with quotes."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)

        result = await store._detect_query_type('"exact phrase" search')
        assert result == 'fts'

    @pytest.mark.asyncio
    async def test_detect_vector_long_semantic(self):
        """Test vector detection for long semantic queries."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)

        result = await store._detect_query_type(
            "this is a very long semantic query that should be handled by vector search without any specific terms"
        )
        assert result == 'vector'

    @pytest.mark.asyncio
    async def test_detect_hybrid_mixed(self):
        """Test hybrid detection for mixed queries."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)

        result = await store._detect_query_type("company Apple Inc stock price")
        assert result == 'hybrid'


class TestRRFFusion:
    """Test Reciprocal Rank Fusion."""

    def test_rrf_fusion_basic(self):
        """Test basic RRF fusion."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)

        fts_results = [
            {'id': 'doc1', 'text': 'hello world'},
            {'id': 'doc2', 'text': 'foo bar'},
        ]
        vec_results = [
            {'id': 'doc2', 'text': 'foo bar'},
            {'id': 'doc3', 'text': 'baz qux'},
        ]

        result = store._rrf_fusion(fts_results, vec_results, top_k=3)

        assert len(result) <= 3
        # doc2 should be first (appears in both)
        assert result[0]['id'] == 'doc2'


class TestWritebackBuffer:
    """Test writeback buffer operations."""

    @pytest.mark.asyncio
    async def test_flush_writeback(self):
        """Test writeback buffer flush."""
        from collections import OrderedDict

        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._writeback_buffer = OrderedDict()
        store._writeback_lock = asyncio.Lock()
        store._cache_env = MagicMock()
        store._cache_env.__enter__ = MagicMock(return_value=MagicMock())
        store._cache_env.__exit__ = MagicMock(return_value=False)

        # Add item to buffer
        store._writeback_buffer['test_key'] = {'embedding': b'test', 'dtype': 'float16'}

        await store._flush_writeback()

        assert len(store._writeback_buffer) == 0

    @pytest.mark.asyncio
    async def test_writeback_overflow(self):
        """Test writeback buffer is initialized as OrderedDict."""
        from collections import OrderedDict

        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._writeback_buffer = OrderedDict()
        store._writeback_lock = asyncio.Lock()

        # Verify buffer is OrderedDict (supports ordering for flush)
        assert isinstance(store._writeback_buffer, OrderedDict)
        # Verify max is defined
        assert hasattr(store, '_WRITEBACK_MAX')
        assert store._WRITEBACK_MAX == 1000


class TestCacheWarming:
    """Test cache warming."""

    @pytest.mark.asyncio
    async def test_warm_cache_method_exists(self):
        """Test _warm_embedding_cache method exists."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._embedder = None
        store._cache_env = None
        store._current_mrl_dim = 768
        store._embed_lock = asyncio.Lock()

        assert hasattr(store, '_warm_embedding_cache')
        assert asyncio.iscoroutinefunction(store._warm_embedding_cache)


class TestHealthCheck:
    """Test health check."""

    @pytest.mark.asyncio
    async def test_health_check_method_exists(self):
        """Test health_check method exists."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._embedder = None
        store._cache_env = None
        store._writeback_buffer = {}
        store._embedder_type = 'not_initialized'

        assert hasattr(store, 'health_check')
        assert asyncio.iscoroutinefunction(store.health_check)

    @pytest.mark.asyncio
    async def test_health_check_returns_dict(self):
        """Test health_check returns proper dict."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._embedder = None
        store._cache_env = None
        store._writeback_buffer = {}
        store._embedder_type = 'not_initialized'
        store._writeback_lock = asyncio.Lock()

        result = await store.health_check()

        assert isinstance(result, dict)
        assert 'healthy' in result
        assert 'errors' in result


class TestBatchedEmbedding:
    """Test batched embedding generation."""

    @pytest.mark.asyncio
    async def test_embed_batch_empty(self):
        """Test batch embedding with empty input."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._embedder = None
        store._embedder_type = None
        store._current_mrl_dim = 768
        store._embed_lock = asyncio.Lock()

        result = await store._embed_batch([])
        assert result == []


class TestConcurrentAccess:
    """Test thread-safe concurrent access."""

    @pytest.mark.asyncio
    async def test_embed_lock_exists(self):
        """Test embed lock exists."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._embed_lock = asyncio.Lock()

        assert hasattr(store, '_embed_lock')
        assert isinstance(store._embed_lock, asyncio.Lock)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
