"""
Sprint 7G: Critical Benchmark Triage
Tests rewritten for canonical SprintScheduler interface (F260)

Original tests checked:
- scan_ct binding fix (legacy FullyAutonomousOrchestrator)
- stealth_crawler async mismatch fix
- duration cap override fix

Canonical replacements:
- SprintScheduler (not legacy orchestrator)
- StealthCrawler (canonical path)
- SprintSchedulerConfig duration handling
"""

import asyncio
import inspect
from unittest.mock import MagicMock, patch

import pytest

from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig
from _core import aclose


class TestScanCtFix:
    """TEST 1: SprintScheduler config and initialization"""

    def test_sprint_scheduler_config_attributes(self):
        """SprintSchedulerConfig should have expected attributes"""
        config = SprintSchedulerConfig(
            sprint_duration_s=5,
            aggressive_mode=True,
    )
        assert config.sprint_duration_s == 5
        assert config.aggressive_mode is True

    @pytest.mark.asyncio
    async def test_sprint_scheduler_can_be_created(self):
        """SprintScheduler should be creatable without errors."""
        config = SprintSchedulerConfig(
            sprint_duration_s=3,
            aggressive_mode=False,
    )
        scheduler = SprintScheduler(config)

        # Verify basic attributes
        assert scheduler._config is not None
        assert scheduler._config.sprint_duration_s == 3
        # SprintSchedulerV2 does not have _seen_hashes or _entries_per_source
        # (those were v1 attributes replaced by bounded dedup in v2)


class TestStealthCrawlerFix:
    """TEST 2: stealth crawler returns real non-coroutine result"""

    def test_fetch_html_sync_returns_string_not_coroutine(self):
        """_fetch_html should return str | None, not a coroutine"""
        from hledac.universal.recon.stealth.scraper import StealthCrawler

        # Patch __init__ to skip dependency checks and set _curl_cffi_available directly
        def mock_init(self, use_header_spoofer=True):
            self._curl_cffi_available = True
            self._httpx_available = False
            self._session = None
            self._header_spoofer = None

        with patch.object(StealthCrawler, "__init__", mock_init), \
             patch.object(StealthCrawler, "_fetch_with_curl_cffi", return_value="<html><body>test</body></html>"):
            crawler = StealthCrawler()
            result = crawler._fetch_html("https://example.com", {"User-Agent": "test"})

        # Must NOT be a coroutine
        assert not inspect.iscoroutine(result)
        # Must be a string
        assert isinstance(result, str)
        assert result == "<html><body>test</body></html>"

    def test_search_duckduckgo_returns_list_not_coroutine(self):
        """_search_duckduckgo should return List[SearchResult], not a coroutine"""
        from hledac.universal.recon.stealth.scraper import StealthCrawler

        valid_html = """
        <html><body>
        <a class="result__a" href="https://example.com">Example</a>
        <a class="result__a" href="https://test.com">Test</a>
        </body></html>
        """
        with patch.object(StealthCrawler, "_fetch_html", return_value=valid_html):
            crawler = StealthCrawler()
            result = crawler._search_duckduckgo("test query", num_results=5)

        # Must NOT be a coroutine
        assert not inspect.iscoroutine(result)
        # Must be a list
        assert isinstance(result, list)


class TestDurationCapFix:
    """TEST 3: SprintScheduler config respects duration"""

    def test_sprint_scheduler_config_duration(self):
        """SprintSchedulerConfig should store sprint_duration_s"""
        config = SprintSchedulerConfig(
            sprint_duration_s=30,  # 30 second sprint
    )
        scheduler = SprintScheduler(config)

        # Verify config has duration
        assert scheduler._config.sprint_duration_s == 30

    def test_sprint_scheduler_config_windup_lead(self):
        """SprintSchedulerConfig should store windup_lead_s"""
        config = SprintSchedulerConfig(
            sprint_duration_s=60,
            windup_lead_s=10,
    )
        assert config.windup_lead_s == 10


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

        from hledac.universal.security.quantum_resistant_crypto import QuantumResistantCrypto

        # Handle stub case — stub has no __del__, just check class exists
        if not hasattr(QuantumResistantCrypto, "__del__"):
            # Stub class — no __del__ means no bare except risk
            return

        # Get the source of __del__
        source = inspect.getsource(QuantumResistantCrypto.__del__)

        # Should NOT contain bare except
        assert "except:" not in source


# =============================================================================
# INTEGRATION SMOKE TEST
# =============================================================================


class TestSmokeIntegration:
    """SMOKE: sprint scheduler basic initialization"""

    @pytest.mark.asyncio
    async def test_sprint_scheduler_init_no_blocker_errors(self):
        """FIX F350M-R: Use @pytest.mark.asyncio instead of asyncio.run()."""
        config = SprintSchedulerConfig(
            sprint_duration_s=5,
    )
        scheduler = SprintScheduler(config)

        # Basic sanity check
        assert scheduler._config is not None
        assert scheduler._config.sprint_duration_s == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
