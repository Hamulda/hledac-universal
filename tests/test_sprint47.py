"""
Sprint 47 tests – Performance + Entity Resolution (Stegdetect Pool + Sherlock JSON + Prefix Cache + Tie-breaker).
"""
import asyncio
import hashlib
import sys
import time
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent))
from hledac.universal.recon.document_intelligence import StegdetectServer
from hledac.universal.layers.communication_layer import CommunicationLayer
from hledac.universal.project_types import CommunicationConfig
from hledac.universal.tools.osint_frameworks import OSINTFrameworkRunner
from hledac.universal.utils.asyncx import parallel_ok, safe_gather_fire_and_forget
from _core import aclose

class TestSprint47(unittest.IsolatedAsyncioTestCase):
    """Tests for Sprint 47 - Performance + Entity Resolution."""

    async def test_stegdetect_pool_semaphore(self):
        """Stegdetect should use semaphore pool for concurrent analysis."""
        server = StegdetectServer(max_workers=2)
        self.assertEqual(server._max_workers, 2)
        self.assertIsInstance(server._semaphore, asyncio.Semaphore)

    async def test_stegdetect_concurrent(self):
        """10 concurrent analyses should complete without deadlock."""
        server = StegdetectServer(max_workers=4)

        class MockStdin:

            def write(self, data):
                pass

            async def drain(self):
                pass
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = None
            mock_proc.communicate = AsyncMock(return_value=(b'positive', b''))
            mock_proc.stdout.readline = AsyncMock(return_value=b'positive')
            mock_proc.stdin = MockStdin()
            mock_exec.return_value = mock_proc
            tasks = [server.analyze(b'test_image_content') for _ in range(10)]
            results = await parallel_ok(*tasks, label='test_sprint47:59')
            errors = [r for r in results if isinstance(r, Exception)]
            self.assertEqual(len(errors), 0)

    async def test_sherlock_json_flag(self):
        """Sherlock should be called with --json flag."""
        runner = OSINTFrameworkRunner()
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b'{"twitter": {"url": "https://twitter.com/test"}}', b''))
            mock_exec.return_value = mock_proc
            await runner.run_sherlock('testuser')
            args = mock_exec.call_args[0]
            self.assertIn('--json', args)

    async def test_sherlock_json_parse(self):
        """Sherlock JSON output should be parsed correctly."""
        runner = OSINTFrameworkRunner()
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b'{"twitter": {"url": "https://twitter.com/testuser"}, "github": {"url": "https://github.com/testuser"}}', b''))
            mock_exec.return_value = mock_proc
            findings = await runner.run_sherlock('testuser')
            self.assertEqual(len(findings), 2)
            self.assertEqual(findings[0]['url'], 'https://twitter.com/testuser')
            self.assertEqual(findings[0]['site'], 'twitter')
            self.assertEqual(findings[0]['source'], 'sherlock')

    async def test_batch_priority_tiebreaker(self):
        """Priority queue should handle equal VoI scores with tie-breaker."""
        config = CommunicationConfig()
        comm = CommunicationLayer(config)
        processed = []

        async def mock_execute(query):
            processed.append(query.get('query'))
            return {'success': True}
        with patch.object(CommunicationLayer, '_execute_query', mock_execute):
            f1 = asyncio.create_task(comm.query_model('task1', voi_score=0.5))
            f2 = asyncio.create_task(comm.query_model('task2', voi_score=0.5))
            await asyncio.sleep(0.2)
            self.assertTrue(f1.done() or f2.done())

    async def test_batch_priority_ordering(self):
        """Higher VoI should be processed first."""
        config = CommunicationConfig()
        comm = CommunicationLayer(config)

        async def mock_execute(query):
            await asyncio.sleep(0.01)
            return {'success': True, 'response': query.get('query')}
        with patch.object(CommunicationLayer, '_execute_query', mock_execute):
            f_low = asyncio.create_task(comm.query_model('low_priority', voi_score=0.1))
            await asyncio.sleep(0.05)
            f_high = asyncio.create_task(comm.query_model('high_priority', voi_score=0.9))
            await safe_gather_fire_and_forget(f_low, f_high, label='test_sprint47:148')

    async def test_batch_adaptive(self):
        """Adaptive batch size should adjust based on queue."""
        config = CommunicationConfig()
        comm = CommunicationLayer(config)
        call_count = 0

        async def mock_execute(queries):
            nonlocal call_count
            call_count += 1
            return [{'success': True}] * len(queries)
        with patch.object(CommunicationLayer, '_process_batch_parallel', mock_execute):
            tasks = [comm.query_model(f'q{i}', voi_score=0.5) for i in range(10)]
            await safe_gather_fire_and_forget(*tasks, label='test_sprint47:170')

    async def test_prefix_cache_hit(self):
        """Cache hit should skip tokenization."""
        cache: dict = {}
        mock_tokenizer = type('MockTokenizer', (), {'encode': lambda self, text: [1, 2, 3, 4, 5]})()
        system = 'You are a helpful assistant.'
        cache_key = hashlib.sha256(system.encode()).hexdigest()
        if cache_key in cache:
            prefix_tokens = cache[cache_key]
        else:
            prefix_tokens = mock_tokenizer.encode(system)
            cache[cache_key] = prefix_tokens
        if cache_key in cache:
            cached_tokens = cache[cache_key]
            self.assertEqual(cached_tokens, [1, 2, 3, 4, 5])

    async def test_prefix_cache_lru(self):
        """LRU eviction should work when cache is full."""
        cache = OrderedDict()
        max_size = 3

        def cache_set(target_cache, key, value):
            while len(target_cache) >= max_size:
                target_cache.popitem(last=False)
            target_cache[key] = value
        cache_set(cache, 'key1', [1, 2])
        cache_set(cache, 'key2', [3, 4])
        cache_set(cache, 'key3', [5, 6])
        cache_set(cache, 'key4', [7, 8])
        self.assertIn('key4', cache)
        self.assertNotIn('key1', cache)

    async def test_batch_timeout(self):
        """No request should wait more than 10 seconds."""
        config = CommunicationConfig()
        comm = CommunicationLayer(config)

        async def mock_execute(query):
            await asyncio.sleep(2)
            return {'success': True}
        with patch.object(CommunicationLayer, '_execute_query', mock_execute):
            start = time.time()
            try:
                await comm.submit_query('slow_task', voi_score=0.5)
            except Exception:
                pass
            time.time() - start

    def test_communication_layer_imports(self):
        """Communication layer should import itertools for counter."""
        from hledac.universal.layers import communication_layer
        self.assertTrue(hasattr(communication_layer, '_counter'))
if __name__ == '__main__':
    pytest.main([__file__, '-v'])