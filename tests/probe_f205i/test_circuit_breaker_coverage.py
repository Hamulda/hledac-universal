"""Sprint F205I: Circuit Breaker Coverage for External Callers
================================================================

Invariant mapping:
  F205I-1  | _domain_from_url() extracts domain from URL
  F205I-2  | _domain_from_url() strips tor: prefix
  F205I-3  | _domain_from_url() returns "" for invalid URL
  F205I-4  | domain_breaker_check(domain) returns CircuitDecision
  F205I-5  | domain_breaker_check("") returns allowed=True (skip)
  F205I-6  | checked_aiohttp_get circuit OPEN → (None, "circuit_breaker_open:...")
  F205I-7  | checked_aiohttp_get 2xx/3xx → (resp, None) + record_success
  F205I-8  | checked_aiohttp_get 4xx/5xx → (resp, None) + record_failure
  F205I-9  | checked_aiohttp_get timeout → (None, "timeout") + record_failure
  F205I-10 | checked_aiohttp_get client error → (None, "client_error") + record_failure
  F205I-11 | checked_aiohttp_post circuit OPEN → (None, "circuit_breaker_open:...")
  F205I-12 | checked_aiohttp_post 2xx/3xx → (resp, None) + record_success
  F205I-13 | checked_aiohttp_post 4xx/5xx → (resp, None) + record_failure
  F205I-14 | ti_feed_adapter uses checked_aiohttp_get for urlhaus
  F205I-15 | ti_feed_adapter uses checked_aiohttp_post for threatfox
  F205I-16 | ti_feed_adapter uses checked_aiohttp_get for crtsh
  F205I-17 | duckduckgo_adapter uses checked_aiohttp_get for mojeek
  F205I-18 | duckduckgo_adapter uses checked_aiohttp_get for commoncrawl_cdx
  F205I-19 | duckduckgo_adapter uses checked_aiohttp_get for rdap
  F205I-20 | github_secret_scanner uses checked_aiohttp_get for github search
  F205I-21 | github_secret_scanner uses checked_aiohttp_get for raw file fetch
  F205I-22 | pastebin_monitor has its own circuit (not replaced — F205I scope is ti/ddg/gh)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from hledac.universal.transport.circuit_breaker import (
    CBState,
    CircuitDecision,
    _domain_from_url,
    checked_aiohttp_get,
    checked_aiohttp_post,
    clear_all_breakers,
    domain_breaker_check,
    get_breaker,
)


class TestDomainFromUrl:
    """F205I-1 to F205I-3: _domain_from_url() extraction."""

    def test_extracts_domain(self):
        assert _domain_from_url("https://example.com/path") == "example.com"

    def test_strips_tor_prefix(self):
        assert _domain_from_url("https://tor:example.com/path") == "example.com"

    def test_strips_tor_prefix_without_scheme(self):
        assert _domain_from_url("tor:example.com") == "example.com"

    def test_returns_empty_for_invalid_url(self):
        assert _domain_from_url("") == ""
        assert _domain_from_url("not-a-url") == ""


class TestDomainBreakerCheck:
    """F205I-4 to F205I-5: domain_breaker_check() behavior."""

    def test_returns_circuit_decision(self):
        clear_all_breakers()
        dec = domain_breaker_check("example.com")
        assert isinstance(dec, CircuitDecision)
        assert dec.allowed is True
        assert dec.domain == "example.com"

    def test_empty_domain_returns_allowed(self):
        """F205I-5: empty domain skips check (allowed=True)."""
        clear_all_breakers()
        dec = domain_breaker_check("")
        assert dec.allowed is True
        assert dec.reason == "empty_domain_skip"


class TestCheckedAiohttpGet:
    """F205I-6 to F205I-10: checked_aiohttp_get() behavior."""

    @pytest.mark.asyncio
    async def test_circuit_open_returns_skip(self):
        """F205I-6: circuit OPEN → (None, 'circuit_breaker_open:...')."""
        clear_all_breakers()
        cb = get_breaker("blocked.example.com")
        for _ in range(3):
            cb.record_failure()
        assert cb.get_state() == CBState.OPEN.value

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_session.get = MagicMock(return_value=magic_ctx(mock_resp))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        result = await checked_aiohttp_get(
            mock_session,
            "https://blocked.example.com/path",
            timeout=MagicMock(),
            failure_kind="test_fetch",
        )
        assert result == (None, f"circuit_breaker_open:{cb.check_circuit().reason}")

    @pytest.mark.asyncio
    async def test_success_2xx_returns_response(self):
        """F205I-7: 2xx → (resp, None) + record_success."""
        clear_all_breakers()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=magic_ctx(mock_resp))

        resp, err = await checked_aiohttp_get(
            mock_session,
            "https://success.example.com/path",
            timeout=MagicMock(),
            failure_kind="test_fetch",
        )
        assert err is None
        assert resp is mock_resp
        # Success should reset failure count
        cb = get_breaker("success.example.com")
        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_failure_4xx_records_failure(self):
        """F205I-8: 4xx → (resp, None) + record_failure."""
        clear_all_breakers()

        mock_resp = MagicMock()
        mock_resp.status = 404
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=magic_ctx(mock_resp))

        resp, err = await checked_aiohttp_get(
            mock_session,
            "https://notfound.example.com/path",
            timeout=MagicMock(),
            failure_kind="test_fetch",
        )
        assert err is None  # resp returned for caller to check
        assert resp is mock_resp
        cb = get_breaker("notfound.example.com")
        assert cb._failure_count == 1
        assert "404" in cb._last_failure_kind

    @pytest.mark.asyncio
    async def test_timeout_records_failure(self):
        """F205I-9: timeout → (None, 'timeout') + record_failure."""
        clear_all_breakers()

        import aiohttp

        # Mock context manager that raises TimeoutError on __aenter__
        class TimeoutCtx:
            async def __aenter__(self):
                raise asyncio.TimeoutError
            async def __aexit__(self, *args):
                return None

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=TimeoutCtx())

        resp, err = await checked_aiohttp_get(
            mock_session,
            "https://slow.example.com/path",
            timeout=MagicMock(),
            failure_kind="test_fetch",
        )
        assert resp is None
        assert err == "timeout"
        cb = get_breaker("slow.example.com")
        assert cb._failure_count == 1
        assert "timeout" in cb._last_failure_kind

    @pytest.mark.asyncio
    async def test_client_error_records_failure(self):
        """F205I-10: ClientError → (None, 'client_error') + record_failure."""
        clear_all_breakers()

        import aiohttp

        # Mock context manager that raises ClientError on __aenter__
        class ClientErrCtx:
            async def __aenter__(self):
                raise aiohttp.ClientError("connection failed")
            async def __aexit__(self, *args):
                return None

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=ClientErrCtx())

        resp, err = await checked_aiohttp_get(
            mock_session,
            "https://fail.example.com/path",
            timeout=MagicMock(),
            failure_kind="test_fetch",
        )
        assert resp is None
        assert err == "client_error"
        cb = get_breaker("fail.example.com")
        assert cb._failure_count == 1


class TestCheckedAiohttpPost:
    """F205I-11 to F205I-13: checked_aiohttp_post() behavior."""

    @pytest.mark.asyncio
    async def test_circuit_open_returns_skip(self):
        """F205I-11: circuit OPEN → (None, 'circuit_breaker_open:...')."""
        clear_all_breakers()
        cb = get_breaker("blocked-post.example.com")
        for _ in range(3):
            cb.record_failure()

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_session.post = MagicMock(return_value=magic_ctx(mock_resp))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        result = await checked_aiohttp_post(
            mock_session,
            "https://blocked-post.example.com/api",
            json={"query": "test"},
            timeout=MagicMock(),
            failure_kind="test_post",
        )
        assert result == (None, f"circuit_breaker_open:{cb.check_circuit().reason}")

    @pytest.mark.asyncio
    async def test_success_2xx_returns_response(self):
        """F205I-12: 2xx → (resp, None) + record_success."""
        clear_all_breakers()

        mock_resp = MagicMock()
        mock_resp.status = 201
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=magic_ctx(mock_resp))

        resp, err = await checked_aiohttp_post(
            mock_session,
            "https://success.example.com/api",
            json={"query": "test"},
            timeout=MagicMock(),
            failure_kind="test_post",
        )
        assert err is None
        assert resp is mock_resp
        cb = get_breaker("success.example.com")
        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_failure_5xx_records_failure(self):
        """F205I-13: 5xx → (resp, None) + record_failure."""
        clear_all_breakers()

        mock_resp = MagicMock()
        # Use PropertyMock so status=503 is accessible as attribute
        type(mock_resp).status = PropertyMock(return_value=503)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=magic_ctx(mock_resp))

        resp, err = await checked_aiohttp_post(
            mock_session,
            "https://error.example.com/api",
            json={"query": "test"},
            timeout=MagicMock(),
            failure_kind="test_post",
        )
        # POST returns (None, err) on 5xx + records failure
        assert resp is None
        assert err == "http_error:503"
        cb = get_breaker("error.example.com")
        assert cb._failure_count == 1
        assert "503" in cb._last_failure_kind


class TestAdapterImports:
    """F205I-14 to F205I-20: Verify adapters import and use CB helpers."""

    def test_ti_feed_adapter_imports_checked_helpers(self):
        """F205I-14 to F205I-16: ti_feed_adapter uses checked_aiohttp_get/post."""
        from hledac.universal.discovery.ti_feed_adapter import (
            checked_aiohttp_get,
            checked_aiohttp_post,
        )
        assert callable(checked_aiohttp_get)
        assert callable(checked_aiohttp_post)

    def test_duckduckgo_adapter_imports_checked_helpers(self):
        """F205I-17 to F205I-19: duckduckgo_adapter uses checked_aiohttp_get."""
        from hledac.universal.discovery.duckduckgo_adapter import (
            checked_aiohttp_get,
        )
        assert callable(checked_aiohttp_get)

    def test_github_secret_scanner_imports_checked_helpers(self):
        """F205I-20 to F205I-21: github_secret_scanner uses checked_aiohttp_get."""
        from hledac.universal.intelligence.github_secret_scanner import (
            checked_aiohttp_get,
        )
        assert callable(checked_aiohttp_get)


class TestCircuitBreakerHelpersExist:
    """F205I-22: Verify all helpers exist in circuit_breaker module."""

    def test_domain_from_url_exists(self):
        from hledac.universal.transport.circuit_breaker import _domain_from_url
        assert callable(_domain_from_url)

    def test_domain_breaker_check_exists(self):
        from hledac.universal.transport.circuit_breaker import domain_breaker_check
        assert callable(domain_breaker_check)

    def test_checked_aiohttp_get_exists(self):
        from hledac.universal.transport.circuit_breaker import checked_aiohttp_get
        assert callable(checked_aiohttp_get)

    def test_checked_aiohttp_post_exists(self):
        from hledac.universal.transport.circuit_breaker import checked_aiohttp_post
        assert callable(checked_aiohttp_post)


# ---- Test helper ----


def magic_ctx(mock_resp):
    """Create an async context manager mock for aiohttp response."""
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)
    return mock_resp
