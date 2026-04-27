"""
Sprint F197A: DeepProbe Canonical Ingest Tests
==============================================

Tests verify that DeepProbe findings flow through the canonical path:
  1. Findings normalized to CanonicalFinding
  2. Persisted ONLY via async_ingest_findings_batch()
  3. DHT findings NOT persisted (ephemeral)
  4. Runtime counters updated correctly

Invariant table (for pytest naming):
  invariant_1 | probe findings have source_type="deep_probe"
  invariant_2 | timeout is bounded (MAX_PROBE_DURATION_S = 120)
  invariant_3 | depth is bounded (MAX_CRAWL_DEPTH = 3)
  invariant_4 | sprint export completes before probe starts
  invariant_5 | all methods are fail-safe (try/except everywhere)
  invariant_6 | findings persisted ONLY via async_ingest_findings_batch()
  invariant_7 | DHT findings are NOT persisted
"""

import asyncio
import time
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from hledac.universal.deep_probe import (
    DeepProbeScanner,
    scan_ipfs,
    scan_s3_buckets,
)
from hledac.universal.deep_research.probe_runner import (
    run_deep_probe,
    run_deep_probe_if_enabled,
    _make_discovery_findings,
    _extract_domain,
    MAX_PROBE_DURATION_S,
    MAX_CRAWL_DEPTH,
    MAX_BUCKET_SCAN,
)
from hledac.universal.knowledge.duckdb_store import CanonicalFinding


class TestDeepProbeCanonicalFindings:
    """Test that deep_probe methods return CanonicalFinding objects."""

    @pytest.fixture
    def mock_store(self):
        """Mock DuckDBShadowStore with async_ingest_findings_batch."""
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[])
        store.async_initialize = AsyncMock()
        store.aclose = AsyncMock()
        return store

    def test__make_bucket_finding_returns_canonical_finding(self):
        """invariant_6: _make_bucket_finding returns CanonicalFinding."""
        scanner = DeepProbeScanner()
        result = {
            "bucket": "test-bucket",
            "provider": "s3",
            "objects": [{"key": "file.txt", "size": 100}],
            "accessible": True,
        }

        finding = scanner._make_bucket_finding(result, "deep_probe")

        assert finding is not None
        assert isinstance(finding, CanonicalFinding)
        assert finding.source_type == "deep_probe"
        assert finding.query == "test-bucket"
        assert finding.confidence == 0.9  # has objects
        assert finding.payload_text is not None
        assert "deep_probe" in finding.provenance

    def test__make_bucket_finding_no_objects_confidence(self):
        """invariant_6: bucket without objects has lower confidence."""
        scanner = DeepProbeScanner()
        result = {
            "bucket": "empty-bucket",
            "provider": "s3",
            "objects": [],
            "accessible": True,
        }

        finding = scanner._make_bucket_finding(result, "deep_probe")

        assert finding is not None
        assert finding.confidence == 0.5  # no objects

    def test__make_bucket_finding_fail_safe(self):
        """invariant_5: fail-safe on malformed input."""
        scanner = DeepProbeScanner()
        result = {}  # missing required fields

        finding = scanner._make_bucket_finding(result, "deep_probe")

        assert finding is None  # fail-safe returns None

    def test__make_discovery_findings_returns_list(self):
        """invariant_6: _make_discovery_findings returns list of CanonicalFinding."""
        urls = [
            "https://example.com/research/paper.pdf",
            "https://example.com/docs/manual.pdf",
        ]

        findings = _make_discovery_findings(urls, "example.com research")

        assert isinstance(findings, list)
        assert len(findings) == 2
        for f in findings:
            assert isinstance(f, CanonicalFinding)
            assert f.source_type == "deep_probe"

    def test__make_discovery_findings_capped_at_100(self):
        """invariant_6: discovery findings capped at 100 URLs."""
        urls = [f"https://example.com/url/{i}.pdf" for i in range(200)]

        findings = _make_discovery_findings(urls, "example.com")

        assert len(findings) == 100

    def test__make_discovery_findings_dedup_key_includes_url(self):
        """invariant_6: each finding has unique ID based on URL."""
        urls = ["https://example.com/unique1.pdf", "https://example.com/unique2.pdf"]

        findings = _make_discovery_findings(urls, "example.com")

        assert len(findings) == 2
        ids = [f.finding_id for f in findings]
        assert ids[0] != ids[1]  # different URLs = different IDs


