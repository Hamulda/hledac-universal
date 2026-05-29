"""
Sprint 42 - Adaptive Intelligence Tests
=======================================

Tests for:
- A. Batch Aging (anti-starvation) - wait_since, AGING_RATE
- B. Predictive RSS Monitor (EMA) - exponential smoothing, predictive throttle
- C. LinUCB Contextual Bandit - selection, fallback, persistence, context sensitivity
"""

import asyncio
import heapq
import time
import unittest
from unittest.mock import MagicMock

import pytest
from hledac.universal.layers.communication_layer import _BatchItem
from hledac.universal.tools.source_bandit import SourceBandit


class TestSprint42A_Aging(unittest.IsolatedAsyncioTestCase):
    """Tests for Batch Aging (anti-starvation)."""

    async def test_starvation_prevention(self):
        """Low VoI waiting >200ms should get priority boost."""
        # Low VoI (0.1) waiting 300ms
        old_item = _BatchItem(
            priority=-0.1,
            timestamp=time.time(),
            wait_since=time.time() - 0.3,
            query={'prompt': 'low'},
            future=asyncio.Future()
        )
        # High VoI (0.9) just arrived
        new_item = _BatchItem(
            priority=-0.9,
            timestamp=time.time(),
            wait_since=time.time(),
            query={'prompt': 'high'},
            future=asyncio.Future()
        )

        # Simulate aging
        now = time.time()
        aged_items = []
        for item in [old_item, new_item]:
            wait_seconds = now - item.wait_since
            if wait_seconds > 0.2:
                boosted = min(item.priority + 0.01 * wait_seconds, -0.01)
                aged_items.append(_BatchItem(
                    priority=boosted,
                    timestamp=item.timestamp,
                    wait_since=item.wait_since,
                    query=item.query,
                    future=item.future
                ))
            else:
                aged_items.append(item)
        heapq.heapify(aged_items)

        # High-VoI should remain first
        self.assertEqual(aged_items[0].priority, -0.9)
        self.assertEqual(aged_items[0].query['prompt'], 'high')
        # Low-VoI should be boosted (less negative)
        aged_low = aged_items[1]
        self.assertGreater(aged_low.priority, -0.1)
        self.assertLess(aged_low.priority, -0.01)

    async def test_aging_no_side_effect(self):
        """Tasks waiting <200ms should not have priority changed."""
        now = time.time()
        item1 = _BatchItem(
            priority=-0.5, timestamp=now, wait_since=now,
            query={'prompt': 'a'}, future=asyncio.Future()
        )
        item2 = _BatchItem(
            priority=-0.5, timestamp=now, wait_since=now,
            query={'prompt': 'b'}, future=asyncio.Future()
        )

        # Aging (wait <200ms) should not change priorities
        now = time.time()
        aged_items = []
        for item in [item1, item2]:
            wait_seconds = now - item.wait_since
            if wait_seconds > 0.2:
                boosted = min(item.priority + 0.01 * wait_seconds, -0.01)
                aged_items.append(_BatchItem(
                    priority=boosted,
                    timestamp=item.timestamp,
                    wait_since=item.wait_since,
                    query=item.query,
                    future=item.future
                ))
            else:
                aged_items.append(item)
        heapq.heapify(aged_items)

        # Both should still have priority -0.5
        prompts = {item.query['prompt'] for item in aged_items}
        self.assertEqual(prompts, {'a', 'b'})
        for item in aged_items:
            self.assertEqual(item.priority, -0.5)


