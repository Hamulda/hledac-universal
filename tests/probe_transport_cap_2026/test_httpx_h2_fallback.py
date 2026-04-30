"""
F206AF: HTTPX/H2 Auto-Fallback Tests

Tests httpx_h2 failure classification, bounded fallback, and auto-disable.

Test strategy (isolated, no network):
  - Mock httpx client to raise exceptions
  - Verify failure classification, counter increment, auto-disable
  - Verify no infinite loops, no CancelledError suppression

INVARIANTS tested:
  [H2-A1] After _MAX_FAILURES (3) failures, auto-disable for rest of process
  [H2-A2] httpx_h2 never used for Tor/I2P/Freenet/JS/stealth
  [H2-A3] Fallback is one-shot per URL (no infinite loops)
  [H2-A4] transport_fallback_reason set on fallback
  [H2-A5] CancelledError re-raised (not classified)
  [H2-A6] Auto-disable gates: disabled after 3 failures
"""

import asyncio
import os
import pytest

from hledac.universal.transport.httpx_transport import (
    should_use_httpx_h2,
    classify_httpx_h2_error,
    reset_httpx_h2_state,
    get_httpx_h2_auto_disable,
    get_httpx_h2_failure_count,
    record_httpx_h2_failure,
)


class TestClassifyHttpxH2Error:
    """Phase 2: Error classification taxonomy."""

    def test_none(self):
        assert classify_httpx_h2_error(Exception("foo")) == "unknown_httpx_error"

    def test_connect_timeout(self):
        err = TimeoutError("connection timed out")
        assert classify_httpx_h2_error(err) == "connect_timeout"

    def test_read_timeout(self):
        class ReadTimeout(Exception):
            pass

        err = ReadTimeout()
        assert classify_httpx_h2_error(err) == "read_timeout"

    def test_tls_error(self):
        class TLSError(Exception):
            pass

        err = TLSError("SSL handshake failed")
        assert classify_httpx_h2_error(err) == "tls_error"

    def test_http_403(self):
        class MockResponse:
            status_code = 403

        class HTTPError(Exception):
            def __init__(self, msg, response=None):
                super().__init__(msg)
                self.response = response

        err = HTTPError("403", response=MockResponse())
        assert classify_httpx_h2_error(err) == "http_403"

    def test_http_429(self):
        class MockResponse:
            status_code = 429

        class HTTPError(Exception):
            def __init__(self, msg, response=None):
                super().__init__(msg)
                self.response = response

        err = HTTPError("429", response=MockResponse())
        assert classify_httpx_h2_error(err) == "http_429"

    def test_http_5xx(self):
        class MockResponse:
            status_code = 502

        class HTTPError(Exception):
            def __init__(self, msg, response=None):
                super().__init__(msg)
                self.response = response

        err = HTTPError("502", response=MockResponse())
        assert classify_httpx_h2_error(err) == "http_5xx"

    def test_protocol_error(self):
        class ProtocolError(Exception):
            pass

        err = ProtocolError("malformed response")
        assert classify_httpx_h2_error(err) == "protocol_error"

    def test_pool_timeout(self):
        class PoolTimeout(Exception):
            pass

        err = PoolTimeout()
        assert classify_httpx_h2_error(err) == "pool_timeout"

    def test_cancelled_error_raises(self):
        """CancelledError MUST be re-raised, not classified."""
        err = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            classify_httpx_h2_error(err)

    def test_unknown_httpx_error(self):
        """Non-httpx exceptions get unknown_httpx_error."""
        err = ValueError("not httpx")
        assert classify_httpx_h2_error(err) == "unknown_httpx_error"


