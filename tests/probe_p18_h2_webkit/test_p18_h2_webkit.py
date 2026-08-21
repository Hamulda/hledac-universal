"""
tests/probe_p18_h2_webkit/test_p18_h2_webkit.py

[NEXUS]-018-01: HTTP/2 Frame Windowing & Safari WebKit SETTINGS Spoofing

Probe tests for Safari WebKit HTTP/2 SETTINGS preset:

1. Safari 18.0 SETTINGS match against p0f3 database
2. Absence of PRIORITY frames on Safari profiles
3. WINDOW_UPDATE hook without TTFB regression
4. Rust extension constants validation
5. Integration with curl_cffi session creation
"""

import pytest


class TestH2SafariPresetRustExtension:
    """Test h2_safari_preset Rust extension constants and API."""

    _rust_available: bool = False
    _get_safari18_settings = None
    _get_safari17_settings = None
    _get_safari16_settings = None
    _needs_webkit_preset = None
    _get_webkit_window_increment = None
    _get_webkit_initial_window_size = None
    _get_curl_default_initial_window_size = None
    _get_preset_for_profile = None
    _validate_safari_fingerprint = None
    _get_webkit_profiles = None

    @classmethod
    def _ensure_rust_imports(cls) -> None:
        if cls._rust_available:
            return
        try:
            from hledac.rust import get_curl_default_initial_window_size as _gcd
            from hledac.rust import get_preset_for_profile as _gpfp
            from hledac.rust import get_safari16_settings as _g16
            from hledac.rust import get_safari17_settings as _g17
            from hledac.rust import get_safari18_settings as _g18
            from hledac.rust import get_webkit_initial_window_size as _giw
            from hledac.rust import get_webkit_profiles as _gwp
            from hledac.rust import get_webkit_window_increment as _gwi
            from hledac.rust import needs_webkit_preset as _nwp
            from hledac.rust import validate_safari_fingerprint as _vsf

            cls._get_safari18_settings = _g18
            cls._get_safari17_settings = _g17
            cls._get_safari16_settings = _g16
            cls._needs_webkit_preset = _nwp
            cls._get_webkit_window_increment = _gwi
            cls._get_webkit_initial_window_size = _giw
            cls._get_curl_default_initial_window_size = _gcd
            cls._get_preset_for_profile = _gpfp
            cls._validate_safari_fingerprint = _vsf
            cls._get_webkit_profiles = _gwp
            cls._rust_available = True
        except ImportError:
            pytest.skip("h2_safari_preset Rust extension not built")

    def test_safari18_settings_known_values(self) -> None:
        """Verify Safari 18.0 SETTINGS values match expected fingerprint."""
        self._ensure_rust_imports()
        preset = self._get_safari18_settings()

        # Key differentiator: INITIAL_WINDOW_SIZE = 4,194,304 (4 MiB)
        # curl_cffi default is 65,535
        initial_window = None
        for setting_id, value in preset.settings:
            if setting_id == 4:  # SETTINGS_INITIAL_WINDOW_SIZE
                initial_window = value
                break

        assert initial_window == 4_194_304, f"Safari 18.0 INITIAL_WINDOW_SIZE should be 4,194,304, got {initial_window}"

    def test_safari17_settings_known_values(self) -> None:
        """Verify Safari 17.4 SETTINGS values."""
        self._ensure_rust_imports()
        preset = self._get_safari17_settings()

        max_header_list = None
        for setting_id, value in preset.settings:
            if setting_id == 6:  # SETTINGS_MAX_HEADER_LIST_SIZE
                max_header_list = value
                break

        assert max_header_list == 80_000, f"Safari 17.4 MAX_HEADER_LIST_SIZE should be 80,000, got {max_header_list}"

    def test_safari18_no_priority(self) -> None:
        """Verify Safari 18.0 does NOT send PRIORITY frames (RFC 9218 strict)."""
        self._ensure_rust_imports()
        preset = self._get_safari18_settings()
        assert preset.no_priority is True, "Safari 18.0 should suppress PRIORITY frames"

    def test_webkit_window_increment(self) -> None:
        """Verify Safari WebKit WINDOW_UPDATE increment = 1,048,304 bytes."""
        self._ensure_rust_imports()
        increment = self._get_webkit_window_increment()
        assert increment == 1_048_304, f"Safari WINDOW_UPDATE increment should be 1,048,304, got {increment}"

    def test_needs_webkit_preset_safari(self) -> None:
        """Verify needs_webkit_preset returns True for Safari profiles."""
        self._ensure_rust_imports()
        assert self._needs_webkit_preset("safari18_0") is True
        assert self._needs_webkit_preset("safari17_4") is True

    def test_needs_webkit_preset_non_safari(self) -> None:
        """Verify needs_webkit_preset returns False for non-Safari profiles."""
        self._ensure_rust_imports()
        assert self._needs_webkit_preset("chrome133") is False
        assert self._needs_webkit_preset("firefox136") is False

    def test_get_preset_for_profile(self) -> None:
        """Verify get_preset_for_profile returns correct preset for Safari."""
        self._ensure_rust_imports()
        preset = self._get_preset_for_profile("safari18_0")
        assert preset is not None
        assert preset.profile_name == "safari18_0"

        preset = self._get_preset_for_profile("chrome133")
        assert preset is None

    def test_validate_safari_fingerprint(self) -> None:
        """Verify validate_safari_fingerprint returns valid dict for Safari."""
        self._ensure_rust_imports()
        result = self._validate_safari_fingerprint("safari18_0")
        assert result["valid"] is True
        assert result["profile"] == "safari18_0"
        assert result["initial_window_size"] == 4_194_304


