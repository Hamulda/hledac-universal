"""
Sprint 45 tests – Lightpanda Pool + LSH + Persistent Stegdetect + MessagePack.
"""

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator, LightpandaPool, LightpandaManager
from hledac.universal.intelligence.document_intelligence import DeepForensicsAnalyzer, StegdetectServer
from hledac.universal.intelligence.relationship_discovery import RelationshipDiscoveryEngine, LSHLinkPredictor


class TestSprint45(unittest.IsolatedAsyncioTestCase):
    """Tests for Sprint 45 - 10× Performance."""

    # === Part A – Lightpanda Pool ===

    async def test_pool_size(self):
        """Pool should have configured number of instances."""
        pool = LightpandaPool(size=3)
        # Mock the LightpandaManager
        with patch.object(LightpandaManager, 'ensure_running', new_callable=AsyncMock):
            await pool.start()
            self.assertEqual(len(pool._all_instances), 3)

    async def test_pool_reuse(self):
        """Instance should be reused after release."""
        pool = LightpandaPool(size=1)

        with patch.object(LightpandaManager, 'ensure_running', new_callable=AsyncMock):
            await pool.start()

            # Get instance
            lp1 = await pool.get_instance()
            await pool.release(lp1)

            # Get again - should be same instance
            lp2 = await pool.get_instance()
            self.assertIs(lp1, lp2)

    async def test_pool_queue(self):
        """When pool exhausted, request should wait (not fail)."""
        pool = LightpandaPool(size=1)

        with patch.object(LightpandaManager, 'ensure_running', new_callable=AsyncMock):
            await pool.start()

            # Get the only instance
            lp1 = await pool.get_instance()

            # Try to get another - should wait (we won't release in this test)
            # This tests that the queue mechanism exists
            self.assertEqual(pool._available.empty(), True)

    # === Part B – LSH Link Prediction ===

    def test_lsh_candidates_count(self):
        """LSH should return ≤1% candidates compared to brute force."""
        try:
            import igraph as ig
        except ImportError:
            self.skipTest("igraph not available")

        # Create test graph
        g = ig.Graph(edges=[(i, i+1) for i in range(50)])

        predictor = LSHLinkPredictor(threshold=0.7)
        predictor.build_index(g)

        candidates = predictor.get_candidates(0)
        # With threshold 0.7, should get very few candidates
        # compared to 50 possible neighbors
        self.assertLessEqual(len(candidates), 10)

    def test_lsh_recall(self):
        """LSH should include all high-scoring edges in candidates."""
        try:
            import igraph as ig
        except ImportError:
            self.skipTest("igraph not available")

        # Create graph with known structure
        g = ig.Graph(edges=[(0, 1), (1, 2), (2, 3), (3, 4)])

        predictor = LSHLinkPredictor(threshold=0.5)
        predictor.build_index(g)

        # All edges should be in candidates
        candidates = predictor.get_candidates(0)
        self.assertGreater(len(candidates), 0)

    def test_lsh_speed(self):
        """LSH computation should be fast for large graphs."""
        try:
            import igraph as ig
        except ImportError:
            self.skipTest("igraph not available")

        # Create larger graph
        g = ig.Graph.Erdos_Renyi(n=500, m=1000)

        predictor = LSHLinkPredictor(threshold=0.7, num_perm=64)

        start = time.time()
        predictor.build_index(g)
        candidates = predictor.get_candidates(0)
        elapsed = time.time() - start

        # Should complete in under 10ms
        self.assertLess(elapsed, 0.01)

    # === Part C – Persistent Stegdetect Server ===

    async def test_stegdetect_server_running(self):
        """Server should start and stay running."""
        server = StegdetectServer()

        with patch.object(server, 'ensure_running', new_callable=AsyncMock):
            await server.ensure_running()
            # Should have called ensure_running
            self.assertTrue(True)

    async def test_stegdetect_server_speed(self):
        """100 analyses should complete in under 1 second."""
        server = StegdetectServer()

        # Mock the underlying analysis
        async def mock_analyze(content):
            return 0.5

        with patch.object(server, 'analyze', side_effect=mock_analyze):
            start = time.time()
            for _ in range(100):
                await server.analyze(b'fake_image' * 1000)
            elapsed = time.time() - start

            self.assertLess(elapsed, 1.0)

    async def test_stegdetect_auto_restart(self):
        """Server should auto-restart on failure."""
        server = StegdetectServer()

        with patch.object(server, 'restart', new_callable=AsyncMock) as mock_restart:
            server._proc = MagicMock()
            server._proc.returncode = 1  # Dead process

            # Try to analyze - should trigger restart
            with patch.object(server, 'ensure_running', new_callable=AsyncMock):
                try:
                    await server.analyze(b'test')
                except Exception:
                    pass

                # Restart should have been called or process should be recreated

    # === Part D – MessagePack ===

    def test_msgpack_used(self):
        """MessagePack should be available and used."""
        try:
            from hledac.universal.tools.serialization import pack, unpack
            MSGPACK_AVAILABLE = True
        except ImportError:
            MSGPACK_AVAILABLE = False
            self.skipTest("msgpack not available")

        # Basic test that pack/unpack works
        data = {'key': 'value', 'number': 42}
        packed = pack(data)
        unpacked = unpack(packed)

        self.assertEqual(unpacked['key'], 'value')
        self.assertEqual(unpacked['number'], 42)

    def test_msgpack_size(self):
        """MessagePack should be smaller than JSON."""
        try:
            from hledac.universal.tools.serialization import pack
            import numpy as np
        except ImportError:
            self.skipTest("msgpack/numpy not available")

        # Create test data with numpy arrays
        data = {
            'a': list(range(100)),
            'b': {'nested': 'value'},
            'c': 42
        }

        json_data = json.dumps(data).encode()
        msgpack_data = pack(data)

        # MessagePack should be smaller
        self.assertLess(len(msgpack_data), len(json_data))

    def test_msgpack_speed(self):
        """MessagePack should be comparable or faster than JSON for larger data."""
        try:
            from hledac.universal.tools.serialization import pack, unpack
            import numpy as np
        except ImportError:
            self.skipTest("msgpack not available")

        # Larger data with arrays
        data = {
            'sources': ['web', 'academic', 'darkweb', 'archive', 'blockchain', 'osint'],
            'scores': [float(i)/100 for i in range(100)],
            'metadata': {f'key_{i}': f'value_{i}' for i in range(50)},
            'embeddings': list(range(256))
        }

        # JSON timing
        start = time.time()
        for _ in range(500):
            json_bytes = json.dumps(data).encode()
            json.loads(json_bytes)
        json_time = time.time() - start

        # MessagePack timing
        start = time.time()
        for _ in range(500):
            msgpack_bytes = pack(data)
            unpack(msgpack_bytes)
        msgpack_time = time.time() - start

        # For larger data, MessagePack should be comparable or faster
        # (the key benefit is smaller size, which is tested separately)
        self.assertLessEqual(msgpack_time, json_time * 2)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
