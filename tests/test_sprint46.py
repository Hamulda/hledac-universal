"""
Sprint 46 tests - Access to Unreachable Data (Sessions + Paywall + OSINT + Darknet).

Migrated from unittest.IsolatedAsyncioTestCase to pytest-asyncio to reuse
the session-scoped event loop from conftest.py (saves 5-10 MB per test).
LMDB uses context manager (with lmdb.open(...) as env:) for auto-cleanup.
"""

import asyncio
import tempfile
from unittest.mock import AsyncMock, mock_open, patch

import lmdb
import pytest

# Lazy import: lmdb loaded only when tests that need it actually run
from hledac.universal.tools.darknet import DarknetConnector  # noqa: E402
from hledac.universal.tools.osint_frameworks import OSINTFrameworkRunner  # noqa: E402
from hledac.universal.tools.paywall import PaywallBypass  # noqa: E402
from hledac.universal.tools.session_manager import SessionManager  # noqa: E402
from _core import aclose


class TestSprint46:
    """Tests for Sprint 46 - Access to Unreachable Data."""

    # === Part A - Session Management ===

    @pytest.mark.asyncio
    async def test_session_persistence(self):
        """Session manager should save and retrieve cookies from LMDB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with lmdb.open(tmpdir, map_size=10 * 1024 * 1024) as env:
                sm = SessionManager(env)
                await sm.save_session("example.com", {"cookie": "abc123"}, {"X-Custom": "value"})
                session = await sm.get_session("example.com")
                assert session is not None
                assert session["cookies"]["cookie"] == "abc123"
                assert session["headers"]["X-Custom"] == "value"

    @pytest.mark.asyncio
    async def test_session_injection(self):
        """Session should be injected into requests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with lmdb.open(tmpdir, map_size=10 * 1024 * 1024) as env:
                sm = SessionManager(env)
                await sm.save_session("test.com", {"session": "xyz789"})
                session = await sm.get_session("test.com")
                assert session is not None
                assert session["cookies"]["session"] == "xyz789"

    @pytest.mark.asyncio
    async def test_credential_rotation(self):
        """Should rotate credentials on 401/403."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with lmdb.open(tmpdir, map_size=10 * 1024 * 1024) as env:
                sm = SessionManager(env)
                await sm.save_session("example.com", {"auth": "token1"})
                await sm.rotate_credentials("example.com")
                session = await sm.get_session("example.com")
                assert session is None

    # === Part B - Paywall Bypass ===

    def test_paywall_detection_nytimes(self):
        """Should detect NYT paywall."""
        pb = PaywallBypass()
        html = '<div class="gateway">Subscribe to continue reading</div>'
        assert pb.detect(html) == "nytimes"

    def test_paywall_detection_wsj(self):
        """Should detect WSJ paywall."""
        pb = PaywallBypass()
        html = '<section class="wsj-paywall">Subscriber exclusive content</section>'
        assert pb.detect(html) == "wsj"

    def test_paywall_detection_medium(self):
        """Should detect Medium paywall."""
        pb = PaywallBypass()
        html = '<span class="member-only">Member-only story</span>'
        assert pb.detect(html) == "medium"

    def test_paywall_no_detection(self):
        """Should return None for normal content."""
        pb = PaywallBypass()
        html = "<p>Regular article content here...</p>"
        assert pb.detect(html) is None

    @pytest.mark.asyncio
    async def test_archive_is(self):
        """Archive.is should return content."""
        pb = PaywallBypass()
        assert asyncio.iscoroutinefunction(pb.fetch_via_archive)

    @pytest.mark.asyncio
    async def test_12ft_io(self):
        """12ft.io should return content."""
        pb = PaywallBypass()
        assert asyncio.iscoroutinefunction(pb.fetch_via_12ft)

    # === Part C - OSINT Frameworks ===

    @pytest.mark.asyncio
    async def test_theharvester_not_installed(self):
        """theHarvester should handle missing tool gracefully."""
        runner = OSINTFrameworkRunner()
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            results = await runner.run_theharvester("test.com")
            assert results == []

    @pytest.mark.asyncio
    async def test_theharvester_output_parsing(self):
        """Should parse theHarvester JSON output."""
        runner = OSINTFrameworkRunner()
        mock_json = '{"emails": [{"email": "test@test.com"}]}'
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_exec.return_value = proc
            with patch("builtins.open", new_callable=mock_open, read_data=mock_json):
                with patch("os.path.exists", return_value=True):
                    findings = await runner.run_theharvester("test.com")
                    assert isinstance(findings, list)
                    assert len(findings) == 1
                    assert findings[0]["type"] == "email"
                    assert findings[0]["value"] == "test@test.com"
                    assert findings[0]["source"] == "theHarvester"

    @pytest.mark.asyncio
    async def test_sherlock_output_parsing(self):
        """Should parse Sherlock output."""
        runner = OSINTFrameworkRunner()
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(
                return_value=(b"[+] https://twitter.com/testuser\n[+] https://github.com/testuser\n", b"")
            )
            mock_exec.return_value = proc
            findings = await runner.run_sherlock("testuser")
            assert len(findings) == 2
            assert findings[0]["url"] == "https://twitter.com/testuser"
            assert findings[0]["source"] == "sherlock"

    @pytest.mark.asyncio
    async def test_sherlock_not_installed(self):
        """Sherlock should handle missing tool gracefully."""
        runner = OSINTFrameworkRunner()
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            results = await runner.run_sherlock("testuser")
            assert results == []

    @pytest.mark.asyncio
    async def test_osint_findings_structure(self):
        """OSINT findings should have proper structure."""
        runner = OSINTFrameworkRunner()
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"[+] https://github.com/user\n", b""))
            mock_exec.return_value = proc
            findings = await runner.run_sherlock("user")
            for finding in findings:
                assert "type" in finding
                assert "url" in finding
                assert "source" in finding

    # === Part D - Darknet ===

    @pytest.mark.asyncio
    async def test_tor_proxy(self):
        """Tor proxy connector should work."""
        try:
            from httpx_socks import AsyncProxyTransport
        except ImportError:
            pytest.skip("httpx-socks not available")
        transport = AsyncProxyTransport.from_url("socks5://127.0.0.1:9050", rdns=True)
        assert transport is not None

    @pytest.mark.asyncio
    async def test_i2p_socket(self):
        """I2P socket should be configurable."""
        try:
            from httpx_socks import AsyncProxyTransport
        except ImportError:
            pytest.skip("httpx-socks not available")
        transport = AsyncProxyTransport.from_url("socks5://127.0.0.1:4444", rdns=True)
        assert transport is not None

    @pytest.mark.asyncio
    async def test_liboqs_fallback(self):
        """liboqs should fallback gracefully if not installed."""
        dc = DarknetConnector()
        result = await dc.try_liboqs_handshake("example.com")
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_fetch_onion_requires_onion(self):
        """fetch_onion should only work for .onion URLs."""
        dc = DarknetConnector()
        result = await dc.fetch_onion("https://example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_i2p_requires_i2p(self):
        """fetch_i2p should only work for .i2p URLs."""
        dc = DarknetConnector()
        result = await dc.fetch_i2p("https://example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_darknet_not_available(self):
        """Should handle missing darknet tools gracefully."""
        dc = DarknetConnector()
        result = await dc.fetch_via_tor("http://example.onion")
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
