"""
Sprint F203F: Wayback Diff Miner — Probe Tests
=============================================

Invariant mapping:
  F203F-1  | CDXDiffEvent is frozen dataclass with correct fields
  F203F-2  | WaybackDiffResult.to_findings returns list of CanonicalFinding
  F203F-3  | MAX_DOMAINS_PER_SPRINT=100 cap enforced
  F203F-4  | MAX_CDX_SNAPSHOTS_PER_DOMAIN=50 cap enforced
  F203F-5  | MAX_CHANGE_EVENTS=500 cap enforced on output
  F203F-6  | Circuit breaker opens after 3 consecutive 429/503
  F203F-7  | Circuit breaker auto-resets after 60s cooldown
  F203F-8  | change_type detection: first snapshot = "added"
  F203F-9  | change_type detection: digest change = "changed"
  F203F-10 | WaybackDiffMiner.mine() returns WaybackDiffResult
  F203F-11 | mine() with empty list returns empty result
  F203F-12 | gather return_exceptions=True — errors don't crash mine()
  F203F-13 | to_findings uses source_type="wayback_diff"
  F203F-14 | _timestamp_to_unix converts CDX timestamp to Unix float
  F203F-15 | evidence_url is valid Wayback replay URL
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import aiohttp

from hledac.universal.intelligence.wayback_diff_miner import (
    MAX_CDX_SNAPSHOTS_PER_DOMAIN,
    MAX_DOMAINS_PER_SPRINT,
    MAX_CHANGE_EVENTS,
    MAX_CONSECUTIVE_FAILURES,
    REQUEST_RATE_LIMIT,
    TIMEOUT_PER_REQUEST,
    CDXDiffEvent,
    WaybackDiffResult,
    WaybackDiffMiner,
    _WaybackCircuitBreaker,
    _timestamp_to_unix,
    _build_payload,
    WAYBACK_CDX_API,
    WAYBACK_BASE_URL,
)


# ============================================================================
# F203F-1: CDXDiffEvent frozen dataclass
# ============================================================================

class TestCDXDiffEvent:
    """F203F-1: CDXDiffEvent is a frozen dataclass."""

    def test_cdx_diff_event_frozen(self):
        """CDXDiffEvent instances are frozen (immutable)."""
        event = CDXDiffEvent(
            url="https://example.com",
            timestamp="20240101000000",
            digest="abc123",
            status_code=200,
            change_type="added",
            evidence_url="https://web.archive.org/web/20240101000000/https://example.com",
        )
        with pytest.raises(Exception):  # frozen dataclass — cannot setattr
            event.change_type = "changed"  # type: ignore

    def test_cdx_diff_event_fields(self):
        """CDXDiffEvent has correct fields."""
        event = CDXDiffEvent(
            url="https://example.com/page",
            timestamp="20240315120000",
            digest="def456",
            status_code=301,
            change_type="changed",
            evidence_url="https://web.archive.org/web/20240315120000/https://example.com/page",
        )
        assert event.url == "https://example.com/page"
        assert event.timestamp == "20240315120000"
        assert event.digest == "def456"
        assert event.status_code == 301
        assert event.change_type == "changed"
        assert "web.archive.org" in event.evidence_url

    def test_cdx_diff_event_optional_status(self):
        """CDXDiffEvent status_code can be None."""
        event = CDXDiffEvent(
            url="https://example.com",
            timestamp="20240101000000",
            digest="abc123",
            status_code=None,
            change_type="added",
            evidence_url="https://web.archive.org/web/20240101000000/https://example.com",
        )
        assert event.status_code is None


# ============================================================================
# F203F-14: _timestamp_to_unix
# ============================================================================

class TestTimestampToUnix:
    """F203F-14: _timestamp_to_unix converts CDX timestamp to Unix float."""

    def test_timestamp_convert_valid(self):
        """Valid CDX timestamp converts to positive Unix float."""
        ts = _timestamp_to_unix("20240101000000")
        assert ts > 0
        # Just verify it's a reasonable Unix timestamp for 2024
        assert ts > 1704067200 - 86400  # ~Jan 2024
        assert ts < 1735689600  # ~Jan 2025

    def test_timestamp_convert_invalid(self):
        """Invalid timestamp returns 0.0."""
        assert _timestamp_to_unix("invalid") == 0.0
        assert _timestamp_to_unix("") == 0.0


# ============================================================================
# F203F-15: _build_payload
# ============================================================================

class TestBuildPayload:
    """F203F-15: _build_payload builds evidence envelope."""

    def test_build_payload_added(self):
        """Payload contains change_type, url, timestamp, digest, replay URL."""
        event = CDXDiffEvent(
            url="https://example.com",
            timestamp="20240101000000",
            digest="abc123",
            status_code=200,
            change_type="added",
            evidence_url="https://web.archive.org/web/20240101000000/https://example.com",
        )
        payload = _build_payload(event)
        assert "ADDED" in payload
        assert "example.com" in payload
        assert "20240101000000" in payload
        assert "abc123" in payload
        assert "web.archive.org" in payload


# ============================================================================
# F203F-6/7: Circuit Breaker
# ============================================================================

class TestWaybackCircuitBreaker:
    """F203F-6/7: Circuit breaker behavior."""

    def test_breaker_starts_closed(self):
        """Circuit breaker starts in closed state."""
        breaker = _WaybackCircuitBreaker()
        assert not breaker.is_open()

    def test_breaker_opens_after_3_consecutive_429(self):
        """Circuit opens after 3 consecutive 429 errors."""
        breaker = _WaybackCircuitBreaker()
        for _ in range(MAX_CONSECUTIVE_FAILURES - 1):
            assert not breaker.is_open()
            breaker.record_failure(429)
        assert not breaker.is_open()
        breaker.record_failure(429)
        assert breaker.is_open()

    def test_breaker_opens_after_3_consecutive_503(self):
        """Circuit opens after 3 consecutive 503 errors."""
        breaker = _WaybackCircuitBreaker()
        breaker.record_failure(503)
        breaker.record_failure(503)
        assert not breaker.is_open()
        breaker.record_failure(503)
        assert breaker.is_open()

    def test_breaker_ignores_other_status(self):
        """Circuit breaker ignores non-429/503 status codes."""
        breaker = _WaybackCircuitBreaker()
        breaker.record_failure(500)
        breaker.record_failure(404)
        breaker.record_failure(403)
        assert not breaker.is_open()

    def test_breaker_resets_on_success(self):
        """Successful request resets failure counter."""
        breaker = _WaybackCircuitBreaker()
        breaker.record_failure(429)
        breaker.record_failure(429)
        breaker.record_success()
        assert not breaker.is_open()
        breaker.record_failure(429)
        breaker.record_failure(429)
        assert not breaker.is_open()

    def test_breaker_auto_resets_after_cooldown(self):
        """Circuit auto-resets after 60s cooldown."""
        breaker = _WaybackCircuitBreaker()
        breaker.record_failure(429)
        breaker.record_failure(429)
        breaker.record_failure(429)
        assert breaker.is_open()
        # Simulate time passage by directly manipulating _open_until
        import time
        breaker._open_until = 0.0  # Force past cooldown
        assert not breaker.is_open()
        assert breaker._consecutive_failures == 0


# ============================================================================
# F203F-10/11: WaybackDiffMiner.mine()
# ============================================================================

class TestWaybackDiffMinerMine:
    """F203F-10/11: WaybackDiffMiner.mine() behavior."""

    @pytest.mark.asyncio
    async def test_mine_empty_list_returns_empty_result(self):
        """mine() with empty list returns empty WaybackDiffResult."""
        miner = WaybackDiffMiner()
        result = await miner.mine([])
        assert result.input_count == 0
        assert result.change_events == []
        await miner.close()

# ============================================================================
# F203F-8/9: change_type detection
# ============================================================================

class TestChangeTypeDetection:
    """F203F-8/9: change_type detection in _fetch_and_diff."""

    @pytest.mark.asyncio
    async def test_fetch_and_diff_first_snapshot_added(self):
        """First snapshot in window gets change_type='added'."""
        miner = WaybackDiffMiner()
        miner._session = MagicMock(spec=aiohttp.ClientSession)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=[
            "header",
            ["20240101000000", "https://example.com", "200", "abc123", "1024"],
        ])
        miner._session.get = MagicMock(return_value=MagicMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=None),
        ))
        miner._semaphore = MagicMock()
        miner._semaphore.__aenter__ = AsyncMock(return_value=None)
        miner._semaphore.__aexit__ = AsyncMock(return_value=None)

        events = await miner._fetch_and_diff("example.com")
        await miner.close()

        assert len(events) == 1, f"Expected 1 event, got {len(events)}: {events}"
        assert events[0].change_type == "added"

    @pytest.mark.asyncio
    async def test_fetch_and_diff_digest_change_is_changed(self):
        """Digest different from previous snapshot gets change_type='changed'."""
        miner = WaybackDiffMiner()
        miner._session = MagicMock(spec=aiohttp.ClientSession)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=[
            "header",
            ["20240101000000", "https://example.com", "200", "abc123", "1024"],
            ["20240102000000", "https://example.com", "200", "def456", "2048"],
        ])
        miner._session.get = MagicMock(return_value=MagicMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=None),
        ))
        miner._semaphore = MagicMock()
        miner._semaphore.__aenter__ = AsyncMock(return_value=None)
        miner._semaphore.__aexit__ = AsyncMock(return_value=None)

        events = await miner._fetch_and_diff("example.com")
        await miner.close()

        assert len(events) == 2, f"Expected 2 events, got {len(events)}: {events}"
        assert events[0].change_type == "added"
        assert events[1].change_type == "changed"


# ============================================================================
# F203F-2/13: to_findings conversion
# ============================================================================

class TestWaybackDiffResultToFindings:
    """F203F-2/13: to_findings() converts events to CanonicalFinding."""

    def test_to_findings_source_type(self):
        """to_findings() sets source_type='wayback_diff'."""
        event = CDXDiffEvent(
            url="https://example.com",
            timestamp="20240101000000",
            digest="abc123",
            status_code=200,
            change_type="added",
            evidence_url="https://web.archive.org/web/20240101000000/https://example.com",
        )
        result = WaybackDiffResult(
            input_count=1,
            change_events=[event],
            stats={"domains_processed": 1},
        )
        findings = result.to_findings(query="test query", sprint_id="sprint-1")
        assert len(findings) == 1
        assert findings[0].source_type == "wayback_diff"

    def test_to_findings_payload_text(self):
        """to_findings() populates payload_text with evidence."""
        event = CDXDiffEvent(
            url="https://example.com",
            timestamp="20240101000000",
            digest="abc123",
            status_code=200,
            change_type="added",
            evidence_url="https://web.archive.org/web/20240101000000/https://example.com",
        )
        result = WaybackDiffResult(
            input_count=1,
            change_events=[event],
            stats={},
        )
        findings = result.to_findings(query="test", sprint_id="s1")
        assert "ADDED" in findings[0].payload_text
        assert "example.com" in findings[0].payload_text


# ============================================================================
# F203F-3/4/5: Bounds enforcement
# ============================================================================

class TestBounds:
    """F203F-3/4/5: Bounds are correctly defined."""

    def test_max_domains_per_sprint(self):
        """MAX_DOMAINS_PER_SPRINT is 100."""
        assert MAX_DOMAINS_PER_SPRINT == 100

    def test_max_cdx_snapshots_per_domain(self):
        """MAX_CDX_SNAPSHOTS_PER_DOMAIN is 50."""
        assert MAX_CDX_SNAPSHOTS_PER_DOMAIN == 50

    def test_max_change_events(self):
        """MAX_CHANGE_EVENTS is 500."""
        assert MAX_CHANGE_EVENTS == 500

    def test_max_consecutive_failures(self):
        """MAX_CONSECUTIVE_FAILURES is 3."""
        assert MAX_CONSECUTIVE_FAILURES == 3


# ============================================================================
# F203F-12: gather return_exceptions
# ============================================================================

class TestGatherReturnExceptions:
    """F203F-12: asyncio.gather return_exceptions=True behavior."""

    @pytest.mark.asyncio
    async def test_mine_handles_gather_exceptions(self):
        """mine() handles exceptions from gather gracefully."""
        miner = WaybackDiffMiner()
        # Provide domains but session will fail
        miner._session = AsyncMock(spec=aiohttp.ClientSession)
        miner._semaphore = MagicMock()
        miner._semaphore.__aenter__ = AsyncMock(return_value=None)
        miner._semaphore.__aexit__ = AsyncMock(return_value=None)

        # Make _fetch_and_diff raise an exception
        async def raise_error(target):
            raise RuntimeError("CDX error")

        miner._fetch_and_diff = raise_error

        # Should not raise — gather return_exceptions=True
        result = await miner.mine(["example.com", "test.com"])
        assert result.stats["errors"] >= 1
        await miner.close()