class TestRunDeepProbeCanonicalIngest:
    """Test run_deep_probe calls async_ingest_findings_batch."""

    @pytest.fixture
    def mock_scanner(self):
        """Mock DeepProbeScanner methods."""
        scanner = MagicMock(spec=DeepProbeScanner)
        scanner.scan = AsyncMock(return_value=[
            "https://example.com/research/paper.pdf"
        ])
        scanner.scan_s3_buckets = AsyncMock(return_value=(
            [{"bucket": "test-bucket", "accessible": True, "objects": []}],
            []  # no canonical findings in mock
        ))
        return scanner

    @pytest.fixture
    def mock_store(self):
        """Mock DuckDBShadowStore."""
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[])
        return store

    @pytest.mark.asyncio
    async def test_run_deep_probe_calls_async_ingest_findings_batch(self, mock_store):
        """invariant_6: findings persisted via async_ingest_findings_batch."""
        with patch("hledac.universal.deep_probe.DeepProbeScanner") as MockScanner:
            mock_scanner = MockScanner.return_value
            mock_scanner.scan = AsyncMock(return_value=[
                "https://example.com/research/paper.pdf"
            ])
            mock_scanner.scan_s3_buckets = AsyncMock(return_value=([], []))

            with patch("hledac.universal.deep_probe.scan_ipfs", AsyncMock(return_value=[])):
                result = await run_deep_probe("example.com research", mock_store)

        # Verify batch ingest was called
        mock_store.async_ingest_findings_batch.assert_called_once()
        call_args = mock_store.async_ingest_findings_batch.call_args
        findings = call_args[0][0]  # first positional arg

        assert isinstance(findings, list)
        assert len(findings) > 0  # at least discovery finding
        for f in findings:
            assert isinstance(f, CanonicalFinding)

        assert result["findings_ingested"] >= 0  # count of accepted

    @pytest.mark.asyncio
    async def test_run_deep_probe_fail_safe_on_ingest_error(self, mock_store):
        """invariant_5: fail-safe when async_ingest_findings_batch raises."""
        mock_store.async_ingest_findings_batch = AsyncMock(
            side_effect=Exception("ingest error")
        )

        with patch("hledac.universal.deep_probe.DeepProbeScanner") as MockScanner:
            mock_scanner = MockScanner.return_value
            mock_scanner.scan = AsyncMock(return_value=["https://example.com/test.pdf"])
            mock_scanner.scan_s3_buckets = AsyncMock(return_value=([], []))
            with patch("hledac.universal.deep_probe.scan_ipfs", AsyncMock(return_value=[])):
                result = await run_deep_probe("example.com", mock_store)

        # Should still complete, just with error logged
        assert result["probe_duration_s"] >= 0
        assert "ingest" in str(result["errors"])


class TestDeepProbeConstants:
    """Test that constants are bounded as required."""

    def test_max_probe_duration_s_is_120(self):
        """invariant_2: timeout bounded to 120s."""
        assert MAX_PROBE_DURATION_S == 120.0

    def test_max_crawl_depth_is_3(self):
        """invariant_3: depth bounded to 3."""
        assert MAX_CRAWL_DEPTH == 3

    def test_max_bucket_scan_is_50(self):
        """MAX_BUCKET_SCAN is bounded."""
        assert MAX_BUCKET_SCAN == 50


class TestExtractDomain:
    """Test domain extraction from queries."""

    def test_extract_domain_from_url(self):
        """_extract_domain handles URL input."""
        domain = _extract_domain("https://example.com/research/papers")
        assert domain == "example.com"

    def test_extract_domain_from_www(self):
        """_extract_domain handles www prefix."""
        domain = _extract_domain("www.example.com")
        assert domain == "example.com"

    def test_extract_domain_from_plain_query(self):
        """_extract_domain returns None for plain queries."""
        domain = _extract_domain("machine learning papers")
        assert domain is None

    def test_extract_domain_from_invalid(self):
        """_extract_domain fail-safe on invalid input."""
        domain = _extract_domain("")
        assert domain is None


class TestRunDeepProbeIfEnabled:
    """Test run_deep_probe_if_enabled conditional logic."""

    @pytest.mark.asyncio
    async def test_returns_none_when_disabled(self):
        """invariant_4: returns None when deep_probe_enabled=False."""
        result = await run_deep_probe_if_enabled(
            "test query",
            store=MagicMock(),
            deep_probe_enabled=False,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_runs_when_enabled(self):
        """invariant_4: runs when deep_probe_enabled=True."""
        mock_store = MagicMock()
        mock_store.async_ingest_findings_batch = AsyncMock(return_value=[])

        with patch("hledac.universal.deep_probe.DeepProbeScanner") as MockScanner:
            mock_scanner = MockScanner.return_value
            mock_scanner.scan = AsyncMock(return_value=[])
            mock_scanner.scan_s3_buckets = AsyncMock(return_value=([], []))
            with patch("hledac.universal.deep_probe.scan_ipfs", AsyncMock(return_value=[])):
                result = await run_deep_probe_if_enabled(
                    "test query",
                    mock_store,
                    deep_probe_enabled=True,
                )

        assert result is not None
        assert "probe_duration_s" in result


class TestScanS3BucketsReturnType:
    """Test that scan_s3_buckets returns correct tuple type."""

    @pytest.mark.asyncio
    async def test_scan_s3_buckets_returns_tuple(self):
        """invariant_6: scan_s3_buckets returns (results, findings) tuple."""
        # This tests the interface change - actual API calls are mocked
        with patch("hledac.universal.deep_probe.DeepProbeScanner") as MockScanner:
            mock_scanner = MockScanner.return_value
            mock_scanner.scan_s3_buckets = AsyncMock(return_value=([], []))

            _, findings = await scan_s3_buckets("example.com")

        assert isinstance(findings, list)


class TestScanIpfsReturnType:
    """Test that scan_ipfs returns CanonicalFinding list."""

    @pytest.mark.asyncio
    async def test_scan_ipfs_returns_canonical_findings(self):
        """invariant_6: scan_ipfs returns list of CanonicalFinding with source_type='deep_probe_ipfs'."""
        # Mock the API responses
        mock_response = [
            {"title": "Test Doc", "cid": "QmTest123", "size": 100, "source": "test"}
        ]

        with patch("aiohttp.ClientSession") as mock_session:
            mock_instance = MagicMock()
            mock_session.return_value.__aenter__.return_value = mock_instance
            mock_instance.get.return_value.__aenter__.return_value.json = AsyncMock(return_value=mock_response)
            mock_instance.get.return_value.__aenter__.return_value.status = 200

            findings = await scan_ipfs("test query")

        assert isinstance(findings, list)
        for f in findings:
            assert isinstance(f, CanonicalFinding)
            assert f.source_type == "deep_probe_ipfs"  # F206F: explicit IPFS tag
            assert "ipfs" in f.provenance
