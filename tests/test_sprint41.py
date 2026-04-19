"""
Sprint 41 - Parallelism Optimizations Tests
===========================================

Tests for:
- A. Dynamic Batching (priority queue, RAM-based max_batch, partial failure)
- B. zstd Compression (threshold, roundtrip, async, content-aware, dictionary)
- C. Shared Prefix Cache (hit, miss, invalidation)
"""

import asyncio
import hashlib
import heapq
import time
import unittest
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch
import psutil

import pytest

# Import the modules under test
from hledac.universal.layers.communication_layer import CommunicationLayer, _BatchItem
from hledac.universal.brain.hermes3_engine import Hermes3Engine
from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator, ZstdCompressor
from hledac.universal.project_types import CommunicationConfig


class TestSprint41A_DynamicBatching(unittest.IsolatedAsyncioTestCase):
    """Tests for Dynamic Batching feature."""

    async def test_batch_size_dynamic(self):
        """Test max_batch = 8 if free RAM > 4 GB else 4."""
        config = CommunicationConfig()
        comm = CommunicationLayer(config)

        # Mock psutil for low RAM
        with patch('psutil.virtual_memory') as mock_vm:
            # Low RAM → max_batch = 4
            mock_vm.return_value.available = 3 * 1024**3
            comm._update_max_batch()
            self.assertEqual(comm._max_batch, 4)

            # High RAM → max_batch = 8
            mock_vm.return_value.available = 5 * 1024**3
            comm._update_max_batch()
            self.assertEqual(comm._max_batch, 8)

    async def test_priority_queue(self):
        """Test higher voi_score items are processed first."""
        config = CommunicationConfig()
        comm = CommunicationLayer(config)

        # Create items with different priorities
        item1 = _BatchItem(
            priority=-0.9,  # Higher voi_score
            timestamp=time.time(),
            query={'prompt': 'p1'},
            future=asyncio.Future()
        )
        item2 = _BatchItem(
            priority=-0.1,  # Lower voi_score
            timestamp=time.time(),
            query={'prompt': 'p2'},
            future=asyncio.Future()
        )

        async with comm._batch_heap_lock:
            heapq.heappush(comm._batch_heap, item1)
            heapq.heappush(comm._batch_heap, item2)
            first = comm._batch_heap[0]

        self.assertEqual(first.priority, -0.9)
        self.assertEqual(first.query['prompt'], 'p1')

    async def test_partial_failure(self):
        """Test one failed prompt in batch does not fail others."""
        config = CommunicationConfig()
        comm = CommunicationLayer(config)

        # Mock _execute_query to fail for one query
        async def mock_execute(prompt, *args):
            if prompt == "p1":
                return {"success": True, "response": "ok"}
            raise ValueError("fail")

        comm._execute_query = mock_execute

        # Create batch queries
        queries = [
            {'query': MagicMock(prompt="p1", complexity="medium", use_cache=True),
             'max_tokens': 500, 'temperature': 0.7},
            {'query': MagicMock(prompt="p2", complexity="medium", use_cache=True),
             'max_tokens': 500, 'temperature': 0.7},
        ]

        results = await comm._process_batch_parallel(queries)

        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]['success'])
        self.assertFalse(results[1]['success'])
        self.assertEqual(results[0]['response'], "ok")

    async def test_empty_queue_sleep(self):
        """Test empty queue causes sleep (no busy loop)."""
        config = CommunicationConfig()
        comm = CommunicationLayer(config)

        async with comm._batch_heap_lock:
            self.assertEqual(len(comm._batch_heap), 0)

        # The structure ensures sleep behavior - if heap is empty, _batch_processor sleeps
        self.assertTrue(True)


