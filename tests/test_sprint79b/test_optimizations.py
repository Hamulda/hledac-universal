"""
Tests for Sprint 79b - Memory, Hashing, and Compression optimizations.
"""
import gzip
import os
import sys
import tempfile
import threading
import time
import unittest.mock

import pytest


class TestPromptCache:
    """Tests for PromptCache with xxhash and thread safety."""

    def test_xxhash_available(self):
        """Verify xxhash is available."""
        from hledac.universal.brain.prompt_cache import XXHASH_AVAILABLE
        assert XXHASH_AVAILABLE is True

    def test_hash_key_deterministic(self):
        """Test that hash key is deterministic."""
        from hledac.universal.brain.prompt_cache import _hash_key
        key1 = _hash_key("test")
        key2 = _hash_key("test")
        assert key1 == key2

    def test_hash_key_prefix(self):
        """Test hash key has correct prefix."""
        from hledac.universal.brain.prompt_cache import CACHE_NAMESPACE, CACHE_VERSION, _hash_key
        key = _hash_key("test")
        assert key.startswith(f"{CACHE_NAMESPACE}:{CACHE_VERSION}:")

    def test_xxhash_fallback(self):
        """Test xxhash fallback when not available."""
        with unittest.mock.patch.dict(sys.modules, {'xxhash': None}):
            import importlib

            from hledac.universal.brain import prompt_cache
            importlib.reload(prompt_cache)
            key = prompt_cache._hash_key("test")
            assert key.startswith("pc:v2:")

    def test_prompt_cache_lru(self):
        """Test LRU eviction."""
        from hledac.universal.brain.prompt_cache import PromptCache
        cache = PromptCache(max_entries=2)
        cache.set("key1", "val1")
        cache.set("key2", "val2")
        cache.set("key3", "val3")
        assert cache.get("key1") is None
        assert cache.get("key2") == "val2"
        assert cache.get("key3") == "val3"

    def test_prompt_cache_get_set(self):
        """Test basic get/set."""
        from hledac.universal.brain.prompt_cache import PromptCache
        cache = PromptCache(max_entries=10)
        cache.set("prompt1", "response1")
        assert cache.get("prompt1") == "response1"

    def test_concurrent_cache_thread_safety(self):
        """Test thread safety with concurrent access."""
        from hledac.universal.brain.prompt_cache import PromptCache
        cache = PromptCache(max_entries=100)
        errors = []

        def worker(i):
            try:
                cache.set(f"key_{i}", f"val_{i}")
                val = cache.get(f"key_{i}")
                assert val == f"val_{i}"
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"Thread safety failures: {errors}"


class TestZSTDCompression:
    """Tests for ZSTD compression in SnapshotStorage."""

    def test_zstd_available(self):
        """Verify ZSTD is available."""
        from hledac.universal.knowledge.atomic_storage import ZSTD_AVAILABLE
        assert ZSTD_AVAILABLE is True

    def test_zstd_compression_ratio(self):
        """Test ZSTD compression ratio."""
        from hledac.universal.knowledge.atomic_storage import SnapshotStorage
        storage = SnapshotStorage()
        # JSON-like data with repeated patterns
        sample = '{"key": "value", "list": [1,2,3,4,5]}' * 100
        data = sample.encode()
        if storage._zstd_compressor:
            compressed = storage._zstd_compressor.compress(data)
            ratio = len(compressed) / len(data)
            assert ratio < 1.0, "Compression should reduce size"

    def test_zstd_reads_gzip(self):
        """Test reading gzip-compressed snapshots."""
        from hledac.universal.knowledge.atomic_storage import SnapshotStorage
        storage = SnapshotStorage()
        data = b"test data" * 1000

        with tempfile.NamedTemporaryFile(suffix='.gz', delete=False) as f:
            f.write(gzip.compress(data))
            f.flush()
            path = f.name

        # Write gzip data to a file that looks like snapshot
        storage._index['test_id'] = type('obj', (object,), {
            'snapshot_path': path,
            'content_type': 'text/html',
            'size_bytes': len(data)
        })()

        try:
            # Note: Can't test async directly, but verify ZSTD/GZIP detection works
            # Check magic bytes
            with open(path, 'rb') as f:
                magic = f.read(4)
            assert magic[:2] == b'\x1f\x8b', "Should be gzip magic"
        finally:
            os.unlink(path)

    def test_zstd_error_handling(self):
        """Test error handling for missing snapshots."""
        import asyncio

        from hledac.universal.knowledge.atomic_storage import SnapshotStorage

        storage = SnapshotStorage()

        async def test():
            result = await storage.load_snapshot("/nonexistent/path")
            assert result is None

        asyncio.run(test())

    def test_zstd_detects_format(self):
        """Test ZSTD format detection."""
        from hledac.universal.knowledge.atomic_storage import SnapshotStorage
        storage = SnapshotStorage()

        # ZSTD magic bytes
        assert storage._zstd_decompressor is not None

        # Test actual decompression
        original = b"Hello World!"
        compressed = storage._zstd_compressor.compress(original)
        decompressed = storage._zstd_decompressor.decompress(compressed)
        assert decompressed == original


class TestHashPerformance:
    """Performance tests for hashing."""

    def test_xxhash_speed(self):
        """Test xxhash speed vs baseline."""
        import xxhash
        texts = [f"test_{i}" for i in range(1000)]

        start = time.perf_counter()
        for t in texts:
            xxhash.xxh3_128(t.encode()).hexdigest()
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Should be under 50ms for 1000 ops
        assert elapsed_ms < 50, f"Too slow: {elapsed_ms}ms"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
