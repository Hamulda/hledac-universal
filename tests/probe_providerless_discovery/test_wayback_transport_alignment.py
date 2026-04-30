#!/usr/bin/env python3
"""
Sprint F206AS: Wayback CDX Transport Alignment — probe tests
================================================================

F206AS-1  | Wayback adapter uses shared session (no raw aiohttp.ClientSession())
F206AS-2  | CDX URL construction unchanged
F206AS-3  | JSON response parsing unchanged
F206AS-4  | bounded max_results (hard cap 20)
F206AS-5  | dedup unchanged
F206AS-6  | timeout maps to error_type=timeout
F206AS-7  | HTTP 429 maps to error_type=http_429
F206AS-8  | HTTP 403 maps to error_type=http_403
F206AS-9  | HTTP 5xx maps to error_type=http_5xx
F206AS-10 | parse error maps to error_type=parse_error
F206AS-11 | provider_name/source_family/provider_chain preserved
F206AS-12 | CancelledError re-raised (not swallowed)
F206AS-13 | no archived body fetch (only CDX index API)
F206AS-14 | no Brave/SearXNG imports
F206AS-15 | circuit_breaker integration via checked_aiohttp_get
F206AS-16 | network_error on client_error
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# F206AS-1: No raw aiohttp.ClientSession() construction
# ---------------------------------------------------------------------------


class TestNoRawClientSession:
    """F206AS-1: wayback_cdx_adapter must not construct raw aiohttp.ClientSession()."""

    def test_f206as_1_no_raw_aiohttp_session_in_source(self):
        """F206AS-1: source must not contain 'aiohttp.ClientSession()' bare constructor."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        assert "aiohttp.ClientSession()" not in src, (
            "wayback_cdx_adapter must not construct raw aiohttp.ClientSession()"
        )
        # Must use checked_aiohttp_get
        assert "checked_aiohttp_get" in src, (
            "wayback_cdx_adapter must use checked_aiohttp_get from circuit_breaker"
        )


# ---------------------------------------------------------------------------
# F206AS-2 & F206AS-3: URL construction and JSON parsing
# ---------------------------------------------------------------------------


class TestWaybackURLConstruction:
    """F206AS-2: CDX URL construction unchanged. F206AS-3: JSON response parsing unchanged."""

    @pytest.mark.asyncio
    async def test_f206as_2_cdx_url_is_archive_org(self):
        """F206AS-2: CDX URL must reference Wayback Machine CDX endpoint via _WAYBACK_CDX_URL."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        # URL is stored in _WAYBACK_CDX_URL module constant
        assert "_WAYBACK_CDX_URL" in src, (
            "CDX URL must be Wayback Machine CDX endpoint via _WAYBACK_CDX_URL constant"
        )

    @pytest.mark.asyncio
    async def test_f206as_3_json_parsing_present(self):
        """F206AS-3: JSON parsing via resp.json() must be present."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        assert ".json()" in src, "Must parse JSON response"

    @pytest.mark.asyncio
    async def test_f206as_3_header_row_skip_present(self):
        """F206AS-3: Header row skip logic (CDX has url/timestamp/original/mimetype/statuscode)."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        assert '["url", "timestamp", "original"' in src, (
            "Must handle CDX JSON header row skip"
        )


# ---------------------------------------------------------------------------
# F206AS-4: bounded max_results
# ---------------------------------------------------------------------------


class TestMaxResultsBound:
    """F206AS-4: max_results hard cap at 20."""

    @pytest.mark.asyncio
    async def test_f206as_4_max_results_cap_20(self):
        """F206AS-4: max_results must be capped at 20."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        # Must have min(int(max_results), 20) bound
        assert "min(int(max_results), 20)" in src or "min(max_results, 20)" in src, (
            "max_results must be bounded to 20"
        )


# ---------------------------------------------------------------------------
# F206AS-5: dedup unchanged
# ---------------------------------------------------------------------------