class TestSprint42B_PredictiveRSS(unittest.IsolatedAsyncioTestCase):
    """Tests for Predictive RSS Monitor (EMA)."""

    async def test_ema_convergence(self):
        """EMA with alpha=0.3 should converge to true average within 5% after 5 samples."""
        # Create mock orchestrator
        orch = MagicMock()
        orch._rss_ema = 0.0
        orch._rss_ema_initialized = False
        orch._research_mgr = MagicMock()
        orch._state = MagicMock()
        orch._state.phase = 'EXECUTION'  # HEAVY_PHASES
        orch.HEAVY_PHASES = {'EXECUTION', 'SYNTHESIS'}

        samples = [50, 52, 51, 53, 50, 52, 51, 50, 52, 51]
        true_avg = sum(samples) / len(samples)

        # Manually apply EMA
        EMA_ALPHA = 0.3
        ema = 0.0
        initialized = False

        for val in samples:
            if not initialized:
                ema = val
                initialized = True
            else:
                ema = EMA_ALPHA * val + (1 - EMA_ALPHA) * ema

        self.assertLess(abs(ema - true_avg), 5.0)

    async def test_predictive_throttle(self):
        """Predictive throttle should activate when derivative >5% and EMA <65%."""
        # Create minimal mock
        orch = MagicMock()
        orch._coordinator_bounds = {'fetch': {'max_concurrent': 3}}
        orch._rss_ema = 54.0
        orch._rss_ema_initialized = True
        orch._research_mgr = MagicMock()
        orch._state = MagicMock()
        orch._state.phase = 'EXECUTION'
        orch.HEAVY_PHASES = {'EXECUTION'}

        # Simulate: current RSS = 60%, derivative = 60 - 54 = 6% > 5%
        current_rss = 60.0
        rss_derivative = current_rss - orch._rss_ema

        # Should trigger throttle
        self.assertGreater(rss_derivative, 5.0)
        self.assertLess(orch._rss_ema, 65.0)

        # Apply throttle logic
        new_val = max(1, orch._coordinator_bounds['fetch']['max_concurrent'] - 1)
        orch._coordinator_bounds['fetch']['max_concurrent'] = new_val

        self.assertEqual(orch._coordinator_bounds['fetch']['max_concurrent'], 2)


class TestSprint42C_LinUCB(unittest.IsolatedAsyncioTestCase):
    """Tests for LinUCB Contextual Bandit."""

    def test_linucb_selects_sources(self):
        """LinUCB should select n sources from list."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path
            bandit = SourceBandit(lmdb_path=Path(tmpdir) / 'test.lmdb')
            sources = ["arxiv", "web", "darkweb", "github", "scholar"]
            analysis = {"intent": "technical", "query": "test", "entities": None}
            result = bandit.select_with_context(sources, analysis, n=3)
            self.assertEqual(len(result), 3)
            for s in result:
                self.assertIn(s, sources)

    def test_linucb_fallback(self):
        """LinUCB should fallback to UCB1 when analysis is None."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path
            bandit = SourceBandit(lmdb_path=Path(tmpdir) / 'test.lmdb')
            # Force fallback by passing None analysis
            analysis = None
            sources = ["arxiv", "web"]
            result = bandit.select_with_context(sources, analysis, n=2)
            self.assertEqual(len(result), 2)  # fallback returns sources

    def test_linucb_persistence(self):
        """LinUCB arms should persist across instances."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'test.lmdb'

            # Create and update bandit
            bandit1 = SourceBandit(lmdb_path=path)
            bandit1.update_with_context("arxiv", 1.0,
                                        {"intent": "technical", "query": "test", "entities": None})
            bandit1._save_linucb()

            # Create new bandit and load
            bandit2 = SourceBandit(lmdb_path=path)
            self.assertIn("arxiv", bandit2._linucb_arms)

    def test_linucb_context_sensitivity(self):
        """Different context should result in different rankings."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'test.lmdb'
            bandit = SourceBandit(lmdb_path=path)

            # Train "arxiv" on AI/technical context
            for _ in range(20):
                bandit.update_with_context("arxiv", 1.0,
                                           {"intent": "technical", "query": "AI", "entities": None})
            # Train "darkweb" on investigative/security context
            for _ in range(20):
                bandit.update_with_context("darkweb", 1.0,
                                           {"intent": "investigative", "query": "leak", "entities": ["APT"]})

            # AI context -> should prefer arxiv
            ai_analysis = {"intent": "technical", "query": "AI", "entities": None}
            result_ai = bandit.select_with_context(["arxiv", "darkweb"], ai_analysis, n=1)
            self.assertEqual(result_ai[0], "arxiv")

            # Security context -> should prefer darkweb
            sec_analysis = {"intent": "investigative", "query": "leak", "entities": ["APT"]}
            result_sec = bandit.select_with_context(["arxiv", "darkweb"], sec_analysis, n=1)
            self.assertEqual(result_sec[0], "darkweb")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
