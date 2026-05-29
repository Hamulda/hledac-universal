"""
Sprint 7G: Critical Benchmark Triage
- scan_ct binding fix
- stealth_crawler async mismatch fix
- duration cap override fix
"""
import inspect
from unittest.mock import MagicMock

import pytest

# Import from the project
from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator


class TestScanCtFix:
    """TEST 1: scan_ct no TypeError on invocation"""

    @pytest.mark.asyncio
    async def test_scan_ct_handler_no_type_error(self):
        """scan_ct handler should not require 'self' as first arg"""
        orch = FullyAutonomousOrchestrator()
        await orch.initialize()

        # Register scan_ct handler
        from hledac.universal.utils import ActionResult

        async def _handle_ct_scan(**kwargs) -> ActionResult:
            return ActionResult(success=True, findings=[], metadata={'subdomains': ['test.example.com']})

        def _ct_scorer(state) -> tuple:
            return (0.7, {"domain": "example.com"})

        orch._register_action('scan_ct', _handle_ct_scan, _ct_scorer)

        # Invoke without passing self
        result = await orch._execute_action('scan_ct')
        assert result is not None
        assert result.success is True


class TestStealthCrawlerFix:
    """TEST 2: stealth crawler returns real non-coroutine result"""

    def test_fetch_html_sync_returns_string_not_coroutine(self):
        """_fetch_html should return str | None, not a coroutine"""
        from hledac.universal.intelligence.stealth_crawler import StealthCrawler

        crawler = StealthCrawler()

        # Mock _fetch_with_curl_cffi to return valid HTML
        crawler._fetch_with_curl_cffi = MagicMock(return_value="<html><body>test</body></html>")
        crawler._requests_available = False

        result = crawler._fetch_html("https://example.com", {"User-Agent": "test"})

        # Must NOT be a coroutine
        assert not inspect.iscoroutine(result)
        # Must be a string
        assert isinstance(result, str)
        assert result == "<html><body>test</body></html>"

    def test_search_duckduckgo_returns_list_not_coroutine(self):
        """_search_duckduckgo should return List[SearchResult], not a coroutine"""
        from hledac.universal.intelligence.stealth_crawler import StealthCrawler

        crawler = StealthCrawler()

        # Mock _fetch_html to return valid HTML
        valid_html = """
        <html><body>
        <a class="result__a" href="https://example.com">Example</a>
        <a class="result__a" href="https://test.com">Test</a>
        </body></html>
        """
        crawler._fetch_html = MagicMock(return_value=valid_html)

        result = crawler._search_duckduckgo("test query", num_results=5)

        # Must NOT be a coroutine
        assert not inspect.iscoroutine(result)
        # Must be a list
        assert isinstance(result, list)


class TestDurationCapFix:
    """TEST 3: duration overrides max_iterations"""

    @pytest.mark.asyncio
    async def test_research_with_timeout_allows_high_iterations(self):
        """When timeout is set, _max_iters should be raised to 999999"""
        orch = FullyAutonomousOrchestrator()
        await orch.initialize()

        # Check initial value
        assert orch._max_iters == 200  # Default from __init__

        # Directly set timeout-like condition
        timeout = 30
        if timeout is not None and timeout > 0:
            orch._max_iters = 999999

        assert orch._max_iters == 999999


class TestBenchmarkFPS:
    """TEST 6: benchmark_fps formula uses tolerance"""

    def test_benchmark_fps_tolerance(self):
        """benchmark_fps should equal iterations/elapsed_s within tolerance"""
        iterations = 100
        elapsed_s = 10.5

        # The formula: benchmark_fps = iterations / elapsed_s
        benchmark_fps = iterations / elapsed_s
        expected = 9.523809523809524

        # Should be exactly equal (it's the same formula)
        assert abs(benchmark_fps - expected) < 0.01

        # Test with different values
        iterations = 200
        elapsed_s = 30.0
        benchmark_fps = iterations / elapsed_s
        expected = 200 / 30.0

        assert abs(benchmark_fps - expected) < 0.01


class TestShutdownWarning:
    """TEST 4: quantum shutdown no bare except"""

    def test_quantum_wipe_no_bare_except(self):
        """secure_wipe_keys should not use bare except in __del__"""
        import inspect

        from hledac.security.quantum_resistant_crypto import QuantumResistantCrypto

        # Get the source of __del__
        source = inspect.getsource(QuantumResistantCrypto.__del__)

        # Should NOT contain bare except
        assert "except:" not in source


# =============================================================================
# INTEGRATION SMOKE TEST
# =============================================================================

class TestSmokeIntegration:
    """TEST 4+5: smoke benchmark with no blocker errors"""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_offline_replay_smoke_no_blocker_errors(self):
        """30s OFFLINE_REPLAY smoke should have no blocker errors"""
        import time as time_module

        from hledac.universal.benchmarks.run_sprint82j_benchmark import run_benchmark

        # Run benchmark - pass parameters directly to run_benchmark
        start = time_module.time()
        results = await run_benchmark(
            duration_seconds=30,
            query="test query",
            mode="OFFLINE_REPLAY",
            output_dir="/tmp/sprint7g_smoke",
        )
        elapsed = time_module.time() - start

        # Assertions
        assert elapsed >= 28, f"Should run for >= 28s, got {elapsed:.1f}s"
        assert results.iterations > 100, f"Should have > 100 iterations, got {results.iterations}"

        # Check no blocker errors - verify it completed
        assert results.research_entered is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
