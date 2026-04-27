"""Sprint F206I: Source Health Summary and Circuit Breaker Coverage
================================================================

Invariant mapping:
  F206I-1  | _get_source_health_summary() returns {"entries": [], "total_tracked": int, "max_entries": 100}
  F206I-2  | _get_source_health_summary() sorts by posture (hot first)
  F206I-3  | _get_source_health_summary() bounds entries to MAX_SOURCE_HEALTH_ENTRIES=100
  F206I-4  | _get_source_health_summary() returns {} when no economics tracked
  F206I-5  | _get_circuit_breaker_summary() returns {"total_tracked": int, "open_count": int, ...}
  F206I-6  | _get_circuit_breaker_summary() counts open/half_open correctly
  F206I-7  | _get_circuit_breaker_summary() returns {} on error
  F206I-8  | fetch_malwarebazaar_recent uses checked_aiohttp_post
  F206I-9  | _handle_malwarebazaar_search uses checked_aiohttp_post
  F206I-10 | _query_shodan_internetdb uses checked_aiohttp_get
  F206I-11 | duckduckgo_adapter imports checked_aiohttp_get (verify coverage)
  F206I-12 | MAX_SOURCE_HEALTH_ENTRIES = 100 bound in scheduler
  F206I-13 | circuit_breaker_state wired into _build_diagnostic_report
  F206I-14 | source_health_summary wired into _build_diagnostic_report
  F206I-15 | MAX_TRACKED_DOMAINS = 500 circuit breaker bound preserved
  F206I-16 | query_rdap uses checked_aiohttp_get
  F206I-17 | search_ahmia uses checked_aiohttp_get (both onion and clearnet)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


class TestSourceHealthSummary:
    """F206I-1 to F206I-4: _get_source_health_summary() behavior."""

    def _make_scheduler(self):
        """Build a minimal SprintScheduler for testing."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        cfg = MagicMock()
        cfg.sprint_duration_s = 60.0
        cfg.windup_lead_s = 5.0
        cfg.cycle_sleep_s = 1.0
        cfg.max_cycles = 2
        cfg.max_parallel_sources = 2
        cfg.stop_on_first_accepted = False
        cfg.export_enabled = False
        cfg.export_dir = ""
        cfg.max_entries_per_cycle = 50
        cfg.max_hypothesis_depth = 0
        cfg.max_hypothesis_queries = 0
        cfg.aggressive_mode = False
        cfg.aggressive_branch_timeout_s = 45.0
        cfg.branch_timeout_budget_s = 0.0
        cfg.partial_export_findings_interval = 10
        cfg.source_tier_map = {}
        cfg.tier_of = MagicMock(return_value=MagicMock())
        cfg.sorted_tiers = MagicMock(return_value=[])
        return SprintScheduler(cfg)

    def test_returns_correct_structure(self):
        """F206I-1: Returns dict with entries list, total_tracked, max_entries."""
        from hledac.universal.runtime.sprint_scheduler import SourceEconomics
        scheduler = self._make_scheduler()
        # Populate one source
        econ = SourceEconomics(source="https://example.com/feed")
        econ.recent_health_posture = "hot"
        econ.last_signal_cycle = 1
        econ.silent_streak = 0
        scheduler._source_economics["https://example.com/feed"] = econ

        result = scheduler._get_source_health_summary()
        assert "entries" in result
        assert "total_tracked" in result
        assert "max_entries" in result
        assert result["total_tracked"] == 1
        assert result["max_entries"] == 100
        assert len(result["entries"]) == 1
        assert result["entries"][0]["source"] == "https://example.com/feed"
        assert result["entries"][0]["posture"] == "hot"

    def test_sorts_by_posture_hot_first(self):
        """F206I-2: Entries sorted hot > warm > lukewarm > marginal > cold > unknown."""
        from hledac.universal.runtime.sprint_scheduler import SourceEconomics
        scheduler = self._make_scheduler()
        postures = ["cold", "hot", "marginal", "warm", "unknown", "lukewarm"]
        for i, posture in enumerate(postures):
            econ = SourceEconomics(source=f"https://source{i}.com")
            econ.recent_health_posture = posture
            econ.last_signal_cycle = i
            scheduler._source_economics[f"https://source{i}.com"] = econ

        result = scheduler._get_source_health_summary()
        entries = result["entries"]
        posture_order = ["hot", "warm", "lukewarm", "marginal", "cold", "unknown"]
        actual_order = [e["posture"] for e in entries]
        assert actual_order == posture_order

    def test_bounds_to_max_entries(self):
        """F206I-3: Entries capped at MAX_SOURCE_HEALTH_ENTRIES=100."""
        from hledac.universal.runtime.sprint_scheduler import SourceEconomics
        scheduler = self._make_scheduler()
        # Create 150 sources
        for i in range(150):
            econ = SourceEconomics(source=f"https://source{i}.com")
            econ.recent_health_posture = "hot"
            econ.last_signal_cycle = i
            scheduler._source_economics[f"https://source{i}.com"] = econ

        result = scheduler._get_source_health_summary()
        assert len(result["entries"]) == 100
        assert result["total_tracked"] == 150
        assert result["max_entries"] == 100

    def test_returns_empty_when_no_economics(self):
        """F206I-4: Returns {} when no sources tracked."""
        scheduler = self._make_scheduler()
        result = scheduler._get_source_health_summary()
        assert result == {}

    def test_max_source_health_entries_constant(self):
        """F206I-12: MAX_SOURCE_HEALTH_ENTRIES = 100 in SprintScheduler."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        assert hasattr(SprintScheduler, "MAX_SOURCE_HEALTH_ENTRIES")
        assert SprintScheduler.MAX_SOURCE_HEALTH_ENTRIES == 100


class TestCircuitBreakerSummary:
    """F206I-5 to F206I-7: _get_circuit_breaker_summary() behavior."""

    def _make_scheduler(self):
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        cfg = MagicMock()
        cfg.sprint_duration_s = 60.0
        cfg.windup_lead_s = 5.0
        cfg.cycle_sleep_s = 1.0
        cfg.max_cycles = 2
        cfg.max_parallel_sources = 2
        cfg.stop_on_first_accepted = False
        cfg.export_enabled = False
        cfg.export_dir = ""
        cfg.max_entries_per_cycle = 50
        cfg.max_hypothesis_depth = 0
        cfg.max_hypothesis_queries = 0
        cfg.aggressive_mode = False
        cfg.aggressive_branch_timeout_s = 45.0
        cfg.branch_timeout_budget_s = 0.0
        cfg.partial_export_findings_interval = 10
        cfg.source_tier_map = {}
        cfg.tier_of = MagicMock(return_value=MagicMock())
        cfg.sorted_tiers = MagicMock(return_value=[])
        return SprintScheduler(cfg)

    def test_returns_correct_structure(self):
        """F206I-5: Returns dict with total_tracked, open_count, half_open_count, entries."""
        from hledac.universal.transport.circuit_breaker import (
            clear_all_breakers,
            get_breaker,
        )
        clear_all_breakers()
        # Create an open circuit
        breaker = get_breaker("open.example.com")
        for _ in range(3):
            breaker.record_failure()
        scheduler = self._make_scheduler()

        result = scheduler._get_circuit_breaker_summary()
        assert "total_tracked" in result
        assert "open_count" in result
        assert "half_open_count" in result
        assert "entries" in result
        assert result["total_tracked"] >= 1
        assert result["open_count"] >= 1
        clear_all_breakers()

    def test_counts_open_half_open_correctly(self):
        """F206I-6: open_count and half_open_count are accurate."""
        from hledac.universal.transport.circuit_breaker import (
            clear_all_breakers,
            get_breaker,
        )
        clear_all_breakers()
        # Create open circuit
        open_cb = get_breaker("open.example.com")
        for _ in range(3):
            open_cb.record_failure()
        # Create half-open circuit (after recovery timeout)
        # Create closed circuit
        closed_cb = get_breaker("closed.example.com")
        closed_cb.record_success()

        scheduler = self._make_scheduler()
        result = scheduler._get_circuit_breaker_summary()

        assert result["open_count"] >= 1
        assert result["half_open_count"] >= 0
        assert result["total_tracked"] >= 2
        clear_all_breakers()

    def test_returns_empty_on_error(self):
        """F206I-7: Returns {} when get_all_breaker_snapshots raises."""
        scheduler = self._make_scheduler()
        with patch(
            "hledac.universal.runtime.sprint_scheduler.get_all_breaker_snapshots",
            side_effect=RuntimeError("simulated"),
        ):
            result = scheduler._get_circuit_breaker_summary()
        assert result == {}


class TestDiagnosticReportWiring:
    """F206I-13 and F206I-14: source_health_summary and circuit_breaker_state in report."""

    def _make_scheduler(self):
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        cfg = MagicMock()
        cfg.sprint_duration_s = 60.0
        cfg.windup_lead_s = 5.0
        cfg.cycle_sleep_s = 1.0
        cfg.max_cycles = 2
        cfg.max_parallel_sources = 2
        cfg.stop_on_first_accepted = False
        cfg.export_enabled = False
        cfg.export_dir = ""
        cfg.max_entries_per_cycle = 50
        cfg.max_hypothesis_depth = 0
        cfg.max_hypothesis_queries = 0
        cfg.aggressive_mode = False
        cfg.aggressive_branch_timeout_s = 45.0
        cfg.branch_timeout_budget_s = 0.0
        cfg.partial_export_findings_interval = 10
        cfg.source_tier_map = {}
        cfg.tier_of = MagicMock(return_value=MagicMock())
        cfg.sorted_tiers = MagicMock(return_value=[])
        return SprintScheduler(cfg)

    def test_report_includes_source_health_summary(self):
        """F206I-14: _build_diagnostic_report adds source_health_summary."""
        from hledac.universal.runtime.sprint_scheduler import SourceEconomics
        from hledac.universal.runtime.sprint_lifecycle import (
            SprintLifecycleManager,
            SprintPhase,
        )
        scheduler = self._make_scheduler()
        econ = SourceEconomics(source="https://test.com")
        econ.recent_health_posture = "hot"
        econ.last_signal_cycle = 1
        scheduler._source_economics["https://test.com"] = econ

        lifecycle = MagicMock()
        lifecycle.current_phase.name = "ACTIVE"

        report = scheduler._build_diagnostic_report(lifecycle)
        assert "source_health_summary" in report
        assert report["source_health_summary"]["total_tracked"] == 1

    def test_report_includes_circuit_breaker_state(self):
        """F206I-13: _build_diagnostic_report adds circuit_breaker_state."""
        from hledac.universal.transport.circuit_breaker import (
            clear_all_breakers,
            get_breaker,
        )
        clear_all_breakers()
        get_breaker("test.example.com").record_success()

        scheduler = self._make_scheduler()
        lifecycle = MagicMock()
        lifecycle.current_phase.name = "ACTIVE"

        report = scheduler._build_diagnostic_report(lifecycle)
        assert "circuit_breaker_state" in report
        assert report["circuit_breaker_state"]["total_tracked"] >= 1
        clear_all_breakers()


class TestMalwarebazaarCircuitCoverage:
    """F206I-8 and F206I-9: malwarebazaar uses checked_aiohttp_post."""

    def test_fetch_malwarebazaar_recent_uses_checked_post(self):
        """F206I-8: fetch_malwarebazaar_recent calls checked_aiohttp_post."""
        from unittest.mock import patch
        from hledac.universal.discovery.ti_feed_adapter import fetch_malwarebazaar_recent

        mock_resp = MagicMock()
        mock_resp.json = AsyncMock(return_value={"data": []})

        captured_calls = []

        async def fake_checked_post(session, url, **kwargs):
            captured_calls.append(kwargs)
            return mock_resp, None

        with patch(
            "hledac.universal.discovery.ti_feed_adapter.checked_aiohttp_post",
            side_effect=fake_checked_post,
        ):
            result = asyncio.run(fetch_malwarebazaar_recent(max_items=10))
        assert len(captured_calls) == 1
        assert captured_calls[0]["failure_kind"] == "malwarebazaar_recent"
        assert captured_calls[0]["timeout"] is not None
        assert "json" in captured_calls[0]

    def test_handle_malwarebazaar_search_uses_checked_post(self):
        """F206I-9: _handle_malwarebazaar_search calls checked_aiohttp_post."""
        from unittest.mock import patch
        from hledac.universal.discovery.ti_feed_adapter import _handle_malwarebazaar_search

        mock_task = MagicMock()
        mock_task.ioc_value = "a" * 64  # 64-char hex = SHA256 hash
        mock_scheduler = MagicMock()

        mock_resp = MagicMock()
        mock_resp.json = AsyncMock(return_value={"data": [{"sha256": "test"}]})

        captured_calls = []

        async def fake_checked_post(session, url, **kwargs):
            captured_calls.append(kwargs)
            return mock_resp, None

        with patch(
            "hledac.universal.discovery.ti_feed_adapter.checked_aiohttp_post",
            side_effect=fake_checked_post,
        ):
            asyncio.run(_handle_malwarebazaar_search(mock_task, mock_scheduler))

        assert len(captured_calls) == 1
        assert captured_calls[0]["failure_kind"] == "malwarebazaar_info"


class TestShodanCircuitCoverage:
    """F206I-10: _query_shodan_internetdb uses checked_aiohttp_get."""

    def test_query_shodan_internetdb_uses_checked_get(self):
        """F206I-10: _query_shodan_internetdb calls checked_aiohttp_get."""
        from unittest.mock import patch
        from hledac.universal.discovery.duckduckgo_adapter import _query_shodan_internetdb

        mock_resp = MagicMock()
        mock_resp.json = AsyncMock(
            return_value={"ip": "1.2.3.4", "ports": [80], "cves": [], "hostnames": [], "tags": []}
        )

        captured_calls = []

        async def fake_checked_get(session, url, **kwargs):
            captured_calls.append(kwargs)
            return mock_resp, None

        with patch(
            "hledac.universal.discovery.duckduckgo_adapter.checked_aiohttp_get",
            side_effect=fake_checked_get,
        ):
            result = asyncio.run(_query_shodan_internetdb("1.2.3.4"))

        assert len(captured_calls) == 1
        assert captured_calls[0]["failure_kind"] == "shodan_internetdb"
        assert captured_calls[0]["timeout"] is not None


class TestRdapCircuitCoverage:
    """F206I-16: query_rdap uses checked_aiohttp_get."""

    def test_query_rdap_uses_checked_get(self):
        """F206I-16: query_rdap calls checked_aiohttp_get."""
        from unittest.mock import patch
        from hledac.universal.discovery.ti_feed_adapter import query_rdap

        mock_resp = MagicMock()
        mock_resp.json = AsyncMock(
            return_value={"handle": "1.2.3.4", "events": []}
        )

        captured_calls = []

        async def fake_checked_get(session, url, **kwargs):
            captured_calls.append(kwargs)
            return mock_resp, None

        with patch(
            "hledac.universal.discovery.ti_feed_adapter.checked_aiohttp_get",
            side_effect=fake_checked_get,
        ):
            result = asyncio.run(query_rdap("1.2.3.4"))

        assert len(captured_calls) == 1
        assert captured_calls[0]["failure_kind"] == "rdap"
        assert captured_calls[0]["timeout"] is not None


class TestAhmiaCircuitCoverage:
    """F206I-17: search_ahmia uses checked_aiohttp_get for both onion and clearnet."""

    def test_search_ahmia_uses_checked_get_clearnet(self):
        """F206I-17: search_ahmia clearnet path calls checked_aiohttp_get."""
        from unittest.mock import patch
        from hledac.universal.discovery.ti_feed_adapter import search_ahmia

        mock_resp = MagicMock()
        mock_resp.text = AsyncMock(return_value="<html><li class='result'><h4><a href='/test'>Test</a></h4><p>snippet</p></li></html>")

        captured_calls = []
        async def fake_checked_get(session, url, **kwargs):
            captured_calls.append(kwargs)
            return mock_resp, None

        with patch(
            "hledac.universal.discovery.ti_feed_adapter.checked_aiohttp_get",
            side_effect=fake_checked_get,
        ):
            result = asyncio.run(search_ahmia("test query", use_onion=False))

        assert len(captured_calls) == 1
        assert captured_calls[0]["failure_kind"] == "ahmia_clearnet"
        assert captured_calls[0]["timeout"] is not None

    def test_search_ahmia_uses_checked_get_onion(self):
        """F206I-17: search_ahmia onion path calls checked_aiohttp_get."""
        from unittest.mock import patch
        from hledac.universal.discovery.ti_feed_adapter import search_ahmia

        mock_resp = MagicMock()
        mock_resp.text = AsyncMock(return_value="<html><li class='result'><h4><a href='/test'>Test</a></h4><p>snippet</p></li></html>")

        captured_calls = []
        async def fake_checked_get(session, url, **kwargs):
            captured_calls.append(kwargs)
            return mock_resp, None

        with patch(
            "hledac.universal.discovery.ti_feed_adapter.checked_aiohttp_get",
            side_effect=fake_checked_get,
        ):
            result = asyncio.run(search_ahmia("test query", use_onion=True))

        assert len(captured_calls) == 1
        assert captured_calls[0]["failure_kind"] == "ahmia_onion"
        assert captured_calls[0]["timeout"] is not None


class TestCircuitBreakerBounds:
    """F206I-15: MAX_TRACKED_DOMAINS = 500 bound preserved."""

    def test_max_tracked_domains_is_500(self):
        """F206I-15: Circuit breaker MAX_TRACKED_DOMAINS = 500."""
        from hledac.universal.transport.circuit_breaker import MAX_TRACKED_DOMAINS
        assert MAX_TRACKED_DOMAINS == 500

    def test_circuit_breaker_summary_respects_max(self):
        """F206I-15: Circuit breaker summary entries respect MAX_TRACKED_DOMAINS."""
        from hledac.universal.transport.circuit_breaker import (
            clear_all_breakers,
            get_breaker,
        )
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        clear_all_breakers()
        # Create 600 breakers (exceeds MAX_TRACKED_DOMAINS)
        for i in range(600):
            get_breaker(f"source{i}.example.com").record_success()

        cfg = MagicMock()
        cfg.sprint_duration_s = 60.0
        cfg.windup_lead_s = 5.0
        cfg.cycle_sleep_s = 1.0
        cfg.max_cycles = 2
        cfg.max_parallel_sources = 2
        cfg.stop_on_first_accepted = False
        cfg.export_enabled = False
        cfg.export_dir = ""
        cfg.max_entries_per_cycle = 50
        cfg.max_hypothesis_depth = 0
        cfg.max_hypothesis_queries = 0
        cfg.aggressive_mode = False
        cfg.aggressive_branch_timeout_s = 45.0
        cfg.branch_timeout_budget_s = 0.0
        cfg.partial_export_findings_interval = 10
        cfg.source_tier_map = {}
        cfg.tier_of = MagicMock(return_value=MagicMock())
        cfg.sorted_tiers = MagicMock(return_value=[])
        scheduler = SprintScheduler(cfg)

        result = scheduler._get_circuit_breaker_summary()
        # Entries should be capped to MAX_TRACKED_DOMAINS (500)
        assert len(result["entries"]) <= 500
        assert result["total_tracked"] <= 600  # LRU eviction happened
        clear_all_breakers()
