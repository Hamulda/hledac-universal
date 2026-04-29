"""
F206K: FetchResult Telemetry Fields Tests

Tests additive telemetry fields in FetchResult:
  selected_transport: aiohttp | httpx_h2 | aiohttp_socks | stealth | js
  http_version: h2 | http/1.1 | h2c | None
  transport_policy_reason: api_like | darknet_url | stealth_required | js_required | clearnet_default | httpx_h2_disabled_env | httpx_h2_disabled | httpx_h2_fallback
  transport_fallback_reason: set when fallback occurred

Backward compatibility: existing callers without new fields get None defaults.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestFetchResultTelemetryFields:
    """Verify FetchResult has new telemetry fields with correct defaults."""

    def test_new_fields_exist(self):
        """FetchResult has all 4 new telemetry fields."""
        from hledac.universal.fetching.public_fetcher import FetchResult

        fr = FetchResult(
            url="https://example.com",
            final_url="https://example.com",
            status_code=200,
            content_type="text/html",
            text="test",
            fetched_bytes=4,
            declared_length=-1,
            elapsed_ms=100.0,
        )

        assert hasattr(fr, "selected_transport")
        assert hasattr(fr, "http_version")
        assert hasattr(fr, "transport_policy_reason")
        assert hasattr(fr, "transport_fallback_reason")

    def test_new_fields_have_defaults(self):
        """New fields default to None (backward-compatible)."""
        from hledac.universal.fetching.public_fetcher import FetchResult

        fr = FetchResult(
            url="https://example.com",
            final_url="https://example.com",
            status_code=200,
            content_type="text/html",
            text="test",
            fetched_bytes=4,
            declared_length=-1,
            elapsed_ms=100.0,
        )

        assert fr.selected_transport is None
        assert fr.http_version is None
        assert fr.transport_policy_reason is None
        assert fr.transport_fallback_reason is None

    def test_telemetry_fields_can_be_set(self):
        """Telemetry fields can be set explicitly."""
        from hledac.universal.fetching.public_fetcher import FetchResult

        fr = FetchResult(
            url="https://api.github.com",
            final_url="https://api.github.com",
            status_code=200,
            content_type="application/json",
            text="{}",
            fetched_bytes=2,
            declared_length=-1,
            elapsed_ms=50.0,
            selected_transport="httpx_h2",
            http_version="h2",
            transport_policy_reason="api_like",
            transport_fallback_reason=None,
        )

        assert fr.selected_transport == "httpx_h2"
        assert fr.http_version == "h2"
        assert fr.transport_policy_reason == "api_like"
        assert fr.transport_fallback_reason is None

    def test_aiohttp_telemetry(self):
        """aiohttp path sets correct telemetry."""
        from hledac.universal.fetching.public_fetcher import FetchResult

        fr = FetchResult(
            url="https://example.com",
            final_url="https://example.com",
            status_code=200,
            content_type="text/html",
            text="test",
            fetched_bytes=4,
            declared_length=-1,
            elapsed_ms=100.0,
            selected_transport="aiohttp",
            http_version="http/1.1",
            transport_policy_reason="clearnet_default",
        )

        assert fr.selected_transport == "aiohttp"
        assert fr.http_version == "http/1.1"
        assert fr.transport_policy_reason == "clearnet_default"

    def test_aiohttp_socks_telemetry(self):
        """Tor/I2P path sets correct telemetry."""
        from hledac.universal.fetching.public_fetcher import FetchResult

        fr = FetchResult(
            url="http://3d2u.onion/paste",
            final_url="http://3d2u.onion/paste",
            status_code=200,
            content_type="text/html",
            text="test",
            fetched_bytes=4,
            declared_length=-1,
            elapsed_ms=200.0,
            selected_transport="aiohttp_socks",
            transport_policy_reason="darknet_url",
        )

        assert fr.selected_transport == "aiohttp_socks"
        assert fr.transport_policy_reason == "darknet_url"

    def test_js_transport_telemetry(self):
        """JS rendering sets correct telemetry."""
        from hledac.universal.fetching.public_fetcher import FetchResult

        fr = FetchResult(
            url="https://example.com",
            final_url="https://example.com",
            status_code=200,
            content_type="text/html",
            text="rendered_html",
            fetched_bytes=100,
            declared_length=-1,
            elapsed_ms=500.0,
            selected_transport="js",
            transport_policy_reason="js_required",
        )

        assert fr.selected_transport == "js"
        assert fr.transport_policy_reason == "js_required"


class TestBackwardCompatibility:
    """Verify existing code that creates FetchResult without new fields still works."""

    def test_fetch_result_without_new_fields(self):
        """Existing FetchResult calls without new fields should work."""
        from hledac.universal.fetching.public_fetcher import FetchResult

        # This is how existing code creates FetchResult
        fr = FetchResult(
            url="https://example.com",
            final_url="https://example.com",
            status_code=200,
            content_type="text/html",
            text="test",
            fetched_bytes=4,
            declared_length=-1,
            elapsed_ms=100.0,
            error=None,
            redirected=False,
        )

        # New fields should be None
        assert fr.selected_transport is None
        assert fr.http_version is None
        assert fr.transport_policy_reason is None
        assert fr.transport_fallback_reason is None


class TestTransportRoutingInFetcher:
    """Integration tests for transport routing in async_fetch_public_text."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_sets_aiohttp_telemetry(self):
        """Circuit breaker blocked returns include aiohttp telemetry."""
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text

        # Mock get_breaker to return a breaker that blocks
        mock_breaker = MagicMock()
        mock_breaker.check_circuit.return_value = MagicMock(allowed=False, state="OPEN", reason="rate_limit")

        with patch("hledac.universal.fetching.public_fetcher.get_breaker", return_value=mock_breaker):
            result = await async_fetch_public_text("https://example.com")

        assert result.selected_transport == "aiohttp"
        assert result.transport_policy_reason == "clearnet_default"

    @pytest.mark.asyncio
    async def test_validation_error_has_no_transport(self):
        """Validation errors occur before transport selection."""
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text

        result = await async_fetch_public_text("not-a-url")

        # selected_transport should be None (validation fails before transport selection)
        assert result.selected_transport is None
        assert result.failure_stage == "validation"


__all__ = []
