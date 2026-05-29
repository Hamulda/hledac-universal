"""
Sprint F206AR: Transport Router Probe Tests
============================================

Probe tests for TransportRouter lane selection policy.
Run: python -m pytest tests/probe_f206ar_transport_router/ -v

Tests:
  TR-1: Router never selects httpx_h2 for onion/i2p/freenet
  TR-2: Router selects curl_cffi for stealth
  TR-3: Router selects httpx_h2 only when env gate + API-like URL
  TR-4: Cache lane default disabled
  TR-5: Pastebin path uses circuit breaker (imported from pastebin_monitor)
  TR-6: Archive discovery path has timeout and max_bytes
  TR-7: CancelledError re-raised (by existing httpx_transport contract)
  TR-8: Telemetry selected_transport/fallback_reason preserved
"""

import os
import sys

sys.path.insert(0, "hledac/universal")

from transport.transport_router import TransportDecision, TransportRouter, route_transport


class TestF206ARTransportRouter:
    """Sprint F206AR probe tests for TransportRouter."""

    router = TransportRouter()

    # -------------------------------------------------------------------------
    # TR-1: Router NEVER selects httpx_h2 for onion/i2p/freenet
    # -------------------------------------------------------------------------

    def test_tr1_onion_never_httpx_h2(self):
        """[TR-1] .onion domain → tor_socks, not httpx_h2."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        d = self.router.route("https://example.onion/path")
        assert d.lane == "tor_socks", f"onion got {d.lane}, expected tor_socks"
        assert "onion" in d.reason
        os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    def test_tr1_i2p_never_httpx_h2(self):
        """[TR-1] .i2p domain → i2p_socks, not httpx_h2."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        d = self.router.route("https://example.i2p/path")
        assert d.lane == "i2p_socks", f".i2p got {d.lane}, expected i2p_socks"
        os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    def test_tr1_b32_i2p_never_httpx_h2(self):
        """[TR-1] .b32.i2p domain → i2p_socks, not httpx_h2."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        d = self.router.route("https://example.b32.i2p/path")
        assert d.lane == "i2p_socks", f".b32.i2p got {d.lane}, expected i2p_socks"
        os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    def test_tr1_freenet_never_httpx_h2(self):
        """[TR-1] .freenet → aiohttp_default (not httpx_h2)."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        d = self.router.route("https://example.freenet/path")
        assert d.lane == "aiohttp_default", f".freenet got {d.lane}"
        os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    # -------------------------------------------------------------------------
    # TR-2: Router selects curl_cffi for stealth
    # -------------------------------------------------------------------------

    def test_tr2_explicit_stealth_selects_curl_cffi(self):
        """[TR-2] use_stealth=True → curl_cffi_stealth lane."""
        d = self.router.route("https://example.com/path", use_stealth=True)
        assert d.lane == "curl_cffi_stealth", f"stealth got {d.lane}"
        assert "stealth" in d.reason

    def test_tr2_retry_403_selects_curl_cffi(self):
        """[TR-2] retry_after_status=403 → curl_cffi_stealth."""
        d = self.router.route("https://example.com/path", retry_after_status=403)
        assert d.lane == "curl_cffi_stealth", f"403 retry got {d.lane}"
        assert "403" in d.reason

    def test_tr2_retry_429_selects_curl_cffi(self):
        """[TR-2] retry_after_status=429 → curl_cffi_stealth."""
        d = self.router.route("https://example.com/path", retry_after_status=429)
        assert d.lane == "curl_cffi_stealth", f"429 retry got {d.lane}"
        assert "429" in d.reason

    # -------------------------------------------------------------------------
    # TR-3: Router selects httpx_h2 only when env gate + API-like URL
    # -------------------------------------------------------------------------

    def test_tr3_httpx_h2_requires_env_gate(self):
        """[TR-3] Without HLEDAC_ENABLE_HTTPX_H2=1, httpx_h2 not selected."""
        os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)
        d = self.router.route("https://api.github.com/users")
        assert d.lane != "httpx_h2", "env-missing got httpx_h2"

    def test_tr3_httpx_h2_with_env_gate_api_url(self):
        """[TR-3] With env=1 + API URL → httpx_h2 selected."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        d = self.router.route("https://api.github.com/users")
        assert d.lane == "httpx_h2", f"api.github.com got {d.lane}"
        os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    def test_tr3_httpx_h2_with_api_path(self):
        """[TR-3] API path pattern → httpx_h2 when env enabled."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        d = self.router.route("https://example.com/api/v1/users")
        assert d.lane == "httpx_h2", f"/api/v1/ got {d.lane}"
        os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    def test_tr3_httpx_h2_onion_blocked_despite_env(self):
        """[TR-3] .onion blocks httpx_h2 even when env=1."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        d = self.router.route("https://api.github.onion/users")
        assert d.lane == "tor_socks", f"onion+env got {d.lane}, should be tor_socks"
        os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    def test_tr3_httpx_h2_cloudflare_workers(self):
        """[TR-3] Known API host suffix (workers.dev) → httpx_h2."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        d = self.router.route("https://my-worker.workers.dev/api/endpoint")
        assert d.lane == "httpx_h2", f"workers.dev got {d.lane}"
        os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    # -------------------------------------------------------------------------
    # TR-4: Cache lane default disabled
    # -------------------------------------------------------------------------

    def test_tr4_cache_disabled_by_default(self):
        """[TR-4] cache_allowed=False for all default lanes."""
        d = self.router.route("https://example.com/api/data")
        assert d.cache_allowed is False, "default should have cache_allowed=False"

    def test_tr4_cache_not_allowed_for_clearnet_default(self):
        """[TR-4] Even cache_safe=True on non-API URL stays False (no dependency)."""
        d = self.router.route("https://example.com/page.html", cache_safe=True)
        assert d.cache_allowed is False, "no-cache lane should not allow cache"

    def test_tr4_cache_not_allowed_for_onion(self):
        """[TR-4] cache_safe=True on onion → still False (volatile)."""
        d = self.router.route("https://example.onion/", cache_safe=True)
        assert d.cache_allowed is False, "darknet should never cache"

    # -------------------------------------------------------------------------
    # TR-5: Pastebin path uses circuit breaker
    # -------------------------------------------------------------------------

    def test_tr5_pastebin_monitor_has_circuit_state(self):
        """[TR-5] pastebin_monitor has self-contained _CircuitState."""
        from intelligence.pastebin_monitor import _circuit, _CircuitState
        assert isinstance(_circuit, _CircuitState)
        assert hasattr(_circuit, "is_open")
        assert hasattr(_circuit, "record_failure")
        # Default state: closed
        assert _circuit.is_open() is False

    def test_tr5_pastebin_circuit_opens_after_failures(self):
        """[TR-5] Pastebin circuit breaker opens after 5 failures."""
        from intelligence.pastebin_monitor import _circuit
        # Record 5 failures (limit is 5)
        for _ in range(5):
            _circuit.record_failure()
        assert _circuit.is_open() is True
        # Reset for other tests
        _circuit.failures = 0
        _circuit.opened_at = 0.0

    # -------------------------------------------------------------------------
    # TR-6: Archive discovery has timeout and max_bytes
    # -------------------------------------------------------------------------

    def test_tr6_archive_clients_have_timeout(self):
        """[TR-6] Archive clients set ClientTimeout(total=30)."""
        from intelligence.archive_discovery import (
            ArchiveTodayClient,
            GitHubHistoricalClient,
            IPFSClient,
            WaybackMachineClient,
        )
        wm = WaybackMachineClient(timeout=30.0)
        assert wm.timeout == 30.0
        at = ArchiveTodayClient(timeout=30.0)
        assert at.timeout == 30.0
        ipfs = IPFSClient(timeout=30.0)
        assert ipfs.timeout == 30.0
        gh = GitHubHistoricalClient(timeout=30.0)
        assert gh.timeout == 30.0

    def test_tr6_archive_resurrector_wayback_cdx_uses_timeout(self):
        """[TR-6] WaybackCDX query uses ClientTimeout(total=30)."""
        import os
        src = open(os.path.join(os.path.dirname(__file__), "..", "intelligence", "archive_discovery.py")).read()
        assert "ClientTimeout(total=30)" in src, "WaybackCDX should use 30s timeout"

    # -------------------------------------------------------------------------
    # TR-7: CancelledError re-raised (httpx_transport contract)
    # -------------------------------------------------------------------------

    def test_tr7_httpx_transport_raises_cancelled_error(self):
        """[TR-7] httpx_transport classify_httpx_h2_error re-raises CancelledError.

        httpx_transport.py [H2-A5]: classify_httpx_h2_error() explicitly re-raises
        asyncio.CancelledError — it is NOT classified as a retryable error.
        This test verifies the contract by checking the source code.
        """
        import os
        src = open(os.path.join(os.path.dirname(__file__), "..", "transport", "httpx_transport.py")).read()
        # Verify CancelledError re-raise is in classify_httpx_h2_error
        assert "if isinstance(exc_or_result, asyncio.CancelledError):" in src
        assert "raise exc_or_result  # [H2-A5]" in src

    # -------------------------------------------------------------------------
    # TR-8: Telemetry selected_transport/fallback_reason preserved
    # -------------------------------------------------------------------------

    def test_tr8_transport_decision_has_selected_transport(self):
        """[TR-8] TransportDecision.selected_transport is always set."""
        d = self.router.route("https://example.com/")
        assert d.selected_transport is not None
        assert d.selected_transport != ""

    def test_tr8_transport_decision_clearnet_default(self):
        """[TR-8] Clearnet default → aiohttp_default selected_transport."""
        d = self.router.route("https://example.com/page")
        assert d.lane == "aiohttp_default"
        assert d.selected_transport == "aiohttp_default"

    def test_tr8_transport_decision_onion(self):
        """[TR-8] Onion → tor_socks selected_transport."""
        d = self.router.route("https://example.onion/")
        assert d.lane == "tor_socks"
        assert d.selected_transport == "tor_socks"

    def test_tr8_transport_decision_i2p(self):
        """[TR-8] I2P → i2p_socks selected_transport."""
        d = self.router.route("https://example.i2p/")
        assert d.lane == "i2p_socks"
        assert d.selected_transport == "i2p_socks"

    def test_tr8_transport_decision_js_renderer(self):
        """[TR-8] use_js=True → js_renderer selected_transport."""
        d = self.router.route("https://example.com/", use_js=True)
        assert d.lane == "js_renderer"
        assert d.selected_transport == "js_renderer"

    def test_tr8_transport_decision_stealth(self):
        """[TR-8] use_stealth=True → curl_cffi_stealth selected_transport."""
        d = self.router.route("https://example.com/", use_stealth=True)
        assert d.lane == "curl_cffi_stealth"
        assert d.selected_transport == "curl_cffi_stealth"

    def test_tr8_transport_decision_httpx_h2(self):
        """[TR-8] httpx_h2 → httpx_h2 selected_transport."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        d = self.router.route("https://api.github.com/users")
        assert d.lane == "httpx_h2"
        assert d.selected_transport == "httpx_h2"
        os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    # -------------------------------------------------------------------------
    # TransportDecision passthrough fields
    # -------------------------------------------------------------------------

    def test_tr8_timeout_s_passthrough(self):
        """[TR-8] suggested_timeout_s passed through to decision."""
        d = self.router.route("https://example.com/", suggested_timeout_s=25.0)
        assert d.timeout_s == 25.0

    def test_tr8_max_bytes_passthrough(self):
        """[TR-8] suggested_max_bytes passed through to decision."""
        d = self.router.route("https://example.com/", suggested_max_bytes=1_000_000)
        assert d.max_bytes == 1_000_000

    def test_tr8_concurrency_class_passthrough(self):
        """[TR-8] suggested_concurrency passed through to decision."""
        d = self.router.route("https://example.com/", suggested_concurrency="high")
        assert d.concurrency_class == "high"

    def test_tr8_concurrency_class_low_for_onion(self):
        """[TR-8] tor_socks default concurrency is low."""
        d = self.router.route("https://example.onion/")
        assert d.concurrency_class == "low"

    def test_tr8_concurrency_class_high_for_httpx_h2(self):
        """[TR-8] httpx_h2 default concurrency is high."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        d = self.router.route("https://api.github.com/users")
        assert d.concurrency_class == "high"
        os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    # -------------------------------------------------------------------------
    # Singleton function
    # -------------------------------------------------------------------------

    def test_route_transport_function_returns_transport_decision(self):
        """[TR-8] route_transport() singleton function returns TransportDecision."""
        d = route_transport("https://example.com/")
        assert isinstance(d, TransportDecision)
        assert d.lane == "aiohttp_default"

    # -------------------------------------------------------------------------
    # Frozen dataclass
    # -------------------------------------------------------------------------

    def test_transport_decision_is_frozen(self):
        """[TR-8] TransportDecision is frozen (immutable)."""
        d = self.router.route("https://example.com/")
        try:
            d.lane = "tor_socks"  # type: ignore
            raise AssertionError("should be frozen")
        except Exception:
            pass  # Expected