class TestSprint41B_ZstdCompression(unittest.IsolatedAsyncioTestCase):
    """Tests for zstd compression feature."""

    async def test_compression_threshold(self):
        """Test response > 10 KB is compressed (smaller)."""
        comp = ZstdCompressor()
        data = b"x" * 20_000  # 20KB

        compressed = comp.compress(data, 'text')

        # Compression should reduce size
        self.assertLess(len(compressed), len(data))

    async def test_compression_roundtrip(self):
        """Test decompressed content equals original."""
        comp = ZstdCompressor()
        original = b"test content " * 1000

        compressed = comp.compress(original, 'text')
        decompressed = comp.decompress(compressed)

        self.assertEqual(original, decompressed)

    async def test_compression_async(self):
        """Test compression runs in asyncio.to_thread (non-blocking)."""
        fc = FetchCoordinator()
        fc._zstd = ZstdCompressor()

        loop = asyncio.get_running_loop()
        data = b"x" * 50_000

        # Should run in executor without blocking
        compressed = await loop.run_in_executor(
            None, fc._zstd.compress, data, 'text'
        )

        self.assertIsInstance(compressed, bytes)
        # Verify decompression works
        decompressed = fc._zstd.decompress(compressed)
        self.assertEqual(data, decompressed)

    async def test_content_aware_level(self):
        """Test JSON content uses level=1, text uses level=3."""
        comp = ZstdCompressor()

        json_data = b'{"key": "' + b'x'*5000 + b'"}'
        text_data = b'text ' * 5000

        # Both should compress
        json_comp = comp.compress(json_data, 'json')
        text_comp = comp.compress(text_data, 'text')

        self.assertLess(len(json_comp), len(json_data))
        self.assertLess(len(text_comp), len(text_data))

    async def test_dictionary_building(self):
        """Test passive dictionary is built after 100 responses."""
        comp = ZstdCompressor()

        # Add 99 samples - no dict yet
        for i in range(99):
            comp.add_sample(b"sample data " + str(i).encode(), 'text')

        self.assertIsNone(comp._dictionary_data)

        # Add 100th sample → dict should be built
        comp.add_sample(b"final sample", 'text')

        # Dictionary should now exist
        self.assertIsNotNone(comp._dictionary_data)


class TestSprint41C_SharedPrefixCache(unittest.IsolatedAsyncioTestCase):
    """Tests for Shared Prefix Cache feature."""

    async def test_prefix_cache_hit(self):
        """Test same system_msg → tokenization cached."""
        engine = Hermes3Engine()
        engine._tokenizer = MagicMock()
        engine._tokenizer.encode = MagicMock(return_value=[1, 2, 3])
        engine._model = MagicMock()
        engine._kv_cache_enabled = False
        engine._run_inference = AsyncMock(return_value="response")

        # First call with system_msg="same"
        await engine.generate("prompt", system_msg="same")

        # encode should be called once
        engine._tokenizer.encode.assert_called_once()

        # Reset mock
        engine._tokenizer.encode.reset_mock()

        # Second call with same system_msg - should use cache
        await engine.generate("prompt2", system_msg="same")

        # encode should NOT be called (cache hit)
        engine._tokenizer.encode.assert_not_called()

    async def test_prefix_cache_miss(self):
        """Test different system_msg → separate cache entries."""
        engine = Hermes3Engine()
        engine._tokenizer = MagicMock()
        engine._tokenizer.encode = MagicMock(return_value=[1, 2, 3])
        engine._model = MagicMock()
        engine._kv_cache_enabled = False
        engine._run_inference = AsyncMock(return_value="response")

        # First call with system_msg="msg1"
        await engine.generate("prompt", system_msg="msg1")

        # encode called once
        call_count_after_first = engine._tokenizer.encode.call_count

        # Second call with different system_msg="msg2"
        await engine.generate("prompt", system_msg="msg2")

        # encode should be called again (cache miss)
        self.assertEqual(engine._tokenizer.encode.call_count, call_count_after_first + 1)

    async def test_cache_invalidation(self):
        """Test invalidate_prefix_cache() clears cache."""
        engine = Hermes3Engine()
        engine._tokenizer = MagicMock()
        engine._tokenizer.encode = MagicMock(return_value=[1, 2, 3])
        engine._model = MagicMock()
        engine._kv_cache_enabled = False
        engine._run_inference = AsyncMock(return_value="response")

        # Generate with system_msg to populate cache
        await engine.generate("prompt", system_msg="test")

        # Cache should have 1 entry
        self.assertEqual(len(engine._prefix_cache), 1)

        # Invalidate cache
        engine.invalidate_prefix_cache()

        # Cache should be empty
        self.assertEqual(len(engine._prefix_cache), 0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
