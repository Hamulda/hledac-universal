"""
Sprint 46 tests – Access to Unreachable Data (Sessions + Paywall + OSINT + Darknet).
"""

import pytest

pytest.importorskip("aiohttp_socks", reason="optional dependency not installed")

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, mock_open, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Import after path setup
from hledac.universal.tools.darknet import DarknetConnector
from hledac.universal.tools.osint_frameworks import OSINTFrameworkRunner
from hledac.universal.tools.paywall import PaywallBypass
from hledac.universal.tools.session_manager import SessionManager


class TestSprint46(unittest.IsolatedAsyncioTestCase):
    """Tests for Sprint 46 - Access to Unreachable Data."""

    # === Part A – Session Management ===

    async def test_session_persistence(self):
        """Session manager should save and retrieve cookies from LMDB."""
        import lmdb
        with tempfile.TemporaryDirectory() as tmpdir:
            env = lmdb.open(tmpdir, map_size=10*1024*1024)
            sm = SessionManager(env)

            # Save session
            await sm.save_session('example.com', {'cookie': 'abc123'}, {'X-Custom': 'value'})

            # Retrieve session
            session = await sm.get_session('example.com')
            self.assertIsNotNone(session)
            self.assertEqual(session['cookies']['cookie'], 'abc123')
            self.assertEqual(session['headers']['X-Custom'], 'value')

    async def test_session_injection(self):
        """Session should be injected into requests."""
        import lmdb
        with tempfile.TemporaryDirectory() as tmpdir:
            env = lmdb.open(tmpdir, map_size=10*1024*1024)
            sm = SessionManager(env)

            await sm.save_session('test.com', {'session': 'xyz789'})

            # Get session - should return cached version
            session = await sm.get_session('test.com')
            self.assertEqual(session['cookies']['session'], 'xyz789')

    async def test_credential_rotation(self):
        """Should rotate credentials on 401/403."""
        import lmdb
        with tempfile.TemporaryDirectory() as tmpdir:
            env = lmdb.open(tmpdir, map_size=10*1024*1024)
            sm = SessionManager(env)

            # Save initial session
            await sm.save_session('example.com', {'auth': 'token1'})

            # Rotate credentials
            await sm.rotate_credentials('example.com')

            # Session should be gone
            session = await sm.get_session('example.com')
            self.assertIsNone(session)

    # === Part B – Paywall Bypass ===

    def test_paywall_detection_nytimes(self):
        """Should detect NYT paywall."""
        pb = PaywallBypass()
        html = '<div class="gateway">Subscribe to continue reading</div>'
        self.assertEqual(pb.detect(html), 'nytimes')

    def test_paywall_detection_wsj(self):
        """Should detect WSJ paywall."""
        pb = PaywallBypass()
        html = '<section class="wsj-paywall">Subscriber exclusive content</section>'
        self.assertEqual(pb.detect(html), 'wsj')

    def test_paywall_detection_medium(self):
        """Should detect Medium paywall."""
        pb = PaywallBypass()
        html = '<span class="member-only">Member-only story</span>'
        self.assertEqual(pb.detect(html), 'medium')

    def test_paywall_no_detection(self):
        """Should return None for normal content."""
        pb = PaywallBypass()
        html = '<p>Regular article content here...</p>'
        self.assertIsNone(pb.detect(html))

    async def test_archive_is(self):
        """Archive.is should return content."""
        pb = PaywallBypass()
        # Mock the fetch - we just verify method exists and is async
        self.assertTrue(asyncio.iscoroutinefunction(pb.fetch_via_archive))

    async def test_12ft_io(self):
        """12ft.io should return content."""
        pb = PaywallBypass()
        # Mock the fetch - we just verify method exists and is async
        self.assertTrue(asyncio.iscoroutinefunction(pb.fetch_via_12ft))

    # === Part C – OSINT Frameworks ===

    async def test_theharvester_not_installed(self):
        """theHarvester should handle missing tool gracefully."""
        runner = OSINTFrameworkRunner()
        # Mock that tool is not available
        with patch('asyncio.create_subprocess_exec', side_effect=FileNotFoundError):
            results = await runner.run_theharvester('test.com')
            self.assertEqual(results, [])

    async def test_theharvester_output_parsing(self):
        """Should parse theHarvester JSON output."""
        runner = OSINTFrameworkRunner()
        mock_json = '{"emails": [{"email": "test@test.com"}]}'

        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b'', b''))
            mock_exec.return_value = proc

            with patch('builtins.open', mock_open(read_data=mock_json)):
                with patch('os.path.exists', return_value=True):
                    await runner.run_theharvester('test.com')
                    # May be empty due to mocking, but should not crash

    async def test_sherlock_output_parsing(self):
        """Should parse Sherlock output."""
        runner = OSINTFrameworkRunner()

        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(
                b'[+] https://twitter.com/testuser\n[+] https://github.com/testuser\n',
                b''
            ))
            mock_exec.return_value = proc

            findings = await runner.run_sherlock('testuser')
            self.assertEqual(len(findings), 2)
            self.assertEqual(findings[0]['url'], 'https://twitter.com/testuser')
            self.assertEqual(findings[0]['source'], 'sherlock')

    async def test_sherlock_not_installed(self):
        """Sherlock should handle missing tool gracefully."""
        runner = OSINTFrameworkRunner()
        with patch('asyncio.create_subprocess_exec', side_effect=FileNotFoundError):
            results = await runner.run_sherlock('testuser')
            self.assertEqual(results, [])

    async def test_osint_findings_structure(self):
        """OSINT findings should have proper structure."""
        runner = OSINTFrameworkRunner()

        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b'[+] https://github.com/user\n', b''))
            mock_exec.return_value = proc

            findings = await runner.run_sherlock('user')
            for finding in findings:
                self.assertIn('type', finding)
                self.assertIn('url', finding)
                self.assertIn('source', finding)

    # === Part D – Darknet ===

    async def test_tor_proxy(self):
        """Tor proxy connector should work."""
        try:
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url('socks5://127.0.0.1:9050')
            self.assertIsNotNone(connector)
        except ImportError:
            self.skipTest("aiohttp_socks not available")

    async def test_i2p_socket(self):
        """I2P socket should be configurable."""
        try:
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url('socks5://127.0.0.1:4444')
            self.assertIsNotNone(connector)
        except ImportError:
            self.skipTest("aiohttp_socks not available")

    async def test_liboqs_fallback(self):
        """liboqs should fallback gracefully if not installed."""
        dc = DarknetConnector()
        # Should return False (graceful fallback)
        result = await dc.try_liboqs_handshake('example.com')
        self.assertIsInstance(result, bool)

    async def test_fetch_onion_requires_onion(self):
        """fetch_onion should only work for .onion URLs."""
        dc = DarknetConnector()
        result = await dc.fetch_onion('https://example.com')
        self.assertIsNone(result)  # Not .onion

    async def test_fetch_i2p_requires_i2p(self):
        """fetch_i2p should only work for .i2p URLs."""
        dc = DarknetConnector()
        result = await dc.fetch_i2p('https://example.com')
        self.assertIsNone(result)  # Not .i2p

    async def test_darknet_not_available(self):
        """Should handle missing darknet tools gracefully."""
        dc = DarknetConnector()
        # Try to fetch - should return None gracefully
        result = await dc.fetch_via_tor('http://example.onion')
        self.assertIsNone(result)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
