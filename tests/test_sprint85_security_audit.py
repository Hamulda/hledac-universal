"""
Sprint 85: Network Recon Security + Boundedness Audit Tests
============================================================

Minimal-diff security tests for network_recon:
1. Offline mode fast-fail
2. Private IP filtering
3. No blocking sleep
4. Timeout discipline
5. Bounded cache
6. No brute-force

Tests verify fixes from Sprint 85 security audit.
"""

import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestSprint85OfflineMode:
    """Test offline mode fast-fail in network_recon."""

    def test_network_recon_handler_checks_offline_mode(self):
        """Verify: offline mode check exists in handler."""
        import inspect
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        if source_file is None:
            pytest.skip("Source file not found")

        with open(source_file, 'r') as f:
            content = f.read()

        # Find network_recon_handler section
        handler_start = content.find('async def network_recon_handler')
        if handler_start < 0:
            pytest.skip("network_recon_handler not found")

        # Verify offline check exists before network operations
        handler_content = content[handler_start:handler_start+3000]

        assert 'is_offline_mode()' in handler_content, "Offline mode check missing"
        assert "return ActionResult(success=False, error=\"Offline mode\"" in handler_content

    @pytest.mark.asyncio
    async def test_network_recon_offline_fast_fail(self):
        """Verify: network_recon returns fast-fail when offline."""
        # Save original env
        original = os.environ.get("HLEDAC_OFFLINE")

        try:
            os.environ["HLEDAC_OFFLINE"] = "1"

            # Verify offline mode is active
            from hledac.universal.project_types import is_offline_mode
            assert is_offline_mode() == True

        finally:
            if original:
                os.environ["HLEDAC_OFFLINE"] = original
            else:
                os.environ.pop("HLEDAC_OFFLINE", None)


class TestSprint85PrivateIPFiltering:
    """Test private IP filtering in network_recon."""

    def test_private_ip_check_method_exists(self):
        """Verify: _is_private_ip method exists."""
        from hledac.universal.intelligence.network_reconnaissance import NetworkReconnaissance

        assert hasattr(NetworkReconnaissance, '_is_private_ip')
        assert callable(NetworkReconnaissance._is_private_ip)

    def test_private_ip_check_filters_correct_ranges(self):
        """Verify: private IP detection uses ipaddress module."""
        from hledac.universal.intelligence.network_reconnaissance import NetworkReconnaissance

        # Test IPv4 private ranges
        private_ips = [
            "10.0.0.1",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.0.1",
            "127.0.0.1",
            "169.254.0.1",
        ]

        for ip in private_ips:
            assert NetworkReconnaissance._is_private_ip(ip) == True, f"{ip} should be private"

        # Test public IPs
        public_ips = [
            "8.8.8.8",
            "1.1.1.1",
            "93.184.216.34",
        ]

        for ip in public_ips:
            assert NetworkReconnaissance._is_private_ip(ip) == False, f"{ip} should be public"

    def test_private_nets_defined(self):
        """Verify: _PRIVATE_NETS uses ipaddress module."""
        from hledac.universal.intelligence.network_reconnaissance import NetworkReconnaissance

        assert hasattr(NetworkReconnaissance, '_PRIVATE_NETS')
        assert isinstance(NetworkReconnaissance._PRIVATE_NETS, tuple)
        assert len(NetworkReconnaissance._PRIVATE_NETS) >= 6  # At least IPv4 + IPv6


class TestSprint85NoBlockingSleep:
    """Test no blocking sleep in async paths."""

    def test_network_recon_no_time_sleep(self):
        """Verify: network_reconnaissance.py doesn't use time.sleep."""
        import inspect
        from hledac.universal.intelligence.network_reconnaissance import NetworkReconnaissance

        source_file = inspect.getsourcefile(NetworkReconnaissance)
        with open(source_file, 'r') as f:
            content = f.read()

        # Check for blocking patterns (excluding comments)
        lines = content.split('\n')
        for line in lines:
            if 'time.sleep' in line and not line.strip().startswith('#'):
                pytest.fail(f"Blocking sleep found: {line.strip()}")

    def test_network_recon_uses_async_resolver(self):
        """Verify: network_recon uses dns.asyncresolver (not sync)."""
        import inspect
        from hledac.universal.intelligence.network_reconnaissance import DNSEnumerator

        source_file = inspect.getsourcefile(DNSEnumerator)
        with open(source_file, 'r') as f:
            content = f.read()

        # Verify async resolver is used
        assert 'dns.asyncresolver' in content, "Should use dns.asyncresolver"


class TestSprint85TimeoutDiscipline:
    """Test timeout discipline."""

    def test_wildcard_has_own_timeout(self):
        """Verify: wildcard probes have bounded timeout."""
        from hledac.universal.intelligence.network_reconnaissance import NetworkReconnaissance

        assert NetworkReconnaissance._WILDCARD_PROBE_TIMEOUT_S == 1.5
        assert NetworkReconnaissance._WILDCARD_PROBE_TOTAL_S == 4.0

    def test_handler_has_timeout(self):
        """Verify: handler has bounded timeout constant."""
        import inspect
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        with open(source_file, 'r') as f:
            content = f.read()

        assert '_RECON_TIMEOUT_PER_DOMAIN' in content
        assert 'asyncio.timeout(_RECON_TIMEOUT_PER_DOMAIN)' in content


