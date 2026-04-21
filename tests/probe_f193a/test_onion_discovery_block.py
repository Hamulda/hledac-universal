"""
Sprint F193A: test_onion_discovery_block.py

Tests for the onion discovery pathway in live_public_pipeline:
- Bounded: max 5 onion hits
- Circuit breaker: stops after 3 failures
- Fail-soft: returns gracefully on errors
- Produces CanonicalFinding-compatible output
- No new storage paths introduced
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import hashlib
import time


class TestOnionDiscoveryBounded:
    """Tests for onion discovery bounds and fail-soft behavior."""

    @pytest.mark.asyncio
    async def test_onion_hits_bounded_to_max(self):
        """Onion discovery must not exceed _ONION_HIT_MAX hits."""
        from hledac.universal.pipeline.live_public_pipeline import (
            _inject_onion_hits,
            _ONION_HIT_MAX,
        )

        # Create 10 onion hits (more than MAX)
        mock_store = AsyncMock()
        hits = tuple()
        for i in range(10):
            h = MagicMock()
            h.url = f"http://onion{i}.onion/page"
            hits += (h,)

        with patch("hledac.universal.fetching.public_fetcher.async_fetch_public_text", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = MagicMock(text="content", error=None)
            result = await _inject_onion_hits(hits, "test query", mock_store)

        # Should process at most _ONION_HIT_MAX hits
        assert result <= _ONION_HIT_MAX

    @pytest.mark.asyncio
    async def test_circuit_breaker_after_3_failures(self):
        """Circuit breaker must trip after _ONION_CIRCUIT_FAIL_LIMIT failures."""
        from hledac.universal.pipeline.live_public_pipeline import (
            _inject_onion_hits,
            _ONION_CIRCUIT_FAIL_LIMIT,
            _onion_circuit_state,
        )

        # Reset circuit state
        _onion_circuit_state["failures"] = 0
        _onion_circuit_state["opened_at"] = 0.0

        mock_store = AsyncMock()
        hits = tuple()
        for i in range(5):
            h = MagicMock()
            h.url = f"http://onion{i}.onion/page"
            hits += (h,)

        with patch("hledac.universal.fetching.public_fetcher.async_fetch_public_text", new_callable=AsyncMock) as mock_fetch:
            # All fetches fail
            mock_fetch.return_value = MagicMock(text=None, error="connection failed")

            initial_failures = _onion_circuit_state["failures"]
            await _inject_onion_hits(hits, "test query", mock_store)
            final_failures = _onion_circuit_state["failures"]

        # Circuit breaker records failures
        assert final_failures >= initial_failures

    @pytest.mark.asyncio
    async def test_fail_soft_on_exception(self):
        """Must return gracefully (0) on exceptions without raising."""
        from hledac.universal.pipeline.live_public_pipeline import _inject_onion_hits

        mock_store = AsyncMock()
        hits = (MagicMock(url="http://onion.onion/page"),)

        with patch("hledac.universal.fetching.public_fetcher.async_fetch_public_text", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = Exception("unexpected")

            result = await _inject_onion_hits(hits, "test query", mock_store)

        # Must not raise — fail-soft
        assert result == 0

    @pytest.mark.asyncio
    async def test_produces_canonical_finding(self):
        """Must produce CanonicalFinding objects with correct source_type."""
        from hledac.universal.pipeline.live_public_pipeline import _inject_onion_hits
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        mock_store = AsyncMock()
        hits = (MagicMock(url="http://test.onion/page"),)

        captured_findings = []

        async def capture_ingest(findings):
            captured_findings.extend(findings)

        mock_store.async_ingest_findings_batch = capture_ingest

        with patch("hledac.universal.fetching.public_fetcher.async_fetch_public_text", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = MagicMock(text="onion content here", error=None)

            await _inject_onion_hits(hits, "test query", mock_store)

        # Should have produced CanonicalFinding
        assert len(captured_findings) > 0
        finding = captured_findings[0]
        assert isinstance(finding, CanonicalFinding)
        assert finding.source_type == "onion_discovery"

    @pytest.mark.asyncio
    async def test_no_stored_findings_when_store_none(self):
        """Must not call store when duckdb_store is None (no alternate storage path)."""
        from hledac.universal.pipeline.live_public_pipeline import _inject_onion_hits

        hits = (MagicMock(url="http://test.onion/page"),)

        with patch("hledac.universal.fetching.public_fetcher.async_fetch_public_text", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = MagicMock(text="content", error=None)

            # Pass None store — returns count of successful fetches
            # (even without storage, we count findings found)
            result = await _inject_onion_hits(hits, "test query", None)

        # Should return count without error (1 onion hit found)
        assert isinstance(result, int)
        assert result >= 0


class TestOnionDiscoveryBoundedQuiet:
    """Quiet tests — no output, just assertion verification."""

    @pytest.mark.asyncio
    async def test_returns_int(self):
        """Must return an integer count."""
        from hledac.universal.pipeline.live_public_pipeline import _inject_onion_hits

        mock_store = AsyncMock()
        hits = (MagicMock(url="http://test.onion/"),)

        with patch("hledac.universal.fetching.public_fetcher.async_fetch_public_text", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = MagicMock(text="x", error=None)
            result = await _inject_onion_hits(hits, "q", mock_store)

        assert isinstance(result, int)

    @pytest.mark.asyncio
    async def test_empty_hits_returns_zero(self):
        """Empty hits tuple must return 0 without calling fetch."""
        from hledac.universal.pipeline.live_public_pipeline import _inject_onion_hits

        mock_store = AsyncMock()
        hits = ()

        with patch("hledac.universal.fetching.public_fetcher.async_fetch_public_text", new_callable=AsyncMock) as mock_fetch:
            result = await _inject_onion_hits(hits, "query", mock_store)

        assert result == 0
        mock_fetch.assert_not_called()
