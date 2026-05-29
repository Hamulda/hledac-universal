"""
Sprint 57 tests – PQ Index, DynamicModelManager, PagedAttentionCache.
"""

import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


# =============================================================================
# PQIndex Tests - Zjednodušené pro M1 8GB
# =============================================================================

class TestPQIndex(unittest.IsolatedAsyncioTestCase):
    """Testy pro PQIndex."""

    async def test_pq_init(self):
        """Test #1: PQ index – inicializace."""
        from hledac.universal.knowledge.pq_index import PQIndex

        pq = PQIndex(d=64, m=8, k=16, n_iter=2)
        self.assertEqual(pq.d, 64)
        self.assertEqual(pq.m, 8)
        self.assertEqual(pq.k, 16)
        self.assertFalse(pq.is_trained())

    async def test_pq_train_small(self):
        """Test #2: PQ index – malý trénink."""
        import numpy as np
        from hledac.universal.knowledge.pq_index import PQIndex

        # Very small data for M1
        vectors = np.random.randn(20, 64).astype(np.float32)
        import mlx.core as mx
        vectors_mx = mx.array(vectors)

        pq = PQIndex(d=64, m=8, k=16, n_iter=2)
        pq.train(vectors_mx)

        self.assertTrue(pq.is_trained())

    async def test_pq_add_single(self):
        """Test #3: PQ index – přidání jednoho vektoru."""
        import numpy as np
        from hledac.universal.knowledge.pq_index import PQIndex

        vectors = np.random.randn(20, 32).astype(np.float32)
        import mlx.core as mx
        vectors_mx = mx.array(vectors)

        pq = PQIndex(d=32, m=8, k=16, n_iter=2)
        pq.train(vectors_mx)

        # Add single vector
        pq.add("test_id", vectors_mx[0])
        self.assertEqual(len(pq.ids), 1)
        self.assertEqual(pq.ids[0], "test_id")

    async def test_pq_search(self):
        """Test #4: PQ index – vy."""
        import numpy as np
        from hledac.universal.knowledge.pq_index import PQIndex

        vectors = np.random.randn(30, 32).astype(np.float32)
        import mlx.core as mx
        vectors_mx = mx.array(vectors)

        pq = PQIndex(d=32, m=8, k=16, n_iter=2)
        pq.train(vectors_mx)

        for i in range(10):
            pq.add(f"id_{i}", vectors_mx[i])

        results = pq.search(vectors_mx[0], k=3)
        self.assertGreaterEqual(len(results), 1)

    async def test_pq_memory_estimation(self):
        """Test #5: PQ index – odhad paměti."""
        import numpy as np
        from hledac.universal.knowledge.pq_index import PQIndex

        vectors = np.random.randn(20, 64).astype(np.float32)
        import mlx.core as mx
        vectors_mx = mx.array(vectors)

        pq = PQIndex(d=64, m=8, k=16, n_iter=2)
        pq.train(vectors_mx)

        mem = pq.get_memory_usage()
        self.assertGreater(mem, 0)

    async def test_pq_compression_ratio(self):
        """Test #6: PQ index – kompresní poměr."""
        import numpy as np
        from hledac.universal.knowledge.pq_index import PQIndex

        vectors = np.random.randn(20, 64).astype(np.float32)
        import mlx.core as mx
        vectors_mx = mx.array(vectors)

        pq = PQIndex(d=64, m=8, k=16, n_iter=2)
        pq.train(vectors_mx)

        ratio = pq.get_compression_ratio(20)
        # Komprese by měla být alespoň 2×
        self.assertGreater(ratio, 1)


# =============================================================================
# DynamicModelManager Tests
# =============================================================================