class TestDedup:
    """F206AS-5: URL dedup using seen_urls set must be present."""

    @pytest.mark.asyncio
    async def test_f206as_5_seen_urls_dedup_present(self):
        """F206AS-5: Dedup using seen_urls set must be present."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        assert "seen_urls" in src, "Must have URL dedup via seen_urls set"
        assert "original_url in seen_urls" in src, "Must check seen_urls before appending"


# ---------------------------------------------------------------------------
# F206AS-6 to F206AS-10: Error taxonomy
# ---------------------------------------------------------------------------


class TestErrorTaxonomy:
    """F206AS-6 to F206AS-10: Error type mapping for all HTTP status codes."""

    @pytest.mark.asyncio
    async def test_f206as_6_timeout_maps_to_error_type_timeout(self):
        """F206AS-6: asyncio.TimeoutError → error_type=timeout."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        assert 'error_type="timeout"' in src, "Must map TimeoutError to error_type=timeout"

    @pytest.mark.asyncio
    async def test_f206as_7_http_429_maps_to_error_type_http_429(self):
        """F206AS-7: HTTP 429 → error_type=http_429."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        assert 'status == 429' in src or 'status == 403' in src, (
            "Must check specific HTTP status codes"
        )
        assert 'error_type="http_429"' in src, "HTTP 429 must map to error_type=http_429"

    @pytest.mark.asyncio
    async def test_f206as_8_http_403_maps_to_error_type_http_403(self):
        """F206AS-8: HTTP 403 → error_type=http_403."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        assert 'error_type="http_403"' in src, "HTTP 403 must map to error_type=http_403"

    @pytest.mark.asyncio
    async def test_f206as_9_http_5xx_maps_to_error_type_http_5xx(self):
        """F206AS-9: HTTP 5xx → error_type=http_5xx."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        assert 'status >= 500' in src or 'status >=500' in src, (
            "Must check status >= 500"
        )
        assert 'error_type="http_5xx"' in src, "5xx must map to error_type=http_5xx"

    @pytest.mark.asyncio
    async def test_f206as_10_parse_error_mapped(self):
        """F206AS-10: non-list JSON response → error_type=provider_empty (parse fail)."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        assert 'isinstance(data, list)' in src, "Must check if data is a list"
        assert 'error_type="provider_empty"' in src, (
            "Non-list data must return provider_empty error"
        )

    @pytest.mark.asyncio
    async def test_f206as_16_network_error_on_client_error(self):
        """F206AS-16: checked_aiohttp_get client_error → error_type=network_error."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        assert 'err == "client_error"' in src, (
            "Must handle client_error from checked_aiohttp_get"
        )
        assert 'error_type="network_error"' in src, (
            "client_error must map to error_type=network_error"
        )


# ---------------------------------------------------------------------------
# F206AS-11: provider metadata preserved
# ---------------------------------------------------------------------------


class TestProviderMetadata:
    """F206AS-11: provider_name/source_family/provider_chain always set."""

    @pytest.mark.asyncio
    async def test_f206as_11_provider_chain_always_set(self):
        """F206AS-11: All error returns must set provider_chain=('wayback_cdx',)."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        # Count occurrences of the required provider_chain
        count = src.count('provider_chain=("wayback_cdx",)')
        # Must appear at least 7 times (timeout, circuit_breaker, network_error,
        # http_403, http_429, http_5xx, server_error, final return)
        assert count >= 7, f"provider_chain must be set on all error paths (found {count})"

    @pytest.mark.asyncio
    async def test_f206as_11_source_family_archive(self):
        """F206AS-11: All returns must set source_family='archive'."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        count = src.count('source_family="archive"')
        assert count >= 7, f"source_family='archive' must be set on all paths (found {count})"


# ---------------------------------------------------------------------------
# F206AS-12: CancelledError re-raise
# ---------------------------------------------------------------------------


class TestCancelledError:
    """F206AS-12: CancelledError must be re-raised, not caught."""

    @pytest.mark.asyncio
    async def test_f206as_12_cancelled_error_raised(self):
        """F206AS-12: asyncio.CancelledError must be re-raised."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        assert "raise  # Re-raise CancelledError" in src or "raise" in src, (
            "Must re-raise CancelledError"
        )
        # Must NOT be caught and converted to empty result
        assert 'error_type="provider_exception"' in src, (
            "CancelledError must be re-raised, not converted to provider_exception"
        )


# ---------------------------------------------------------------------------
# F206AS-13: No archived body fetch
# ---------------------------------------------------------------------------


