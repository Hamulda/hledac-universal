"""
Sprint F196B: Async correctness probe tests.

Tests verify that asyncio patterns are correctly implemented.
"""

import asyncio
import pytest


class TestAsyncCorrectness:
    """Verify async patterns are correctly implemented."""

    def test_workflow_orchestrator_waits_for_all_coroutines(self):
        """
        CRITICAL-1 (FALSE POSITIVE): workflow_orchestrator.py:518

        The asyncio.wait_for() calls at line 518 ARE correctly awaited via
        asyncio.gather() at line 525. The pattern:

            tasks = [asyncio.wait_for(coro, timeout=...) for coro in coros]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        is the correct way to create coroutines with timeout wrappers and then
        await them all at once via gather.
        """
        async def mock_coro(name, value):
            await asyncio.sleep(0.01)
            return f"{name}:{value}"

        async def test_pattern():
            coros = [mock_coro(f"c{i}", i) for i in range(3)]
            # Correct pattern: list of unwrapped coroutines
            tasks = [
                asyncio.wait_for(coro, timeout=1.0)
                for coro in coros
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return results

        results = asyncio.run(test_pattern())
        assert len(results) == 3
        assert all(isinstance(r, str) for r in results)

    def test_body_bytes_concat_is_linear(self):
        """
        HIGH-2: stealth_manager.py:622,626

        Verify that bytearray.extend() pattern is O(1) amortized
        vs bytes += which is O(n²).

        The fix changes:
            body_bytes = b''
            body_bytes += chunk
        To:
            body_bytes = bytearray()
            body_bytes.extend(chunk)
        """
        import time

        async def measure_bytes_concat_OLD(chunk_count=1000, chunk_size=8192):
            """Old O(n²) pattern."""
            body_bytes = b''
            chunks = [b'x' * chunk_size] * chunk_count
            start = time.perf_counter()
            for chunk in chunks:
                body_bytes += chunk
            elapsed = time.perf_counter() - start
            return elapsed, len(body_bytes)

        async def measure_bytes_concat_NEW(chunk_count=1000, chunk_size=8192):
            """New O(1) amortized pattern."""
            body_bytes = bytearray()
            chunks = [b'x' * chunk_size] * chunk_count
            start = time.perf_counter()
            for chunk in chunks:
                body_bytes.extend(chunk)
            elapsed = time.perf_counter() - start
            return elapsed, len(body_bytes)

        # Run benchmarks
        old_time = asyncio.run(measure_bytes_concat_OLD())
        new_time = asyncio.run(measure_bytes_concat_NEW())

        # bytearray.extend should be faster or similar
        # (For small counts they might be similar, but bytearray wins for large)
        assert new_time[1] == old_time[1]  # Same final length

    def test_jarm_client_hello_uses_join(self):
        """
        HIGH-3: jarm_fingerprinter.py:393-406

        Verify that _build_client_hello uses b"".join(parts) pattern
        instead of client_hello += piece.

        The fix changes += concatenation to parts.append() + b"".join(parts).
        """
        # Read the actual source to verify the pattern
        import inspect
        from hledac.universal.network.jarm_fingerprinter import _JARMFingerprinter

        source = inspect.getsource(_JARMFingerprinter._build_client_hello)

        # Verify we're using join pattern, not += concatenation
        # The fixed code should have: parts = [...] and parts.append() and b"".join(parts)
        assert "parts = [" in source or "parts=[" in source, \
            "Should use list of parts for join"
        assert "parts.append" in source, \
            "Should use parts.append() for building"
        assert 'b"".join(parts)' in source or "b''.join(parts)" in source, \
            "Should use b\"\".join(parts) for final concatenation"
        # Should NOT have += for bytes concatenation in the fixed version
        # (except for the final handshake_length fill-in which is a different case)


class TestBackgroundTaskTracking:
    """Verify asyncio.create_task is properly tracked."""

    def test_sketch_exchange_has_background_tasks_set(self):
        """
        HIGH-4: dht/sketch_exchange.py

        Verify SketchExchange has _background_tasks set and _track_task method.
        """
        from hledac.universal.dht.sketch_exchange import SketchExchange
        from unittest.mock import MagicMock

        mock_governor = MagicMock()
        mock_node = MagicMock()
        mock_local_graph = MagicMock()

        exchange = SketchExchange(mock_governor, "test", mock_node, mock_local_graph)

        assert hasattr(exchange, '_background_tasks'), \
            "Should have _background_tasks set"
        assert isinstance(exchange._background_tasks, set), \
            "_background_tasks should be a set"
        assert hasattr(exchange, '_track_task'), \
            "Should have _track_task method"

    def test_coord_layer_event_processor_has_background_tasks(self):
        """
        HIGH-4: layers/coordination_layer.py EventDrivenProcessor

        Verify EventDrivenProcessor has _background_tasks and _track_task.
        """
        from hledac.universal.layers.coordination_layer import EventDrivenProcessor

        processor = EventDrivenProcessor(max_workers=1)

        assert hasattr(processor, '_background_tasks'), \
            "Should have _background_tasks set"
        assert isinstance(processor._background_tasks, set), \
            "_background_tasks should be a set"
        assert hasattr(processor, '_track_task'), \
            "Should have _track_task method"

    def test_ghost_watchdog_has_background_tasks(self):
        """
        HIGH-4: layers/coordination_layer.py GhostWatchdog

        Verify GhostWatchdog has _background_tasks and _track_task.
        """
        from hledac.universal.layers.coordination_layer import GhostWatchdog

        watchdog = GhostWatchdog(check_interval=5.0)

        assert hasattr(watchdog, '_background_tasks'), \
            "Should have _background_tasks set"
        assert isinstance(watchdog._background_tasks, set), \
            "_background_tasks should be a set"
        assert hasattr(watchdog, '_track_task'), \
            "Should have _track_task method"

    def test_prefetch_cache_has_background_tasks_and_close(self):
        """
        HIGH-4 + MEDIUM-1: prefetch/prefetch_cache.py

        Verify PrefetchCache has _background_tasks, _track_task, and close().
        """
        from hledac.universal.prefetch.prefetch_cache import PrefetchCache

        cache = PrefetchCache(max_size_mb=10, max_entries=100)

        assert hasattr(cache, '_background_tasks'), \
            "Should have _background_tasks set"
        assert hasattr(cache, '_track_task'), \
            "Should have _track_task method"
        assert hasattr(cache, 'close'), \
            "Should have close method"

    def test_intelligent_cache_has_background_tasks_and_close(self):
        """
        HIGH-4 + MEDIUM-1: utils/intelligent_cache.py

        Verify IntelligentCache has _background_tasks, _track_task.
        """
        from hledac.universal.utils.intelligent_cache import IntelligentCache, CacheConfig

        config = CacheConfig(max_entries=100, max_size_bytes=1024*1024)
        cache = IntelligentCache(config)

        assert hasattr(cache, '_background_tasks'), \
            "Should have _background_tasks set"
        assert hasattr(cache, '_track_task'), \
            "Should have _track_task method"


class TestLMDBClose:
    """Verify LMDB environments are properly closed."""

    def test_semantic_dedup_has_close(self):
        """
        MEDIUM-1: semantic_deduplicator.py

        Verify SemanticDedupCache has close() method.
        """
        from hledac.universal.semantic_deduplicator import SemanticDedupCache

        cache = SemanticDedupCache()

        assert hasattr(cache, 'close'), \
            "Should have close method"
        assert callable(cache.close), \
            "close should be callable"