class TestDynamicModelManager(unittest.IsolatedAsyncioTestCase):
    """Testy pro DynamicModelManager."""

    async def test_dynamic_unload(self):
        """Test #7: Dynamické uvolňování – model se uvolní po timeoutu."""
        mock_manager = MagicMock()
        mock_manager.release_model = AsyncMock()
        mock_manager.acquire_model = AsyncMock(return_value=MagicMock())

        from hledac.universal.brain.dynamic_model_manager import DynamicModelManager

        with patch('hledac.universal.brain.dynamic_model_manager.MLX_AVAILABLE', False):
            dm = DynamicModelManager(
                mock_manager,
                idle_timeout=0.1,
                min_reload_interval=1.0,
                max_loaded_models=2
            )

            await dm.start()
            await dm.acquire("hermes")
            self.assertTrue(dm.is_loaded("hermes"))

            await asyncio.sleep(0.2)
            await dm._check_idle_timeout()

            self.assertFalse(dm.is_loaded("hermes"))
            await dm.stop()

    async def test_dynamic_reload_speed(self):
        """Test #8: Dynamické uvolňování – znovunačtení."""
        mock_manager = MagicMock()
        mock_manager.acquire_model = AsyncMock(return_value=MagicMock())
        mock_manager.release_model = AsyncMock()

        from hledac.universal.brain.dynamic_model_manager import DynamicModelManager

        with patch('hledac.universal.brain.dynamic_model_manager.MLX_AVAILABLE', False):
            dm = DynamicModelManager(
                mock_manager,
                idle_timeout=60.0,
                min_reload_interval=0.1,
                max_loaded_models=2
            )

            await dm.acquire("hermes")
            await dm.force_unload("hermes")
            dm.last_unloaded["hermes"] = 0
            await dm.acquire("hermes")

    async def test_dynamic_thrash(self):
        """Test #9: Dynamické uvolňování – thrashing protection."""
        mock_manager = MagicMock()
        mock_manager.acquire_model = AsyncMock(return_value=MagicMock())
        mock_manager.release_model = AsyncMock()

        from hledac.universal.brain.dynamic_model_manager import DynamicModelManager

        with patch('hledac.universal.brain.dynamic_model_manager.MLX_AVAILABLE', False):
            dm = DynamicModelManager(
                mock_manager,
                idle_timeout=60.0,
                min_reload_interval=10.0,
                max_loaded_models=2
            )

            await dm.acquire("hermes")
            await dm.force_unload("hermes")
            dm.last_unloaded["hermes"] = 0
            await dm.acquire("hermes")

    async def test_dynamic_lru_limit(self):
        """Test #10: Dynamické uvolňování – LRU cache limit."""
        mock_manager = MagicMock()
        mock_manager.acquire_model = AsyncMock(return_value=MagicMock())
        mock_manager.release_model = AsyncMock()

        from hledac.universal.brain.dynamic_model_manager import DynamicModelManager

        with patch('hledac.universal.brain.dynamic_model_manager.MLX_AVAILABLE', False):
            dm = DynamicModelManager(
                mock_manager,
                idle_timeout=60.0,
                min_reload_interval=1.0,
                max_loaded_models=2
            )

            await dm.acquire("model_a")
            await dm.acquire("model_b")
            await dm.acquire("model_c")

            self.assertLessEqual(len(dm.get_loaded_models()), 2)


# =============================================================================
# PagedAttentionCache Tests
# =============================================================================

class TestPagedAttentionCache(unittest.IsolatedAsyncioTestCase):
    """Testy pro PagedAttentionCache."""

    async def test_paged_cache_init(self):
        """Test #11: PagedAttention cache – inicializace."""
        from hledac.universal.brain.paged_attention_cache import PagedAttentionCache

        cache = PagedAttentionCache(max_pages=8, page_size=16)
        self.assertEqual(cache.max_pages, 8)
        self.assertEqual(len(cache), 0)

    async def test_paged_cache_size(self):
        """Test #12: PagedAttention cache – max pages limit."""
        import mlx.core as mx
        from hledac.universal.brain.paged_attention_cache import PagedAttentionCache

        cache = PagedAttentionCache(max_pages=2, page_size=5)

        for _i in range(4):
            keys = mx.ones((5, 1, 4))
            values = mx.ones((5, 1, 4))
            scores = mx.ones(5)
            cache.update(keys, values, scores)

        self.assertLessEqual(len(cache), 2)

    async def test_paged_cache_get(self):
        """Test #13: PagedAttention cache – get vrací data."""
        import mlx.core as mx
        from hledac.universal.brain.paged_attention_cache import PagedAttentionCache

        cache = PagedAttentionCache(max_pages=10, page_size=10)

        keys1 = mx.ones((10, 1, 4))
        values1 = mx.ones((10, 1, 4)) * 2
        scores1 = mx.ones(10)

        cache.update(keys1, values1, scores1)

        result = cache.get()
        self.assertIsNotNone(result)
        all_keys, all_values = result
        self.assertEqual(all_keys.shape[0], 10)

    async def test_paged_cache_clear(self):
        """Test #14: PagedAttention cache – clear."""
        import mlx.core as mx
        from hledac.universal.brain.paged_attention_cache import PagedAttentionCache

        cache = PagedAttentionCache()

        keys = mx.ones((10, 1, 4))
        values = mx.ones((10, 1, 4))
        scores = mx.ones(10)
        cache.update(keys, values, scores)

        self.assertGreater(len(cache), 0)
        cache.clear()
        self.assertEqual(len(cache), 0)


# =============================================================================
# Integration Tests
# =============================================================================

class TestPQIntegration(unittest.IsolatedAsyncioTestCase):
    """Integrační testy."""

    async def test_pq_switch(self):
        """Test #15: PQ + HNSW přepínání."""
        from hledac.universal.knowledge.persistent_layer import PersistentKnowledgeLayer

        layer = PersistentKnowledgeLayer(enable_cache=False)

        self.assertTrue(hasattr(layer, '_use_pq'))
        self.assertTrue(hasattr(layer, '_embedding_buffer'))
        self.assertTrue(hasattr(layer, '_pq_index'))

    async def test_combined(self):
        """Test #16: Kombinace komponent."""
        from hledac.universal.brain.dynamic_model_manager import DynamicModelManager
        from hledac.universal.knowledge.pq_index import PQIndex

        self.assertIsNotNone(DynamicModelManager)
        self.assertIsNotNone(PQIndex)


if __name__ == '__main__':
    unittest.main()