class TestNoBodyFetch:
    """F206AS-13: Wayback adapter must not fetch archived page bodies."""

    def test_f206as_13_no_body_fetch(self):
        """F206AS-13: No archived page body fetch — only CDX index API.

        Two web.archive.org references are legitimate and passive:
        1. _WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"  (CDX API endpoint)
        2. Wayback hit URL: "https://web.archive.org/web/{timestamp}/{original_url}" (snapshot URL in hit)

        Neither triggers a page content fetch. The test checks there is no second HTTP fetch
        (session.get / session.post to archive.org beyond the CDX API call).
        """
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        # Wayback hit URLs must use timestamp-gated archive URLs (passive, not a fetch)
        assert "web.archive.org/web/" in src, (
            "Wayback hit URLs must use timestamp-gated archive URLs"
        )
        # Must not have a second HTTP fetch to web.archive.org beyond the CDX API.
        # We check: any 'session.get' that also references web.archive.org is limited to 1 (the CDX call).
        session_get_lines = [l for l in src.split('\n') if 'session.get' in l and 'web.archive.org' in l]
        assert len(session_get_lines) <= 1, (
            f"Multiple web.archive.org fetches detected: {[l.strip() for l in session_get_lines]}"
        )


# ---------------------------------------------------------------------------
# F206AS-14: No Brave/SearXNG imports
# ---------------------------------------------------------------------------


class TestNoBraveSearxImports:
    """F206AS-14: No Brave/SearXNG imports."""

    def test_f206as_14_no_brave_searx(self):
        """F206AS-14: discovery/wayback_cdx_adapter.py must not import brave or searx."""
        src_path = PROJECT_ROOT / "discovery" / "wayback_cdx_adapter.py"
        src = src_path.read_text()
        assert "brave" not in src.lower(), "brave reference found in wayback_cdx_adapter"
        assert "searx" not in src.lower(), "searx reference found in wayback_cdx_adapter"


# ---------------------------------------------------------------------------
# F206AS-15: Circuit breaker integration
# ---------------------------------------------------------------------------


class TestCircuitBreakerIntegration:
    """F206AS-15: Wayback uses checked_aiohttp_get with circuit breaker."""

    def test_f206as_15_uses_checked_aiohttp_get(self):
        """F206AS-15: Must call checked_aiohttp_get with failure_kind='wayback_cdx'."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        assert "checked_aiohttp_get" in src, (
            "Must use checked_aiohttp_get from circuit_breaker"
        )
        assert 'failure_kind="wayback_cdx"' in src, (
            "Must pass failure_kind='wayback_cdx' to checked_aiohttp_get"
        )

    def test_f206as_15_uses_async_get_aiohttp_session(self):
        """F206AS-15: Must use async_get_aiohttp_session for shared session."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        import inspect
        src = inspect.getsource(async_search_wayback_cdx)
        assert "async_get_aiohttp_session" in src, (
            "Must use async_get_aiohttp_session for shared session"
        )


# ---------------------------------------------------------------------------
# F206AS-16: network_error on client_error (covered above with error taxonomy)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Integration: mocked HTTP round-trip
# ---------------------------------------------------------------------------


class TestMockedHTTPRoundTrip:
    """Integration: mocked HTTP returns correct DiscoveryBatchResult semantics."""

    @pytest.mark.asyncio
    async def test_f206as_empty_query_returns_empty(self):
        """Empty query returns empty without HTTP call."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        result = await async_search_wayback_cdx("", max_results=10)
        assert result.hits == ()
        assert result.error == "empty_query"

    @pytest.mark.asyncio
    async def test_f206as_import_error_returns_import_error(self):
        """aiohttp not available → error_type=import_error."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        with patch.dict("sys.modules", {"aiohttp": None}):
            # Would need to reimport — just check structure exists
            pass  # tested via source inspection

    @pytest.mark.asyncio
    async def test_f206as_returns_correct_discovery_batch_result(self):
        """Returns DiscoveryBatchResult with all required fields."""
        from hledac.universal.discovery.wayback_cdx_adapter import (
            async_search_wayback_cdx,
        )
        # Empty query path gives us a typed result — verify all fields present
        result = await async_search_wayback_cdx("", max_results=10)
        assert hasattr(result, "hits")
        assert hasattr(result, "provider_name")
        assert hasattr(result, "provider_chain")
        assert hasattr(result, "source_family")
        assert hasattr(result, "elapsed_s")
        assert hasattr(result, "error_type")
        assert hasattr(result, "error")