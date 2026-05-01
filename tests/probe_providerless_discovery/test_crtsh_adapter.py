"""
Sprint F206AU — CT/crt.sh Providerless Pivot Adapter
Probe tests for discovery/crtsh_adapter.py

Tests F206AU invariants:
AU-1  domain extraction from query
AU-2  invalid_query without network call
AU-3  crt.sh JSON parsed to DiscoveryHit
AU-4  wildcard domains deduped
AU-5  duplicate subdomains deduped
AU-6  max_results hard capped
AU-7  private/internal domains filtered
AU-8  timeout → error_type=timeout
AU-9  HTTP 429 → error_type=http_429
AU-10 HTTP 403 → error_type=http_403
AU-11 HTTP 5xx → error_type=http_5xx
AU-12 parse error → error_type=parse_error
AU-13 CancelledError re-raised
AU-14 provider metadata preserved
AU-15 no Brave/SearXNG imports
AU-16 no raw aiohttp.ClientSession() constructor
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from hledac.universal.discovery.crtsh_adapter import (
    _extract_domain_from_query,
    _is_private_domain,
    _is_wildcard_only,
    _looks_like_domain,
    _is_ip_like,
    async_search_crtsh,
    _MAX_HITS,
    _CRTSH_URL,
)


# ---------------------------------------------------------------------------
# AU-1: Domain extraction
# ---------------------------------------------------------------------------

class TestDomainExtraction:
    def test_au1_full_domain_query(self):
        assert _extract_domain_from_query("example.com") == "example.com"
        assert _extract_domain_from_query("sub.example.com") == "sub.example.com"
        assert _extract_domain_from_query("deep.sub.example.com") == "deep.sub.example.com"

    def test_au1_free_text_extracts_domain_token(self):
        # "example" has no dot → not domain-like
        assert _extract_domain_from_query("example target company") is None
        # "example.com" with dot → extracted
        res = _extract_domain_from_query("lookup example.com for research")
        assert res == "example.com"

    def test_au1_empty_query_returns_none(self):
        assert _extract_domain_from_query("") is None
        assert _extract_domain_from_query("   ") is None

    def test_au1_no_domain_like_token(self):
        assert _extract_domain_from_query("hello world foo bar") is None

    def test_au1_ip_not_extracted_as_domain(self):
        assert _extract_domain_from_query("192.168.1.1") is None
        assert _extract_domain_from_query("10.0.0.1") is None

    def test_au1_case_normalized(self):
        res = _extract_domain_from_query("LookUp ExAmple.COM")
        assert res is not None
        assert res.lower() == "example.com"


# ---------------------------------------------------------------------------
# AU-2: invalid_query without network
# ---------------------------------------------------------------------------

class TestInvalidQueryNoNetwork:
    @pytest.mark.asyncio
    async def test_au2_empty_query_no_network(self):
        with mock.patch("aiohttp.ClientSession") as mock_cs:
            mock_cs.return_value.get = mock.Mock()
        result = await async_search_crtsh("")
        assert result.error_type == "invalid_query"
        assert result.hits == ()

    @pytest.mark.asyncio
    async def test_au2_whitespace_only_no_network(self):
        result = await async_search_crtsh("   ")
        assert result.error_type == "invalid_query"

    @pytest.mark.asyncio
    async def test_au2_no_domain_token_no_network(self):
        result = await async_search_crtsh("hello world foo bar")
        assert result.error_type == "invalid_query"


# ---------------------------------------------------------------------------
# AU-4 & AU-5 & AU-6 & AU-7: JSON parsing, dedup, bounds, filtering
# ---------------------------------------------------------------------------

def _make_mock_json_response(json_data):
    """Build a mock aiohttp response with json()."""
    mock_resp = mock.AsyncMock()
    mock_resp.status = 200
    mock_resp.json = mock.AsyncMock(return_value=json_data)
    return mock_resp


def _make_mock_checked_aiohttp_get(response, err="", status=200):
    """Patch checked_aiohttp_get to return mock response."""
    def check(session, url, *, params, headers, timeout, failure_kind):
        if err:
            return (None, err)
        mock_resp = mock.AsyncMock()
        mock_resp.status = status
        if response is not None:
            mock_resp.json = mock.AsyncMock(return_value=response)
        else:
            mock_resp.json = mock.AsyncMock(side_effect=ValueError("malformed json"))
        return (mock_resp, "")
    return mock.Mock(side_effect=check)


class TestHitsFromJson:
    def _make_checked_get_mock(self, response_data, err=""):
        """Return an async mock for checked_aiohttp_get."""
        async def mock_fn(*args, **kwargs):
            if err:
                return (None, err)
            mock_resp = mock.AsyncMock()
            mock_resp.status = 200
            mock_resp.json = mock.AsyncMock(return_value=response_data)
            return (mock_resp, "")
        return mock.AsyncMock(side_effect=mock_fn)

    @pytest.mark.asyncio
    async def test_au3_parses_json_to_discovery_hit(self):
        mock_checked = self._make_checked_get_mock(
            [{"name_value": "www.example.com\napi.example.com"}]
        )
        with mock.patch("hledac.universal.discovery.crtsh_adapter.checked_aiohttp_get", mock_checked):
            result = await async_search_crtsh("example.com", max_results=5)
        assert len(result.hits) >= 1
        hit = result.hits[0]
        assert hit.source == "crtsh"
        assert hit.reason == "ct_subdomain"
        assert hit.url.startswith("https://")
        assert hit.snippet is not None
        assert hit.retrieved_ts > 0

    @pytest.mark.asyncio
    async def test_au4_wildcard_domains_filtered(self):
        mock_checked = self._make_checked_get_mock([{"name_value": "*.example.com"}])
        with mock.patch("hledac.universal.discovery.crtsh_adapter.checked_aiohttp_get", mock_checked):
            result = await async_search_crtsh("example.com")
        assert len(result.hits) == 0
        assert result.error_type == "provider_empty"

    @pytest.mark.asyncio
    async def test_au5_duplicate_subdomains_deduped(self):
        mock_checked = self._make_checked_get_mock(
            [{"name_value": "www.example.com\nwww.example.com\napi.example.com"}]
        )
        with mock.patch("hledac.universal.discovery.crtsh_adapter.checked_aiohttp_get", mock_checked):
            result = await async_search_crtsh("example.com", max_results=10)
        urls = [h.url for h in result.hits]
        assert urls.count("https://www.example.com/") == 1

    @pytest.mark.asyncio
    async def test_au6_max_results_hard_capped(self):
        many = [{"name_value": f"s{i}.example.com"} for i in range(30)]
        mock_checked = self._make_checked_get_mock(many)
        with mock.patch("hledac.universal.discovery.crtsh_adapter.checked_aiohttp_get", mock_checked):
            result = await async_search_crtsh("example.com", max_results=5)
        assert len(result.hits) <= 5

    @pytest.mark.asyncio
    async def test_au7_private_domains_filtered(self):
        mock_checked = self._make_checked_get_mock(
            [{"name_value": "localhost\n192.168.1.1\nwww.example.com"}]
        )
        with mock.patch("hledac.universal.discovery.crtsh_adapter.checked_aiohttp_get", mock_checked):
            result = await async_search_crtsh("example.com")
        urls = [h.url for h in result.hits]
        assert not any("localhost" in u for u in urls)
        assert not any("192.168" in u for u in urls)
        assert any("www.example.com" in u for u in urls)

    def test_au7_public_tlds_allowed(self):
        # "test" IS in _PRIVATE_HOSTNAMES (RFC 6761) — correct behavior
        assert _is_private_domain("test") is True
        # public domains should not be filtered
        assert _is_private_domain("example.com") is False


# ---------------------------------------------------------------------------
# AU-8: timeout → error_type=timeout
# ---------------------------------------------------------------------------

class TestTimeout:
    @pytest.mark.asyncio
    async def test_au8_timeout_error_type(self):
        async def mock_timeout(*args, **kwargs):
            return (None, "timeout")
        mock_checked = mock.AsyncMock(side_effect=mock_timeout)
        with mock.patch("hledac.universal.discovery.crtsh_adapter.checked_aiohttp_get", mock_checked):
            result = await async_search_crtsh("example.com", timeout_s=1.0)
        assert result.error_type == "timeout"


# ---------------------------------------------------------------------------
# AU-9: HTTP 429 → http_429
# ---------------------------------------------------------------------------

class TestHttp429:
    @pytest.mark.asyncio
    async def test_au9_http_429_error_type(self):
        async def mock_rate_limited(*args, **kwargs):
            return (None, "rate_limited")
        mock_checked = mock.AsyncMock(side_effect=mock_rate_limited)
        with mock.patch("hledac.universal.discovery.crtsh_adapter.checked_aiohttp_get", mock_checked):
            result = await async_search_crtsh("example.com")
        assert result.error_type == "http_429"


# ---------------------------------------------------------------------------
# AU-10: HTTP 403 → http_403
# ---------------------------------------------------------------------------

class TestHttp403:
    @pytest.mark.asyncio
    async def test_au10_http_403_error_type(self):
        async def mock_403(*args, **kwargs):
            return (None, "captcha_or_blocked")
        mock_checked = mock.AsyncMock(side_effect=mock_403)
        with mock.patch("hledac.universal.discovery.crtsh_adapter.checked_aiohttp_get", mock_checked):
            result = await async_search_crtsh("example.com")
        assert result.error_type == "http_403"


# ---------------------------------------------------------------------------
# AU-11: HTTP 5xx → http_5xx
# ---------------------------------------------------------------------------

class TestHttp5xx:
    @pytest.mark.asyncio
    async def test_au11_http_5xx_error_type(self):
        async def mock_5xx(*args, **kwargs):
            return (None, "server_error")
        mock_checked = mock.AsyncMock(side_effect=mock_5xx)
        with mock.patch("hledac.universal.discovery.crtsh_adapter.checked_aiohttp_get", mock_checked):
            result = await async_search_crtsh("example.com")
        assert result.error_type == "http_5xx"


# ---------------------------------------------------------------------------
# AU-12: parse error → parse_error
# ---------------------------------------------------------------------------

class TestParseError:
    @pytest.mark.asyncio
    async def test_au12_parse_error_error_type(self):
        async def mock_parse_err(*args, **kwargs):
            mock_resp = mock.AsyncMock()
            mock_resp.status = 200
            mock_resp.json = mock.AsyncMock(side_effect=ValueError("malformed json"))
            return (mock_resp, "")
        mock_checked = mock.AsyncMock(side_effect=mock_parse_err)
        with mock.patch("hledac.universal.discovery.crtsh_adapter.checked_aiohttp_get", mock_checked):
            result = await async_search_crtsh("example.com")
        assert result.error_type == "parse_error"


# ---------------------------------------------------------------------------
# AU-13: CancelledError re-raised
# ---------------------------------------------------------------------------

class TestCancelledError:
    @pytest.mark.asyncio
    async def test_au13_cancelled_error_re_raised(self):
        async def mock_cancelled(*args, **kwargs):
            raise asyncio.CancelledError("test cancelled")
        mock_checked = mock.AsyncMock(side_effect=mock_cancelled)
        with mock.patch("hledac.universal.discovery.crtsh_adapter.checked_aiohttp_get", mock_checked):
            with pytest.raises(asyncio.CancelledError):
                await async_search_crtsh("example.com")


# ---------------------------------------------------------------------------
# AU-14: provider metadata
# ---------------------------------------------------------------------------

class TestProviderMetadata:
    @pytest.mark.asyncio
    async def test_au14_provider_name_crtsh(self):
        async def make_ok(*args, **kwargs):
            mock_resp = mock.AsyncMock()
            mock_resp.status = 200
            mock_resp.json = mock.AsyncMock(return_value=[])
            return (mock_resp, "")
        mock_checked = mock.AsyncMock(side_effect=make_ok)
        with mock.patch("hledac.universal.discovery.crtsh_adapter.checked_aiohttp_get", mock_checked):
            result = await async_search_crtsh("example.com")
        assert result.provider_name == "crtsh"
        assert result.provider_chain == ("crtsh",)
        assert result.source_family == "ct"

    @pytest.mark.asyncio
    async def test_au14_elapsed_s_set(self):
        async def make_ok(*args, **kwargs):
            mock_resp = mock.AsyncMock()
            mock_resp.status = 200
            mock_resp.json = mock.AsyncMock(return_value=[])
            return (mock_resp, "")
        mock_checked = mock.AsyncMock(side_effect=make_ok)
        with mock.patch("hledac.universal.discovery.crtsh_adapter.checked_aiohttp_get", mock_checked):
            result = await async_search_crtsh("example.com")
        assert result.elapsed_s is not None
        assert result.elapsed_s >= 0.0

    @pytest.mark.asyncio
    async def test_au14_no_subdomains_provider_empty(self):
        async def make_empty(*args, **kwargs):
            mock_resp = mock.AsyncMock()
            mock_resp.status = 200
            mock_resp.json = mock.AsyncMock(return_value=[])
            return (mock_resp, "")
        mock_checked = mock.AsyncMock(side_effect=make_empty)
        with mock.patch("hledac.universal.discovery.crtsh_adapter.checked_aiohttp_get", mock_checked):
            result = await async_search_crtsh("example.com")
        assert result.error_type == "provider_empty"
        assert result.hits == ()


# ---------------------------------------------------------------------------
# AU-15 & AU-16: no external imports, no raw ClientSession()
# ---------------------------------------------------------------------------

class TestNoExternalImports:
    def test_au15_no_brave_imports(self):
        from hledac.universal.discovery import crtsh_adapter as m
        import inspect
        src = inspect.getsource(m)
        assert "brave" not in src.lower()
        assert " Brave" not in src

    def test_au15_no_searxng_imports(self):
        from hledac.universal.discovery import crtsh_adapter as m
        import inspect
        src = inspect.getsource(m)
        assert "searx" not in src.lower()
        assert "searxng" not in src.lower()

    def test_au16_no_raw_aiohttp_session_constructor(self):
        from hledac.universal.discovery import crtsh_adapter as m
        import inspect
        src = inspect.getsource(m)
        assert "aiohttp.ClientSession()" not in src, (
            "crtsh_adapter must not use raw aiohttp.ClientSession() constructor"
        )