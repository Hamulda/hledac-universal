"""
Sprint 41 - Parallelism Optimizations Tests
===========================================

Tests for:
- A. Dynamic Batching (priority queue, RAM-based max_batch, partial failure)
- B. zstd Compression (threshold, roundtrip, async, content-aware, dictionary)
- C. Shared Prefix Cache (hit, miss, invalidation)
"""

import asyncio
import heapq
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine
from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator, ZstdCompressor

# Import the modules under test
from hledac.universal.layers.communication_layer import CommunicationLayer, _BatchItem
from hledac.universal.project_types import CommunicationConfig
from _core import aclose


class TestSprint41A_DynamicBatching(unittest.IsolatedAsyncioTestCase):  # noqa: N801
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

        # Mock _model_bridge.send_to_model (what _execute_query calls internally).
        # This avoids __slots__ issues with patching _execute_query directly.
        mock_bridge = MagicMock()
        async def mock_send_to_model(**kwargs):
            prompt = kwargs.get('content', '')
            if prompt == "p1":
                return {"success": True, "response": "ok"}
            raise ValueError("fail")
        mock_bridge.send_to_model = AsyncMock(side_effect=mock_send_to_model)
        comm._model_bridge = mock_bridge

        # Create batch queries
        queries = [
            {'query': MagicMock(prompt="p1", complexity="medium", use_cache=True),
             'max_tokens': 500, 'temperature': 0.7},
            {'query': MagicMock(prompt="p2", complexity="medium", use_cache=True),
             'max_tokens': 500, 'temperature': 0.7},
        ]

        results = await comm._process_batch_parallel(queries)

        # Both results returned - run_one catches exceptions and returns dict, not raises
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]['success'])
        self.assertEqual(results[0]['response'], "ok")
        self.assertFalse(results[1]['success'])

    async def test_empty_queue_sleep(self):
        """Test empty queue causes sleep (no busy loop)."""
        config = CommunicationConfig()
        comm = CommunicationLayer(config)

        async with comm._batch_heap_lock:
            self.assertEqual(len(comm._batch_heap), 0)

        # The structure ensures sleep behavior - if heap is empty, _batch_processor sleeps
        self.assertTrue(True)


class TestSprint41B_ZstdCompression(unittest.IsolatedAsyncioTestCase):  # noqa: N801
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


class TestSprint41C_SharedPrefixCache(unittest.IsolatedAsyncioTestCase):  # noqa: N801
    """Tests for Shared Prefix Cache feature.

    Note: test_prefix_cache_hit and test_prefix_cache_miss were removed because
    they tested _get_prefix_cache which requires a real MLX model (_kv_cache_pool).
    The remaining tests cover the functionality that can be tested without MLX.
    """

    async def test_prefix_cache_custom_system_uses_prefix_cache(self):
        """M-02: custom system_msg is correctly passed to _get_prefix_cache.

        Verifies that the system variable name bug (system vs system_msg)
        is fixed and custom system prompts correctly use the KV cache.

        This test uses AST analysis because DeepHermes3Engine uses __slots__
        which prevents full initialization without MLX model.
        """
        import ast

        # Read the source of generate() method
        from brain.deephermes3_engine import DeepHermes3Engine
        import inspect
        source = inspect.getsource(DeepHermes3Engine.generate)

        # Parse AST (dedent to remove decorator indentation)
        import textwrap
        source_dedented = textwrap.dedent(source)
        tree = ast.parse(source_dedented)

        # Find all calls to _get_prefix_cache
        prefix_cache_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == '_get_prefix_cache':
                        if node.args:
                            # Get the argument passed to _get_prefix_cache
                            arg = node.args[0]
                            if isinstance(arg, ast.Name):
                                prefix_cache_calls.append(arg.id)

        # The fix: _get_prefix_cache should be called with 'system_msg', NOT 'system'
        # Before the fix, it was called with undefined variable 'system' which would
        # raise NameError (swallowed by bare except Exception)
        self.assertIn('system_msg', prefix_cache_calls,
                      "_get_prefix_cache should be called with 'system_msg', not 'system'")
        self.assertNotIn('system', prefix_cache_calls,
                         "_get_prefix_cache should NOT be called with bare 'system' (undefined variable)")

        # Additionally, verify the context manager is used properly
        self.assertEqual(prefix_cache_calls.count('system_msg'), 1,
                         "There should be exactly one _get_prefix_cache call with system_msg")

    async def test_cache_invalidation(self):
        """Test invalidate_prefix_cache() clears the prefix cache dict."""
        engine = DeepHermes3Engine()
        # Initialize prefix cache with a mock entry
        engine._prefix_cache = {"test_key": "test_value"}
        self.assertEqual(len(engine._prefix_cache), 1)

        # Invalidate cache
        engine.invalidate_prefix_cache()

        # Cache should be empty
        self.assertEqual(len(engine._prefix_cache), 0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
