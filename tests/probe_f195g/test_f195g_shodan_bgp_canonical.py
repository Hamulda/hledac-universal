"""
Sprint F195G: Shodan + BGP Canonical Finding Persistence
==========================================================

Integration tests verifying that:
1. search_shodan_to_findings() produces valid CanonicalFinding list
2. shodan_enrich handler persists findings via DuckDBShadowStore.async_ingest_findings_batch()
3. bgp_routing_history handler persists findings via DuckDBShadowStore
4. Pivot behavior (buffer_ioc_pivot) is preserved as side effect
5. arm64 graceful fallback remains intact (BGP_AVAILABLE=False)

Invariant mapping:
- F195G-1: search_shodan_to_findings returns (findings, raw_results) with source_type="shodan_search"
- F195G-2: shodan_enrich calls async_ingest_findings_batch when store is set
- F195G-3: shodan_enrich preserves pivot side effect (_buffer_ioc_pivot)
- F195G-4: bgp_routing_history degrades gracefully when BGP_AVAILABLE=False
- F195G-5: bgp_routing_history persists when pybgpstream available
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class FakePivotTask:
    """Minimal task object matching what _execute_pivot passes to handlers."""
    def __init__(self, task_type: str, ioc_value: str):
        self.task_type = task_type
        self.ioc_value = ioc_value


class TestF195G_Shodan_CanonicalFindings:
    """Test suite for Shodan canonical finding persistence (Sprint F195G)."""

    @pytest.fixture
    def mock_duckdb_store(self):
        """Create a mock DuckDBShadowStore that tracks ingested findings."""
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[
            {"accepted": True, "finding_id": "shodan_1.2.3.4_80_test", "reason": None}
        ])
        return store

    @pytest.fixture
    def mock_scheduler(self, mock_duckdb_store):
        """Create a mock scheduler with _duckdb_store and pivot infrastructure."""
        scheduler = MagicMock()
        scheduler._duckdb_store = mock_duckdb_store
        scheduler._buffer_ioc_pivot = AsyncMock()
        scheduler._ioc_graph = MagicMock()
        scheduler._pivot_ioc_graph = None
        return scheduler

    @pytest.mark.asyncio
    async def test_f195g_search_shodan_to_findings_returns_tuple(self):
        """F195G-1: search_shodan_to_findings returns (findings, raw_results) tuple."""
        from hledac.universal.intelligence.shodan_wrapper import search_shodan_to_findings

        mock_raw = [
            {
                "ip": "1.2.3.4",
                "port": 80,
                "banner": "HTTP/1.1 200 OK\r\nServer: Apache/2.4\r\nContent-Type: text/html",
                "hostnames": ["example.com"],
            },
            {
                "ip": "5.6.7.8",
                "port": 443,
                "banner": "SSH-2.0-OpenSSH_8.0",
                "hostnames": [],
            },
        ]

        with patch("hledac.universal.intelligence.shodan_wrapper.search_shodan",
                   new=AsyncMock(return_value=mock_raw)):
            findings, raw_results = await search_shodan_to_findings("apache", limit=10)

        assert isinstance(findings, list)
        assert isinstance(raw_results, list)
        assert len(findings) == 2
        assert len(raw_results) == 2

    @pytest.mark.asyncio
    async def test_f195g_shodan_finding_source_type_contract(self):
        """F195G-1b: CanonicalFinding from search_shodan has source_type='shodan_search'."""
        from hledac.universal.intelligence.shodan_wrapper import search_shodan_to_findings
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        mock_raw = [
            {
                "ip": "1.2.3.4",
                "port": 80,
                "banner": "Apache banner content here" * 10,
                "hostnames": ["example.com"],
            },
        ]

        with patch("hledac.universal.intelligence.shodan_wrapper.search_shodan",
                   new=AsyncMock(return_value=mock_raw)):
            findings, _ = await search_shodan_to_findings("apache", limit=10)

        assert len(findings) == 1
        finding = findings[0]
        assert isinstance(finding, CanonicalFinding)
        assert finding.source_type == "shodan_search"
        assert finding.query.startswith("shodan_search:")
        assert finding.confidence >= 0.65
        assert finding.provenance is not None
        assert len(finding.provenance) >= 4  # (shodan_search, query, ip, port)

    @pytest.mark.asyncio
    async def test_f195g_shodan_finding_confidence_richness(self):
        """F195G-1c: Confidence scales with banner richness."""
        from hledac.universal.intelligence.shodan_wrapper import search_shodan_to_findings

        # Short banner → lower confidence
        with patch("hledac.universal.intelligence.shodan_wrapper.search_shodan",
                   new=AsyncMock(return_value=[{"ip": "1.2.3.4", "port": 8080, "banner": "short", "hostnames": []}])):
            findings, _ = await search_shodan_to_findings("apache", limit=10)
        assert findings[0].confidence == 0.65

        # Long banner → higher confidence
        with patch("hledac.universal.intelligence.shodan_wrapper.search_shodan",
                   new=AsyncMock(return_value=[{"ip": "1.2.3.4", "port": 8080, "banner": "x" * 200, "hostnames": []}])):
            findings, _ = await search_shodan_to_findings("apache", limit=10)
        assert findings[0].confidence == 0.75

        # Banner + hostname → highest
        with patch("hledac.universal.intelligence.shodan_wrapper.search_shodan",
                   new=AsyncMock(return_value=[{"ip": "1.2.3.4", "port": 8080, "banner": "x" * 200, "hostnames": ["ex.com"]}])):
            findings, _ = await search_shodan_to_findings("apache", limit=10)
        assert findings[0].confidence == 0.80

        # Common service ports with banner → highest tier
        with patch("hledac.universal.intelligence.shodan_wrapper.search_shodan",
                   new=AsyncMock(return_value=[{"ip": "1.2.3.4", "port": 443, "banner": "TLS server banner!", "hostnames": []}])):
            findings, _ = await search_shodan_to_findings("apache", limit=10)
        assert findings[0].confidence == 0.85

    @pytest.mark.asyncio
    async def test_f195g_shodan_enrich_persists_and_pivots(self, mock_scheduler):
        """F195G-2+3: shodan_enrich calls async_ingest_findings_batch AND preserves pivot."""
        from hledac.universal.discovery.ti_feed_adapter import _handle_shodan_enrich

        mock_raw = [
            {"ip": "1.2.3.4", "port": 80, "banner": "Apache/2.4", "hostnames": ["ex.com"]},
        ]
        findings = [
            MagicMock(
                finding_id="shodan_1.2.3.4_80_test",
                query="shodan_search:test",
                source_type="shodan_search",
                confidence=0.80,
                ts=1234567890.0,
                provenance=("shodan_search", "test", "1.2.3.4", "80"),
            )
        ]

        with patch("hledac.universal.intelligence.shodan_wrapper.search_shodan_to_findings",
                   new=AsyncMock(return_value=(findings, mock_raw))):
            task = FakePivotTask(task_type="shodan_enrich", ioc_value="apache")
            await _handle_shodan_enrich(task, mock_scheduler)

        # Persistence: async_ingest_findings_batch was called
        mock_scheduler._duckdb_store.async_ingest_findings_batch.assert_called_once()

        # Pivot side effect: _buffer_ioc_pivot was called
        mock_scheduler._buffer_ioc_pivot.assert_called_once_with("ipv4", "apache", 0.80)

    @pytest.mark.asyncio
    async def test_f195g_shodan_enrich_no_store_still_pivots(self):
        """F195G-2b: shodan_enrich degrades gracefully when store is None, pivot preserved."""
        from hledac.universal.discovery.ti_feed_adapter import _handle_shodan_enrich

        scheduler = MagicMock()
        scheduler._duckdb_store = None
        scheduler._buffer_ioc_pivot = AsyncMock()
        scheduler._ioc_graph = MagicMock()
        scheduler._pivot_ioc_graph = None

        mock_raw = [{"ip": "1.2.3.4", "port": 80, "banner": "Apache", "hostnames": []}]
        findings: list = []

        with patch("hledac.universal.intelligence.shodan_wrapper.search_shodan_to_findings",
                   new=AsyncMock(return_value=(findings, mock_raw))):
            task = FakePivotTask(task_type="shodan_enrich", ioc_value="apache")
            await _handle_shodan_enrich(task, scheduler)

        # Pivot should still fire even with no findings and no store
        scheduler._buffer_ioc_pivot.assert_called_once_with("ipv4", "apache", 0.80)


class TestF195G_BGP_CanonicalFindings:
    """Test suite for BGP canonical finding persistence (Sprint F195G)."""

    @pytest.fixture
    def mock_duckdb_store(self):
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[
            {"accepted": True, "finding_id": "bgp_test_123", "reason": None}
        ])
        return store

    @pytest.fixture
    def mock_scheduler(self, mock_duckdb_store):
        scheduler = MagicMock()
        scheduler._duckdb_store = mock_duckdb_store
        scheduler._buffer_ioc_pivot = AsyncMock()
        scheduler._ioc_graph = MagicMock()
        scheduler._pivot_ioc_graph = None
        return scheduler

    @pytest.mark.asyncio
    async def test_f195g_bgp_handler_graceful_no_bgp_available(self, mock_scheduler):
        """F195G-4: bgp_routing_history degrades gracefully when BGP_AVAILABLE=False."""
        from hledac.universal.discovery.ti_feed_adapter import _handle_bgp_routing_history

        # Patch the bgp_monitor module's BGP_AVAILABLE to False
        with patch.dict("hledac.universal.network.bgp_monitor.__dict__", {"BGP_AVAILABLE": False}):
            with patch("hledac.universal.discovery.ti_feed_adapter.query_bgp_routing_history",
                       new=AsyncMock(return_value={"resource": "1.2.3.0/24", "history": [{"ts": 123}]})):
                task = FakePivotTask(task_type="bgp_routing_history", ioc_value="1.2.3.0/24")
                # Should not raise — graceful degradation returns early after pivot
                await _handle_bgp_routing_history(task, mock_scheduler)

        # Pivot should still be called (history was returned)
        mock_scheduler._buffer_ioc_pivot.assert_called_once()

    @pytest.mark.asyncio
    async def test_f195g_bgp_routing_history_pivots_and_persists(self, mock_scheduler):
        """F195G-5: bgp_routing_history handler persists findings when pybgpstream available."""
        from hledac.universal.discovery.ti_feed_adapter import _handle_bgp_routing_history

        # Mock query_bgp_routing_history to return history (triggers the handler body)
        with patch("hledac.universal.discovery.ti_feed_adapter.query_bgp_routing_history",
                   new=AsyncMock(return_value={"resource": "1.2.3.0/24", "history": [{"ts": 123}]})):
            # Mock monitor_bgp at source module (imported inside handler)
            mock_events = [
                {"timestamp": 1234567890.0, "prefix": "1.2.3.0/24", "as_path": "13335 1234", "event_type": "announce"},
                {"timestamp": 1234567900.0, "prefix": "1.2.3.0/24", "as_path": "13335 5678", "event_type": "withdraw"},
            ]
            with patch("hledac.universal.network.bgp_monitor.monitor_bgp",
                       new=AsyncMock(return_value=mock_events)):
                task = FakePivotTask(task_type="bgp_routing_history", ioc_value="1.2.3.0/24")
                await _handle_bgp_routing_history(task, mock_scheduler)

        # Pivot side effect should be called
        mock_scheduler._buffer_ioc_pivot.assert_called()

        # Persistence: async_ingest_findings_batch was called with BGP findings
        mock_scheduler._duckdb_store.async_ingest_findings_batch.assert_called_once()
        call_args = mock_scheduler._duckdb_store.async_ingest_findings_batch.call_args
        findings = call_args[0][0]

        assert len(findings) == 2
        for finding in findings:
            assert finding.source_type == "bgp_monitor"
            assert finding.query.startswith("bgp_monitor:")
            assert finding.confidence == 0.75
            assert "prefix" in finding.payload_text

    @pytest.mark.asyncio
    async def test_f195g_bgp_handler_no_history_no_persist(self, mock_scheduler):
        """F195G-5b: bgp_routing_history skips persistence when no history returned."""
        from hledac.universal.discovery.ti_feed_adapter import _handle_bgp_routing_history

        with patch("hledac.universal.discovery.ti_feed_adapter.query_bgp_routing_history",
                   new=AsyncMock(return_value={"resource": "1.2.3.0/24", "history": []})):
            task = FakePivotTask(task_type="bgp_routing_history", ioc_value="1.2.3.0/24")
            await _handle_bgp_routing_history(task, mock_scheduler)

        # No pivot when no history
        mock_scheduler._buffer_ioc_pivot.assert_not_called()
        # No persistence either
        mock_scheduler._duckdb_store.async_ingest_findings_batch.assert_not_called()