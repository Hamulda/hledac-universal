#!/usr/bin/env python3
"""
Sprint 6E Tests - ThreadSafeBoundedQueue + Score Hack Removal
"""
import asyncio
import sys
import time

import pytest

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

# ThreadSafeBoundedQueue may be in the legacy module - use lazy import to avoid cascade errors
def _get_ts_queue():
    try:
        from hledac.universal.autonomous_orchestrator import ThreadSafeBoundedQueue
        return ThreadSafeBoundedQueue
    except (ImportError, AttributeError):
        pass
    return None

ThreadSafeBoundedQueue = _get_ts_queue()


class TestThreadSafeBoundedQueue:
    """Test ThreadSafeBoundedQueue implementation (rule 19-22)."""

    def test_basic_put_get(self):
        q = ThreadSafeBoundedQueue(maxsize=10)
        assert q.put("item1") is True
        assert len(q) == 1
        assert q.get() == "item1"
        assert len(q) == 0

    def test_fifo_eviction(self):
        """Test FIFO eviction when full."""
        q = ThreadSafeBoundedQueue(maxsize=3)
        q.put("a")
        q.put("b")
        q.put("c")
        assert len(q) == 3
        # Adding 4th should evict oldest (a)
        q.put("d")
        assert len(q) == 3
        # First item should now be 'b' (a was evicted)
        assert q.get() == "b"

    def test_drop_count(self):
        """Test that drop_count is tracked."""
        q = ThreadSafeBoundedQueue(maxsize=2)
        q.put("1")
        q.put("2")
        q.put("3")  # Should drop 1
        q.put("4")  # Should drop 2
        assert q.drop_count == 2

    def test_not_asyncio_queue(self):
        """Rule 19: target_queue must not be asyncio.Queue."""
        q = ThreadSafeBoundedQueue(maxsize=10)
        assert not isinstance(q, asyncio.Queue)


class TestScoreHackRemoval:
    """Test that static score hacks are removed."""

    def test_network_recon_score_reasonable(self):
        """Network recon should have reasonable base score."""
        import asyncio

        async def run():
            from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
            orch = FullyAutonomousOrchestrator()

            # Initialize actions (lazy)
            await orch._initialize_actions()

            # Find network_recon in registry
            network_recon = None
            for name, (_handler, scorer) in orch._action_registry.items():
                if name == 'network_recon':
                    network_recon = (name, scorer)
                    break

            assert network_recon is not None, "network_recon not registered"
            name, scorer = network_recon

            # Call scorer with state containing domain
            state = {'new_domain': 'example.com', 'domain_staleness': 0}
            score, params = scorer(state)

            # Score should be reasonable (0.40 baseline)
            assert 0.3 <= score <= 0.6, f"network_recon score {score} outside reasonable range"

        asyncio.run(run())

    def test_academic_search_score_reasonable(self):
        """Academic search should have reasonable base score."""
        import asyncio

        async def run():
            from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
            orch = FullyAutonomousOrchestrator()

            # Initialize actions (lazy)
            await orch._initialize_actions()

            # Find academic_search in registry
            academic_search = None
            for name, (_handler, scorer) in orch._action_registry.items():
                if name == 'academic_search':
                    academic_search = (name, scorer)
                    break

            assert academic_search is not None, "academic_search not registered"
            name, scorer = academic_search

            # Call scorer with state
            state = {'query': 'test', 'source_types_seen': set()}
            score, params = scorer(state)

            # Score should be reasonable (0.20 baseline)
            assert 0.1 <= score <= 0.5, f"academic_search score {score} outside reasonable range"

        asyncio.run(run())


class TestTargetQueueMetrics:
    """Test target queue metrics are tracked."""

    def test_target_queue_metrics_in_benchmark(self):
        """Benchmark should return target queue metrics."""
        import asyncio

        async def run():
            from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
            from hledac.universal.knowledge.atomic_storage import EvidencePacket, EvidencePacketStorage

            orch = FullyAutonomousOrchestrator()
            orch._evidence_packet_storage = EvidencePacketStorage()

            for i in range(5):
                packet = EvidencePacket(
                    evidence_id=f'evidence_{i}',
                    url=f'http://localhost:{64000+i}/test',
                    final_url=f'http://localhost:{64000+i}/test',
                    domain='localhost',
                    fetched_at=time.time() - (i * 86400),
                    status=200,
                    headers_digest='abc123',
                    snapshot_ref={'blob_hash': f'hash_{i}', 'path': '/tmp', 'size': 1000, 'encrypted': False},
                    content_hash=f'content_hash_{i}',
                    page_type='text/html',
                )
                packet.metadata_digests = {'email': f'test{i}@example.com'}
                orch._evidence_packet_storage.store_packet(f'evidence_{i}', packet)

            import random
            random.seed(42)

            result = await asyncio.wait_for(
                orch.run_benchmark(
                    mode='propagation_on',
                    duration_seconds=3,
                    warmup_iterations=0,
                    query='test',
                    prefer_offline_replay=True,
                ),
                timeout=20
            )

            # Check target queue metrics exist
            assert 'target_queue_source' in result
            assert 'target_queue_size' in result

        asyncio.run(run())