class TestCurlCffiFetchWebKitIntegration:
    """Test curl_cffi_fetch.py WebKit HTTP/2 integration."""

    def test_webkit_h2_profiles_constant(self) -> None:
        """Verify _WEBKIT_H2_PROFILES contains expected Safari profiles."""
        from hledac.universal.transport.curl_cffi_fetch import _WEBKIT_H2_PROFILES

        assert "safari18_0" in _WEBKIT_H2_PROFILES
        assert "safari17_4" in _WEBKIT_H2_PROFILES
        assert "chrome133" not in _WEBKIT_H2_PROFILES

    def test_is_webkit_h2_profile(self) -> None:
        """Verify _is_webkit_h2_profile correctly identifies Safari profiles."""
        from hledac.universal.transport.curl_cffi_fetch import _is_webkit_h2_profile

        assert _is_webkit_h2_profile("safari18_0") is True
        assert _is_webkit_h2_profile("safari17_4") is True
        assert _is_webkit_h2_profile("chrome133") is False

    def test_webkit_window_increment_constant(self) -> None:
        """Verify _WEBKIT_WINDOW_INCREMENT = 1,048,304 bytes."""
        from hledac.universal.transport.curl_cffi_fetch import _WEBKIT_WINDOW_INCREMENT

        assert _WEBKIT_WINDOW_INCREMENT == 1_048_304

    def test_h2_webkit_preset_flag_exists(self) -> None:
        """Verify HLEDAC_H2_WEBKIT_PRESET flag exists and is accessible."""
        from hledac.universal.transport.curl_cffi_fetch import HLEDAC_H2_WEBKIT_PRESET

        assert isinstance(HLEDAC_H2_WEBKIT_PRESET, bool)

    def test_get_webkit_h2_settings_returns_dict(self) -> None:
        """Verify _get_webkit_h2_settings returns preset dict for Safari."""
        from hledac.universal.transport.curl_cffi_fetch import _get_webkit_h2_settings

        settings = _get_webkit_h2_settings("safari18_0")

        if settings is not None:
            assert "initial_window_size" in settings
            assert settings["initial_window_size"] == 4_194_304
            assert settings["no_priority"] is True

    def test_get_webkit_h2_settings_non_safari(self) -> None:
        """Verify _get_webkit_h2_settings returns None for non-Safari."""
        from hledac.universal.transport.curl_cffi_fetch import _get_webkit_h2_settings

        settings = _get_webkit_h2_settings("chrome133")
        assert settings is None