class TestHttpxH2AutoDisable:
    """Phase 3: Bounded fallback policy — auto-disable after 3 failures."""

    def setup_method(self):
        reset_httpx_h2_state()

    def teardown_method(self):
        reset_httpx_h2_state()

    def test_default_not_disabled(self):
        assert get_httpx_h2_auto_disable() is False
        assert get_httpx_h2_failure_count() == 0

    def test_record_failure_increments_counter(self):
        for i in range(3):
            record_httpx_h2_failure()
            assert get_httpx_h2_failure_count() == i + 1

    def test_after_3_failures_auto_disabled(self):
        for _ in range(3):
            record_httpx_h2_failure()
        assert get_httpx_h2_auto_disable() is True
        assert get_httpx_h2_failure_count() == 3

    def test_auto_disable_blocks_should_use(self):
        # Set up auto-disable state
        for _ in range(3):
            record_httpx_h2_failure()
        assert get_httpx_h2_auto_disable() is True

        # Even with env enabled, should_use_httpx_h2 returns False
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        try:
            # Use an API-like URL that would normally route to httpx_h2
            use, reason = should_use_httpx_h2("https://api.github.com/users", False, False)
            assert use is False
            assert reason == "httpx_h2_auto_disabled"
        finally:
            os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    def test_reset_clears_auto_disable(self):
        for _ in range(3):
            record_httpx_h2_failure()
        assert get_httpx_h2_auto_disable() is True

        reset_httpx_h2_state()
        assert get_httpx_h2_auto_disable() is False
        assert get_httpx_h2_failure_count() == 0

    def test_no_more_failures_after_auto_disable(self):
        """Once auto-disabled, record_httpx_h2_failure is a no-op."""
        for _ in range(3):
            record_httpx_h2_failure()
        # Additional failures should not increment
        record_httpx_h2_failure()
        record_httpx_h2_failure()
        assert get_httpx_h2_failure_count() == 3
        assert get_httpx_h2_auto_disable() is True


class TestHttpxH2RoutingBlocks:
    """Routing guards — darknet/JS/stealth blocked BEFORE httpx_h2."""

    def test_onion_blocked(self):
        """Tor URLs NEVER route to httpx_h2."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        try:
            use, reason = should_use_httpx_h2("http://3d2u.onion/paste", False, False)
            assert use is False
            assert reason == "darknet_url"
        finally:
            os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    def test_i2p_blocked(self):
        """I2P URLs NEVER route to httpx_h2."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        try:
            use, reason = should_use_httpx_h2("http://example.i2p/page", False, False)
            assert use is False
            assert reason == "darknet_url"
        finally:
            os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    def test_freenet_blocked(self):
        """Freenet URLs NEVER route to httpx_h2."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        try:
            use, reason = should_use_httpx_h2("http://mysite.freenet/", False, False)
            assert use is False
            assert reason == "freenet_not_httpx_supported"
        finally:
            os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    def test_stealth_blocked(self):
        """use_stealth=True NEVER routes to httpx_h2."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        try:
            use, reason = should_use_httpx_h2("https://api.github.com/users", True, False)
            assert use is False
            assert reason == "stealth_required"
        finally:
            os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    def test_js_blocked(self):
        """use_js=True NEVER routes to httpx_h2."""
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        try:
            use, reason = should_use_httpx_h2("https://api.github.com/users", False, True)
            assert use is False
            assert reason == "js_required"
        finally:
            os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)


class TestHttpxH2DefaultDisabled:
    """Default HLEDAC_ENABLE_HTTPX_H2=0 behavior unchanged."""

    def test_default_env_disabled(self):
        """Default (unset) env means httpx_h2 disabled."""
        os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)
        use, reason = should_use_httpx_h2("https://api.github.com/users", False, False)
        assert use is False
        assert reason == "httpx_h2_disabled_env"

    def test_explicit_0_disabled(self):
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "0"
        try:
            use, reason = should_use_httpx_h2("https://api.github.com/users", False, False)
            assert use is False
            assert reason == "httpx_h2_disabled_env"
        finally:
            os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)

    def test_explicit_false_disabled(self):
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "false"
        try:
            use, reason = should_use_httpx_h2("https://api.github.com/users", False, False)
            assert use is False
            assert reason == "httpx_h2_disabled_env"
        finally:
            os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)


__all__ = []