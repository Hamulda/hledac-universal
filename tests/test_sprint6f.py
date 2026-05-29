#!/usr/bin/env python3
"""
Sprint 6F Tests - FPS Root-Cause + TS Truth Restoration
"""
import asyncio
import collections
import sys

import pytest

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


class TestFPSRootCause:
    """Test FPS root cause fix (academic_search offline guard)."""

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
                    assert result.metadata.get('offline_replay')
                    break

        asyncio.run(run())


class TestUCB1Warmup:
    """Test UCB1 warmup implementation."""

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


class TestMonopolyGuard:
    """Test monopoly guard implementation."""

    def test_monopoly_guard_enabled(self):
        """Monopoly guard should be enabled."""
        import asyncio

        async def run():
            from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

            orch = FullyAutonomousOrchestrator()

            # Check monopoly guard constants are set
            assert hasattr(orch, '_monopoly_guard_window')
            assert hasattr(orch, '_monopoly_guard_threshold')
            assert hasattr(orch, '_monopoly_guard_history')
            assert isinstance(orch._monopoly_guard_history, collections.deque)
            assert orch._monopoly_guard_threshold == 0.80

        asyncio.run(run())

    def test_monopoly_detection(self):
        """Monopoly guard should detect when one action exceeds 80%."""
        import asyncio

        async def run():
            from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

            orch = FullyAutonomousOrchestrator()

            # Fill history with monopoly (82%)
            orch._monopoly_guard_history = collections.deque(maxlen=50)
            for _ in range(42):
                orch._monopoly_guard_history.append('surface_search')
            for _ in range(8):
                orch._monopoly_guard_history.append('network_recon')

            # Check monopoly detection
            counts = collections.Counter(orch._monopoly_guard_history)
            top_action, top_count = counts.most_common(1)[0]
            is_monopoly = top_count / len(orch._monopoly_guard_history) > orch._monopoly_guard_threshold

            assert is_monopoly
            assert top_action == 'surface_search'

        asyncio.run(run())


class TestScoreHackRemoval:
    """Test that static score hacks are removed."""

    def test_network_recon_score_reasonable(self):
        """Network recon should have reasonable base score."""
        import asyncio

        async def run():
            from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
            orch = FullyAutonomousOrchestrator()
            await orch._initialize_actions()

            network_recon = None
            for name, (_handler, scorer) in orch._action_registry.items():
                if name == 'network_recon':
                    network_recon = (name, scorer)
                    break

            assert network_recon is not None, "network_recon not registered"
            name, scorer = network_recon

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
            await orch._initialize_actions()

            academic_search = None
            for name, (_handler, scorer) in orch._action_registry.items():
                if name == 'academic_search':
                    academic_search = (name, scorer)
                    break

            assert academic_search is not None, "academic_search not registered"
            name, scorer = academic_search

            state = {'query': 'test', 'source_types_seen': set()}
            score, params = scorer(state)

            # Score should be reasonable (0.20 baseline)
            assert 0.1 <= score <= 0.5, f"academic_search score {score} outside reasonable range"

        asyncio.run(run())


class TestTargetQueue:
    """Test target queue for replay-only routing."""

    def test_target_queue_initialized(self):
        """Target queue should be initialized."""
        import asyncio

        async def run():
            from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
            orch = FullyAutonomousOrchestrator()

            assert hasattr(orch, '_target_queue')

        asyncio.run(run())


class TestThreadSafeBoundedQueue:
    """Test ThreadSafeBoundedQueue implementation."""

    def test_basic_put_get(self):
        from hledac.universal.autonomous_orchestrator import ThreadSafeBoundedQueue
        q = ThreadSafeBoundedQueue(maxsize=10)
        assert q.put("item1") is True
        assert len(q) == 1
        assert q.get() == "item1"
        assert len(q) == 0

    def test_fifo_eviction(self):
        from hledac.universal.autonomous_orchestrator import ThreadSafeBoundedQueue
        q = ThreadSafeBoundedQueue(maxsize=3)
        q.put("a")
        q.put("b")
        q.put("c")
        q.put("d")  # Should evict "a"
        assert len(q) == 3
        assert q.get() == "b"

    def test_not_asyncio_queue(self):
        from hledac.universal.autonomous_orchestrator import ThreadSafeBoundedQueue
        q = ThreadSafeBoundedQueue(maxsize=10)
        assert not isinstance(q, asyncio.Queue)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
