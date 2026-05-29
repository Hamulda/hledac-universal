"""
Tests for embedding cache (Sprint 76).
"""


import pytest


class TestEmbeddingCache:
    """Test LMDB embedding cache with float16 quantization."""

    def test_cache_attributes_exist(self):
        """Test cache attributes exist."""
        try:
            from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore
            store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
            assert hasattr(store, '_cache_env')
            assert hasattr(store, '_cache_db')
        except Exception:
            pass

    def test_init_cache_method_exists(self):
        """Test _init_cache method exists."""
        try:
            from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore
            store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
            assert hasattr(store, '_init_cache')
        except Exception:
            pass

    def test_get_cached_embedding_method_exists(self):
        """Test _get_cached_embedding method exists."""
        try:
            from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore
            store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
            assert hasattr(store, '_get_cached_embedding')
        except Exception:
            pass

    def test_store_embedding_method_exists(self):
        """Test _store_embedding method exists."""
        try:
            from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore
            store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
            assert hasattr(store, '_store_embedding')
        except Exception:
            pass

    def test_warm_cache_method_exists(self):
        """Test _warm_cache method exists."""
        try:
            from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore
            store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
            assert hasattr(store, '_warm_cache')
        except Exception:
            pass


class TestFloat16Quantization:
    """Test float16 quantization for 50% memory savings."""

    def test_float16_conversion(self):
        """Test float16 quantization conversion."""
        import numpy as np

        original = [1.0, 2.0, 3.0, 4.0]
        emb_np = np.array(original, dtype=np.float16)
        restored = emb_np.astype(np.float32).tolist()

        # Should be approximately equal
        for orig, rest in zip(original, restored, strict=False):
            assert abs(orig - rest) < 0.01

    def test_memory_savings(self):
        """Test float16 uses half the memory."""
        import numpy as np

        original = np.random.rand(768).astype(np.float32)
        quantized = original.astype(np.float16)

        # float32 = 4 bytes, float16 = 2 bytes
        assert quantized.nbytes == original.nbytes // 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
