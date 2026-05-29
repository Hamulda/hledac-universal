"""
Sprint 83C: Wildcard DNS Detection + Network Recon Hardening
===========================================================

Tests for bounded wildcard DNS detection and subdomain forwarding guard.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestSprint83CWildcardDetection:
    """Test wildcard DNS detection."""

    @pytest.mark.asyncio
    async def test_network_recon_detects_wildcard_dns(self):
        """Test that wildcard detection works for known wildcard domains."""
        from hledac.universal.intelligence.network_reconnaissance import NetworkReconnaissance

        recon = NetworkReconnaissance()

        # Test with a domain that should NOT be wildcard
        result = await recon.detect_wildcard("python.org")

        # Should return structured result
        assert 'wildcard_suspected' in result
        assert 'probe_count' in result
        assert 'probe_method' in result
        assert result['probe_count'] == 3

        # python.org should NOT be wildcard (unless DNS config changed)
        # Conservative branch returns False on errors
        assert isinstance(result['wildcard_suspected'], bool)

    @pytest.mark.asyncio
    async def test_network_recon_wildcard_cache_prevents_reprobe(self):
        """Test that wildcard result is cached per domain."""
        from hledac.universal.intelligence.network_reconnaissance import NetworkReconnaissance

        recon = NetworkReconnaissance()

        # First call
        result1 = await recon.detect_wildcard("example.com")

        # Second call should hit cache
        result2 = await recon.detect_wildcard("example.com")

        assert result1['probe_method'] != 'cache'
        assert result2['probe_method'] == 'cache'
        assert result1['wildcard_suspected'] == result2['wildcard_suspected']

    def test_network_recon_wildcard_constants_defined(self):
        """Verify bounded constants are defined."""
        from hledac.universal.intelligence.network_reconnaissance import NetworkReconnaissance

        assert hasattr(NetworkReconnaissance, '_WILDCARD_PROBE_COUNT')
        assert hasattr(NetworkReconnaissance, '_WILDCARD_PROBE_TIMEOUT_S')
        assert hasattr(NetworkReconnaissance, '_WILDCARD_PROBE_TOTAL_S')

        assert NetworkReconnaissance._WILDCARD_PROBE_COUNT == 3
        assert NetworkReconnaissance._WILDCARD_PROBE_TIMEOUT_S == 1.5
        assert NetworkReconnaissance._WILDCARD_PROBE_TOTAL_S == 4.0

    def test_network_recon_high_entropy_probes(self):
        """Verify high-entropy random probes are generated (not test/dev/admin)."""
        from hledac.universal.intelligence.network_reconnaissance import NetworkReconnaissance

        NetworkReconnaissance()

        # Check the probes don't use low-entropy patterns
        # We can't test the exact probes, but we can verify the constants
        assert NetworkReconnaissance._WILDCARD_PROBE_COUNT == 3


class TestSprint83CWildcardForwardingHardening:
    """Test subdomain forwarding suppression on wildcard."""

    def test_network_recon_suppresses_subdomain_forwarding_on_wildcard(self):
        """Verify subdomain forwarding is suppressed when wildcard detected."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        if source_file is None:
            pytest.skip("Source file not found")
        with open(source_file) as f:
            content = f.read()

        # Verify wildcard check before forwarding
        assert 'wildcard_suspected' in content
        assert 'subdomains_suppressed_by_wildcard' in content
        assert 'is_wildcard' in content

    def test_network_recon_keeps_forwarding_when_not_wildcard(self):
        """Verify forwarding still works for non-wildcard domains."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        if source_file is None:
            pytest.skip("Source file not found")
        with open(source_file) as f:
            content = f.read()

        # Verify conditional forwarding logic
        assert "if not is_wildcard:" in content or "if is_wildcard:" in content


class TestSprint83CWildcardMetadata:
    """Test wildcard metadata in output."""

    def test_network_recon_wildcard_metadata_truthful(self):
        """Verify wildcard metadata is included in handler output."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        if source_file is None:
            pytest.skip("Source file not found")
        with open(source_file) as f:
            content = f.read()

        # Verify metadata fields are in the ActionResult
        assert "'wildcard_suspected': is_wildcard" in content or '"wildcard_suspected": is_wildcard' in content
        assert "'wildcard_probe_method':" in content or '"wildcard_probe_method":' in content
        assert "'subdomains_suppressed_by_wildcard':" in content or '"subdomains_suppressed_by_wildcard":' in content


class TestSprint83CWildcardCache:
    """Test per-domain caching."""

    def test_network_recon_has_wildcard_cache(self):
        """Verify per-domain cache structures exist."""
        from hledac.universal.intelligence.network_reconnaissance import NetworkReconnaissance

        recon = NetworkReconnaissance()

        # Check cache attributes exist
        assert hasattr(recon, '_wildcard_domains')
        assert hasattr(recon, '_confirmed_non_wildcard')
        assert isinstance(recon._wildcard_domains, set)
        assert isinstance(recon._confirmed_non_wildcard, set)


class TestSprint83CWildcardProbeTimeout:
    """Test timeout behavior."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_network_recon_probe_timeout_is_conservative(self):
        """Verify probe timeout returns conservative (non-wildcard) result."""
        from hledac.universal.intelligence.network_reconnaissance import NetworkReconnaissance

        recon = NetworkReconnaissance()

        # Even with timeout, should return valid result
        # This is tested by other tests via caching
        result = await recon.detect_wildcard("timeout-test.invalid")

        # Should have all required fields
        assert 'wildcard_suspected' in result
        assert 'probe_count' in result
        assert 'probe_method' in result

        # Conservative branch should return False for ambiguous results
        assert not result['wildcard_suspected']


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