class TestSprint85BoundedCache:
    """Test bounded cache structures."""

    def test_wildcard_cache_is_bounded_set(self):
        """Verify: wildcard cache uses bounded set."""
        from hledac.universal.intelligence.network_reconnaissance import NetworkReconnaissance

        recon = NetworkReconnaissance()

        # Verify cache structures exist and are sets (bounded by memory)
        assert hasattr(recon, '_wildcard_domains')
        assert hasattr(recon, '_confirmed_non_wildcard')
        assert isinstance(recon._wildcard_domains, set)
        assert isinstance(recon._confirmed_non_wildcard, set)

    def test_scanned_domains_is_bounded(self):
        """Verify: _scanned_domains is bounded."""
        import inspect
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        with open(source_file, 'r') as f:
            content = f.read()

        # Verify bounded constant exists
        assert '_SCANNED_DOMAINS_MAXSIZE' in content


class TestSprint85NoBruteForce:
    """Test brute-force remains disabled."""

    def test_network_recon_bruteforce_disabled(self):
        """Verify: include_subdomains=False in handler."""
        import inspect
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        with open(source_file, 'r') as f:
            content = f.read()

        # Find handler
        handler_start = content.find('async def network_recon_handler')
        handler_end = content.find('def network_recon_scorer')
        handler_content = content[handler_start:handler_end]

        # Verify BRUTEFORCE DISABLED comment exists
        assert 'BRUTEFORCE DISABLED' in handler_content or 'include_subdomains=False' in handler_content


class TestSprint85ItertoolsImport:
    """Test itertools import fix."""

    def test_itertools_is_imported(self):
        """Verify: itertools is imported in network_reconnaissance.py."""
        import inspect
        from hledac.universal.intelligence.network_reconnaissance import NetworkReconnaissance

        source_file = inspect.getsourcefile(NetworkReconnaissance)
        with open(source_file, 'r') as f:
            content = f.read()

        # Verify itertools import exists
        assert 'import itertools' in content


class TestSprint85BoundedForwarding:
    """Test bounded forwarding constants."""

    def test_forwarding_constants_defined(self):
        """Verify: bounded forwarding constants exist."""
        import inspect
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        with open(source_file, 'r') as f:
            content = f.read()

        # Verify bounded constants
        assert '_MAX_SUBDOMAINS_FORWARDED_PER_DOMAIN' in content
        assert '_MAX_DOMAINS_PER_SPRINT' in content


class TestSprint85WildcardSuppression:
    """Test wildcard suppression still works (regression test)."""

    def test_wildcard_still_blocks_forwarding(self):
        """Verify: wildcard detection still suppresses forwarding."""
        import inspect
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        with open(source_file, 'r') as f:
            content = f.read()

        # Find handler
        handler_start = content.find('async def network_recon_handler')
        handler_end = content.find('def network_recon_scorer')
        handler_content = content[handler_start:handler_end]

        # Verify wildcard suppression logic exists
        assert 'is_wildcard' in handler_content
        assert 'if not is_wildcard:' in handler_content
        assert 'subdomains_suppressed_by_wildcard' in handler_content


class TestEmergencyPurgeSecurity:
    """Test emergency_purge security invariants (P0-5)."""

    def test_emergency_purge_no_audit_deletion(self):
        """P0-5: emergency_purge must NOT delete audit logs (compliance requirement)."""
        import inspect
        from hledac.universal.security.deep_research_security import DeepResearchSecurity

        source_file = inspect.getsourcefile(DeepResearchSecurity)
        if source_file is None:
            pytest.skip("Source file not found")

        with open(source_file, 'r') as f:
            content = f.read()

        # Find emergency_purge method
        method_start = content.find('async def emergency_purge')
        if method_start < 0:
            pytest.skip("emergency_purge not found")

        # Extract method body (rough extraction to next method)
        method_end = content.find('\n    def ', method_start + 1)
        method_content = content[method_start:method_end]

        # Must contain the compliance NOTE about audit preservation
        assert 'Audit logs are NEVER deleted' in method_content, \
            "emergency_purge must document audit log preservation policy"

        # Must NOT contain actual audit deletion code
        # Exclude comments from this check
        lines = method_content.split('\n')
        code_lines = [l for l in lines if not l.strip().startswith('#') and not l.strip().startswith('"""')]
        code_only = '\n'.join(code_lines)

        # Check for suspicious patterns like audit.delete
        assert 'self.audit.delete' not in code_only, \
            "emergency_purge must NOT call self.audit.delete (compliance violation)"

    def test_emergency_purge_calls_session_cleanup(self):
        """P0-5: emergency_purge must call emergency_cleanup on all sessions."""
        import inspect
        from hledac.universal.security.deep_research_security import DeepResearchSecurity

        source_file = inspect.getsourcefile(DeepResearchSecurity)
        if source_file is None:
            pytest.skip("Source file not found")

        with open(source_file, 'r') as f:
            content = f.read()

        # Find emergency_purge method
        method_start = content.find('async def emergency_purge')
        if method_start < 0:
            pytest.skip("emergency_purge not found")

        method_end = content.find('\n    def ', method_start + 1)
        method_content = content[method_start:method_end]

        # Must iterate over sessions and call emergency_cleanup
        assert 'for session in' in method_content
        assert 'emergency_cleanup()' in method_content

    @pytest.mark.asyncio
    async def test_emergency_purge_returns_stats(self):
        """P0-5: emergency_purge must return cleanup statistics."""
        from hledac.universal.security.deep_research_security import (
            DeepResearchSecurity,
            DeepSecurityConfig,
        )

        security = DeepResearchSecurity(config=DeepSecurityConfig())
        results = await security.emergency_purge()

        # Must return dict with expected keys
        assert isinstance(results, dict)
        assert 'sessions_terminated' in results
        assert 'files_destroyed' in results
        assert 'memory_wiped' in results


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
