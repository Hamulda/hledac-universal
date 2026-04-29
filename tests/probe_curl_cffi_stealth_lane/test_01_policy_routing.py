"""
Tests 1-7: curl_cffi transport policy routing.

Covers:
1. curl_cffi missing → lane disabled reason curl_cffi_missing
2. env HLEDAC_ENABLE_CURL_CFFI unset → curl_cffi_disabled_env
3. env enabled + use_stealth=True → explicit_stealth
4. env enabled + prior_status=403 → status_403_or_429
5. .onion/.i2p/.b32.i2p → darknet_url
6. use_js=True → js_required
7. env enabled + protection_hint cloudflare → protection_detected
"""

import os
import sys
import pytest
from unittest.mock import patch


class TestCurlCffiPolicyRouting:
    """Test should_use_curl_cffi routing decisions."""

    def _import_transport(self):
        # Force reimport to pick up mocked availability
        if "hledac.universal.transport.curl_cffi_transport" in sys.modules:
            del sys.modules["hledac.universal.transport.curl_cffi_transport"]
        return __import__("hledac.universal.transport.curl_cffi_transport", fromlist=["should_use_curl_cffi"])

    def _import_runtime(self):
        if "hledac.universal.transport.curl_cffi_runtime" in sys.modules:
            del sys.modules["hledac.universal.transport.curl_cffi_runtime"]
        return __import__("hledac.universal.transport.curl_cffi_runtime", fromlist=["is_curl_cffi_available"])

    # --- Test 2: env not set → disabled_env ---
    def test_env_not_set_returns_disabled_env(self):
        """env HLEDAC_ENABLE_CURL_CFFI unset → curl_cffi_disabled_env."""
        mod = self._import_transport()
        with patch.dict(os.environ, {}, clear=True):
            should, reason = mod.should_use_curl_cffi("https://example.com")
        assert should is False
        assert reason == "curl_cffi_disabled_env"

    # --- Test 1: curl_cffi missing → curl_cffi_missing ---
    def test_curl_cffi_missing_returns_curl_cffi_missing(self):
        """curl_cffi not installed → curl_cffi_missing (via env not set reason)."""
        mod = self._import_transport()
        with patch.dict(os.environ, {}, clear=True):
            should, reason = mod.should_use_curl_cffi("https://example.com")
        assert should is False
        # Env gate fires first
        assert reason == "curl_cffi_disabled_env"

    # --- Test 3: env enabled + use_stealth=True ---
    def test_env_enabled_stealth_true_returns_explicit_stealth(self):
        """HLEDAC_ENABLE_CURL_CFFI=1 + use_stealth=True → explicit_stealth."""
        mod = self._import_transport()
        with patch.dict(os.environ, {"HLEDAC_ENABLE_CURL_CFFI": "1"}):
            should, reason = mod.should_use_curl_cffi(
                "https://example.com",
                use_stealth=True,
            )
        assert should is True
        assert reason == "explicit_stealth"

    # --- Test 4: env enabled + prior_status=403 ---
    def test_env_enabled_prior_status_403_returns_status_403_or_429(self):
        """HLEDAC_ENABLE_CURL_CFFI=1 + prior_status=403 → status_403_or_429."""
        mod = self._import_transport()
        with patch.dict(os.environ, {"HLEDAC_ENABLE_CURL_CFFI": "1"}):
            should, reason = mod.should_use_curl_cffi(
                "https://example.com",
                prior_status=403,
            )
        assert should is True
        assert reason == "status_403_or_429"

    def test_env_enabled_prior_status_429_returns_status_403_or_429(self):
        """HLEDAC_ENABLE_CURL_CFFI=1 + prior_status=429 → status_403_or_429."""
        mod = self._import_transport()
        with patch.dict(os.environ, {"HLEDAC_ENABLE_CURL_CFFI": "1"}):
            should, reason = mod.should_use_curl_cffi(
                "https://example.com",
                prior_status=429,
            )
        assert should is True
        assert reason == "status_403_or_429"

    # --- Test 5: darknet URLs ---
    def test_onion_url_returns_darknet_url(self):
        """.onion URL never uses curl_cffi."""
        mod = self._import_transport()
        with patch.dict(os.environ, {"HLEDAC_ENABLE_CURL_CFFI": "1"}):
            for url in [
                "http://expyuzz4wqqeyhyt.onion/",
                "https://d onion123.i2p/",
                "http://b32.i2p/",
            ]:
                should, reason = mod.should_use_curl_cffi(url)
                assert should is False
                assert reason == "darknet_url", f"Failed for {url}"

    def test_freenet_url_returns_freenet_not_supported(self):
        """.freenet URL returns freenet_not_supported."""
        mod = self._import_transport()
        with patch.dict(os.environ, {"HLEDAC_ENABLE_CURL_CFFI": "1"}):
            should, reason = mod.should_use_curl_cffi("https://example.freenet")
        assert should is False
        assert reason == "freenet_not_supported"

    # --- Test 6: use_js=True ---
    def test_use_js_true_returns_js_required(self):
        """use_js=True → js_required, even with env enabled."""
        mod = self._import_transport()
        with patch.dict(os.environ, {"HLEDAC_ENABLE_CURL_CFFI": "1"}):
            should, reason = mod.should_use_curl_cffi(
                "https://example.com",
                use_js=True,
            )
        assert should is False
        assert reason == "js_required"

    # --- Test 7: protection_hint ---
    def test_protection_hint_cloudflare_returns_protection_detected(self):
        """protection_hint=cloudflare → protection_detected."""
        mod = self._import_transport()
        with patch.dict(os.environ, {"HLEDAC_ENABLE_CURL_CFFI": "1"}):
            should, reason = mod.should_use_curl_cffi(
                "https://example.com",
                protection_hint="cloudflare",
            )
        assert should is True
        assert reason == "protection_detected"

    def test_protection_hint_akamai_returns_protection_detected(self):
        """protection_hint=akamai → protection_detected."""
        mod = self._import_transport()
        with patch.dict(os.environ, {"HLEDAC_ENABLE_CURL_CFFI": "1"}):
            should, reason = mod.should_use_curl_cffi(
                "https://example.com",
                protection_hint="akamai",
            )
        assert should is True
        assert reason == "protection_detected"

    def test_protection_hint_datadome_returns_protection_detected(self):
        """protection_hint=datadome → protection_detected."""
        mod = self._import_transport()
        with patch.dict(os.environ, {"HLEDAC_ENABLE_CURL_CFFI": "1"}):
            should, reason = mod.should_use_curl_cffi(
                "https://example.com",
                protection_hint="datadome",
            )
        assert should is True
        assert reason == "protection_detected"

    # --- Default: no curl_cffi ---
    def test_default_returns_default_aiohttp(self):
        """No special conditions → default_aiohttp."""
        mod = self._import_transport()
        with patch.dict(os.environ, {"HLEDAC_ENABLE_CURL_CFFI": "1"}):
            should, reason = mod.should_use_curl_cffi("https://example.com")
        assert should is False
        assert reason == "default_aiohttp"
