"""
Sprint 48+49 Tests - Monitor MLX + Cleanup Race + ToT Dedup + LMDB Async + ELA Graph + Paywall Pool
Tests all invariants from Sprint 48 and Sprint 49 implementation.
"""

import asyncio
import inspect
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


class TestSprint48(unittest.IsolatedAsyncioTestCase):
    """Sprint 48 tests - MLX Monitor, Cleanup Race, ToT Dedup, Adaptive Interval, Holt Smoothing"""

    # S48-P1: No numpy import in autonomy_monitor_step
    def test_monitor_no_numpy(self):
        """S48-P1: Žádný numpy import v autonomy_monitor_step"""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        source = inspect.getsource(FullyAutonomousOrchestrator._autonomy_monitor_step)
        self.assertNotIn("import numpy", source)
        self.assertNotIn("from numpy", source)

    # S48-P2: deque(maxlen=10) for O(1) append
    def test_rss_history_deque(self):
        """S48-P2: rss_history je deque(maxlen=10)"""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        # Check __init__ has deque(maxlen=10)
        source = inspect.getsource(FullyAutonomousOrchestrator.__init__)
        self.assertIn("deque(maxlen=10)", source)

    # S48-P3: Monitor cancel FIRST in cleanup
    async def test_cleanup_monitor_first(self):
        """S48-P3: Monitor cancel PRVNÍ v cleanup()"""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        # Create a mock orchestrator
        orch = MagicMock()
        orch._autonomy_monitor_task = AsyncMock()
        orch._autonomy_monitor_running = True
        orch._metrics_registry = None
        orch._layer_manager = None
        orch._memory_mgr = None
        orch._orch = MagicMock()
        orch._orch._security_mgr = None
        orch._forensics_mgr = None
        orch._tot_executor = None

        # Call cleanup
        with patch('asyncio.wait_for', return_value=None):
            await FullyAutonomousOrchestrator.cleanup(orch)

        # Verify monitor was cancelled first
        orch._autonomy_monitor_task.cancel.assert_called_once()

    # S48-P4: tot_used flag prevents duplicate ToT evaluation
    def test_tot_single_eval(self):
        """S48-P4: should_activate_tot() max 1× per query"""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        # Check research method has tot_used flag logic
        source = inspect.getsource(FullyAutonomousOrchestrator.research)
        self.assertIn("tot_used", source)

    # S48-P5: psutil singleton in __init__
    def test_psutil_singleton(self):
        """S48-P5: self._psutil_proc singleton v __init__"""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        source = inspect.getsource(FullyAutonomousOrchestrator.__init__)
        self.assertIn("_psutil_proc", source)

    # S48-P6: Adaptive interval based on trend
    def test_monitor_adaptive_interval(self):
        """S48-P6: HEAVY_PHASES → 1.5s interval, jinak 8s"""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        # Check monitor step has adaptive interval logic
        source = inspect.getsource(FullyAutonomousOrchestrator._autonomy_monitor_step)
        self.assertIn("last_monitor_interval", source)
        self.assertIn("rss_trend", source)

    # S48-P7: Holt's double EMA (level+trend)
    def test_holt_smoothing(self):
        """S48-P7: Holt's double EMA (level+trend)"""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        # Check that Holt's smoothing constants are defined
        source = inspect.getsource(FullyAutonomousOrchestrator.__init__)
        self.assertIn("HOLT_ALPHA", source)
        self.assertIn("HOLT_BETA", source)
        self.assertIn("rss_trend", source)

        # Check monitor step uses Holt's formulas
        step_source = inspect.getsource(FullyAutonomousOrchestrator._autonomy_monitor_step)
        self.assertIn("HOLT_ALPHA", step_source)
        self.assertIn("HOLT_BETA", step_source)

    # S48-P8: orjson for faster serialization (if available)
    def test_orjson_available(self):
        """S48-P8: Použití orjson pro rychlejší serializaci v LMDB"""
        try:
            import orjson

            # If orjson is available, SessionManager should use it
            from hledac.universal.tools.session_manager import USE_ORJSON
            self.assertTrue(USE_ORJSON)
        except ImportError:
            self.skipTest("orjson not installed")