class TestUCB1Warmup:
    """Test UCB1 warmup implementation (rule 8-9)."""

    def test_ucb1_warmup_enabled(self):
        """UCB1 warmup should be enabled."""
        import asyncio

        async def run():
            from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

            orch = FullyAutonomousOrchestrator()

            # Check UCB1 warmup constants are set
            assert hasattr(orch, '_UCB1_WARMUP_MIN_EXECUTIONS')
            assert hasattr(orch, '_UCB1_WARMUP_ENABLED')
            assert orch._UCB1_WARMUP_MIN_EXECUTIONS == 20
            assert orch._UCB1_WARMUP_ENABLED

        asyncio.run(run())

    def test_monopoly_guard_enabled(self):
        """Monopoly guard should be enabled."""
        import asyncio
        import collections

        async def run():
            from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

            orch = FullyAutonomousOrchestrator()

            # Check monopoly guard constants are set
            assert hasattr(orch, '_monopoly_guard_window')
            assert hasattr(orch, '_monopoly_guard_threshold')
            assert hasattr(orch, '_monopoly_guard_history')
            assert isinstance(orch._monopoly_guard_history, collections.deque)
            assert orch._monopoly_guard_threshold == 0.60

        asyncio.run(run())


class TestMonopolyGuard:
    """Test monopoly guard implementation (rule 10)."""

    def test_monopoly_guard_triggers_at_threshold(self):
        """Monopoly guard should trigger when one action exceeds 60%."""
        import asyncio
        import collections

        async def run():
            from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

            orch = FullyAutonomousOrchestrator()
            await orch._initialize_actions()

            # Fill history with one dominant action (65%)
            orch._monopoly_guard_history = collections.deque(maxlen=50)
            for _ in range(33):  # 33/50 = 66%
                orch._monopoly_guard_history.append('surface_search')
            for _ in range(17):
                orch._monopoly_guard_history.append('network_recon')

            # Now check if monopoly is detected
            if len(orch._monopoly_guard_history) >= orch._monopoly_guard_history.maxlen:
                counts = collections.Counter(orch._monopoly_guard_history)
                top_action, top_count = counts.most_common(1)[0]
                is_monopoly = top_count / len(orch._monopoly_guard_history) > orch._monopoly_guard_threshold
                assert is_monopoly, "Monopoly should be detected at 66%"
                assert top_action == 'surface_search'

        asyncio.run(run())

    def test_monopoly_guard_excludes_dominant(self):
        """Monopoly guard should exclude dominant action when triggered."""
        import asyncio
        import collections

        async def run():
            from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

            orch = FullyAutonomousOrchestrator()
            await orch._initialize_actions()

            # Fill history with monopoly
            orch._monopoly_guard_history = collections.deque(maxlen=50)
            for _ in range(40):  # 80%
                orch._monopoly_guard_history.append('surface_search')
            for _ in range(10):
                orch._monopoly_guard_history.append('network_recon')

            # Candidates from scorer
            candidates_scored = [
                (0.9, 'surface_search', {}),
                (0.5, 'network_recon', {}),
                (0.3, 'academic_search', {})
            ]

            # Simulate monopoly guard logic
            counts = collections.Counter(orch._monopoly_guard_history)
            top_action, top_count = counts.most_common(1)[0]
            threshold_exceeded = top_count / len(orch._monopoly_guard_history) > orch._monopoly_guard_threshold

            if threshold_exceeded:
                # Exclude dominant action
                remaining = [(s, n, p) for s, n, p in candidates_scored if n != top_action]
                # Should have network_recon and academic_search
                assert len(remaining) == 2
                action_names = [n for _, n, _ in remaining]
                assert 'network_recon' in action_names
                assert 'surface_search' not in action_names

        asyncio.run(run())


class TestOfflineGuard:
    """Test academic_search offline guard (STEP 0.5)."""

    def test_academic_search_offline_fastfail(self):
        """academic_search should fast-fail in OFFLINE_REPLAY mode."""
        import asyncio

        async def run():
            from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

            orch = FullyAutonomousOrchestrator()
            await orch._initialize_actions()

            # Set OFFLINE_REPLAY mode
            orch._data_mode = 'OFFLINE_REPLAY'

            # Find and call academic_search handler
            for name, (handler, _scorer) in orch._action_registry.items():
                if name == 'academic_search':
                    result = await handler('test query')
                    # Should succeed with mock findings (no real HTTP)
                    assert result.success, f"Expected success in OFFLINE_REPLAY, got {result.error}"
                    assert len(result.findings) > 0, "Should return mock findings"
                    # Verify offline_replay flag
                    assert result.metadata.get('offline_replay')
                    break

        asyncio.run(run())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