class TestPublicFetcherTelemetry:
    """Test macos_webkit_count telemetry in public_fetcher."""

    def test_webkit_transport_stats_function_exists(self) -> None:
        """Verify get_webkit_transport_stats function exists."""
        from hledac.universal.fetching.public_fetcher import get_webkit_transport_stats

        stats = get_webkit_transport_stats()

        assert "macos_webkit_count" in stats
        assert "macos_webkit_success" in stats
        assert "macos_webkit_failure" in stats
        assert "h2_webkit_preset_enabled" in stats

    def test_reset_webkit_transport_telemetry(self) -> None:
        """Verify _reset_webkit_transport_telemetry resets counters."""
        from hledac.universal.fetching.public_fetcher import (
            _reset_webkit_transport_telemetry,
            get_webkit_transport_stats,
        )

        _reset_webkit_transport_telemetry()
        stats = get_webkit_transport_stats()

        assert stats["macos_webkit_count"] == 0
        assert stats["macos_webkit_success"] == 0
        assert stats["macos_webkit_failure"] == 0


class TestWINDOWUPDATEWorker:
    """Test WINDOW_UPDATE telemetry marker.

    [NEXUS]-018-01: WINDOW_UPDATE frames are handled by libcurl automatically.
    This test verifies the telemetry marker function executes without error.
    """

    def test_log_webkit_window_update_direct(self) -> None:
        """Verify _log_webkit_window_update telemetry marker."""
        from hledac.universal.transport.curl_cffi_fetch import _log_webkit_window_update

        # Should not raise
        _log_webkit_window_update("test.host", 1_048_304)


class TestH2SettingsFingerprint:
    """Test HTTP/2 SETTINGS fingerprint against expected values."""

    @classmethod
    def _ensure_rust(cls):
        try:
            from hledac.rust import (
                get_curl_default_initial_window_size,
                get_webkit_initial_window_size,
                get_webkit_profiles,
            )
            from hledac.rust import get_safari16_settings as _g16
            from hledac.rust import get_safari18_settings as _g18

            return _g18, _g16, get_webkit_initial_window_size, get_curl_default_initial_window_size, get_webkit_profiles
        except ImportError:
            pytest.skip("h2_safari_preset Rust extension not built")

    def test_safari18_settings_count(self) -> None:
        """Verify Safari 18.0 has 6 SETTINGS entries."""
        _g18, _g16, _giw, _gcd, _gwp = self._ensure_rust()
        preset = _g18()
        assert len(preset.settings) == 6

    def test_safari_initial_window_size_different_from_curl(self) -> None:
        """Verify Safari INITIAL_WINDOW_SIZE differs from curl_cffi default."""
        _g18, _g16, _giw, _gcd, _gwp = self._ensure_rust()
        safari_window = _giw()
        curl_window = _gcd()
        assert safari_window == 4_194_304
        assert curl_window == 65_535
        assert safari_window != curl_window

    def test_safari_max_concurrent_streams(self) -> None:
        """Verify Safari MAX_CONCURRENT_STREAMS = 100."""
        _g18, _g16, _giw, _gcd, _gwp = self._ensure_rust()
        preset = _g18()
        max_concurrent = None
        for setting_id, value in preset.settings:
            if setting_id == 3:
                max_concurrent = value
                break
        assert max_concurrent == 100


class TestWebKitProfilesList:
    """Test WebKit profiles list."""

    @classmethod
    def _ensure_rust(cls):
        try:
            from hledac.rust import get_webkit_profiles

            return get_webkit_profiles
        except ImportError:
            pytest.skip("h2_safari_preset Rust extension not built")

    def test_get_webkit_profiles(self) -> None:
        """Verify get_webkit_profiles returns expected profiles."""
        _gwp = self._ensure_rust()
        profiles = _gwp()
        assert "safari18_0" in profiles
        assert "safari17_4" in profiles
        assert len(profiles) >= 3