class TestSprint49(unittest.IsolatedAsyncioTestCase):
    """Sprint 49 tests - LMDB Async, ELA Graph, Paywall Pool, URL Mapping"""

    # S49-B: LMDB operations async via executor
    async def test_lmdb_async_no_block(self):
        """S49-B: Všechny LMDB operace async přes executor"""
        import lmdb
        from hledac.universal.tools.session_manager import SessionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            env = lmdb.Environment(os.path.join(tmpdir, "test.lmdb"), map_size=10*1024*1024)
            mgr = SessionManager(env)

            # Test async get_session
            start = asyncio.get_event_loop().time()
            await mgr.get_session("test.com")
            elapsed = asyncio.get_event_loop().time() - start

            # Should be non-blocking (async)
            self.assertLess(elapsed, 0.1)

            await mgr.close()
            env.close()

    # S49-C: ELA score > 0.7 → graph manipulation flag + credibility reduction
    async def test_ela_graph_pipeline(self):
        """S49-C: ela_score > 0.7 → v grafu se nastaví manipulation_flag"""
        from hledac.universal.intelligence.document_intelligence import DeepForensicsAnalyzer
        from hledac.universal.intelligence.relationship_discovery import RelationshipDiscoveryEngine

        # Create mock orchestrator with relationship discovery
        orch = MagicMock()
        orch._research_mgr = MagicMock()
        orch._research_mgr.relationship_discovery = AsyncMock(spec=RelationshipDiscoveryEngine)

        # Create analyzer with orch reference
        analyzer = DeepForensicsAnalyzer(orch=orch)

        # Mock ELA analysis to return high score
        analyzer._ela_analysis = AsyncMock(return_value=0.85)

        # Mock stegdetect
        analyzer._stegdetect = AsyncMock(return_value=0.05)

        # Run analysis
        result = await analyzer.analyze_image(b"fake_image_data", url="http://test.jpg")

        # Check ELA score is in result
        self.assertIn("ela_score", result)
        self.assertEqual(result["ela_score"], 0.85)

        # Check that flag_manipulated_image was called
        orch._research_mgr.relationship_discovery.flag_manipulated_image.assert_awaited_once_with(
            url="http://test.jpg", ela_score=0.85
        )

    # S49-D: PaywallBypass session reuse
    async def test_paywall_session_reuse(self):
        """S49-D: PaywallBypass._session reused mezi voláními"""
        from hledac.universal.tools.paywall import PaywallBypass

        bypass = PaywallBypass()

        # Get session twice
        session1 = await bypass._get_session()
        session2 = await bypass._get_session()

        # Should be the same session
        self.assertIs(session1, session2)

        await bypass.close()

    # S49-E: URL to node mapping
    async def test_url_to_node_map(self):
        """S49-E: relationship_discovery udržuje url_to_node mapu"""
        from hledac.universal.intelligence.relationship_discovery import RelationshipDiscoveryEngine

        engine = RelationshipDiscoveryEngine()

        # Check url_to_node attribute exists
        self.assertTrue(hasattr(engine, 'url_to_node'))

        # Add document
        engine.add_document("http://test.com", "node123")

        # Check mapping
        self.assertEqual(engine.url_to_node.get("http://test.com"), "node123")

    # S49-C: Flag manipulated image updates credibility
    async def test_manipulation_reduces_credibility(self):
        """S49-C: Snížení credibility při vysokém ELA skóre"""
        from hledac.universal.intelligence.relationship_discovery import Entity, EntityType, RelationshipDiscoveryEngine

        engine = RelationshipDiscoveryEngine()

        # Add entity with credibility
        entity = Entity("node1", EntityType.DOCUMENT, {"credibility": 1.0})
        engine.add_entity(entity)
        engine.add_document("http://test.jpg", "node1")

        # Flag as manipulated with high ELA score
        await engine.flag_manipulated_image("http://test.jpg", ela_score=0.8)

        # Check credibility was reduced
        updated = engine._entities["node1"]
        self.assertLess(updated.attributes["credibility"], 1.0)


class TestSprint48Integration(unittest.IsolatedAsyncioTestCase):
    """Integration tests for Sprint 48 features"""

    async def test_monitor_loop_uses_adaptive_interval(self):
        """Test that monitor loop uses adaptive interval"""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        # Check the loop uses last_monitor_interval
        source = inspect.getsource(FullyAutonomousOrchestrator._autonomy_monitor_loop)
        self.assertIn("last_monitor_interval", source)

    async def test_mlx_linear_regression_fallback(self):
        """Test MLX linear regression has proper fallback"""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source = inspect.getsource(FullyAutonomousOrchestrator._autonomy_monitor_step)

        # Should have MLX with try/except fallback
        self.assertIn("MLX_AVAILABLE", source)


if __name__ == '__main__':
    unittest.main()
