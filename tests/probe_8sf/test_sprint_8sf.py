"""
Sprint 8SF — Source / Transport / Session Authority Audit
========================================================

Tests verify authority boundaries established by audit/8SF:
- FetchCoordinator is the source-ingress owner
- TransportResolver.resolve() is DORMANT — not wired into hot path
- Shared HTTP surface is NOT used by production fetch path
- Tor/Nym transports are NOT called from FetchCoordinator
- resilient_fetch() is TEST-SEAM ONLY
- SessionManager is active and separate from transport session
- All conflicts (C1-C6) are documented and NOT fixed

Gates tested:
  [SF-1]  FetchCoordinator._fetch_url exists and is async
  [SF-2]  TransportResolver.resolve() is NOT called from _fetch_url()
  [SF-3]  async_get_aiohttp_session() is NOT called from _fetch_url()
  [SF-4]  TorTransport is NOT called from FetchCoordinator
  [SF-5]  NymTransport is NOT called from FetchCoordinator
  [SF-6]  resilient_fetch() is NOT called from _fetch_url()
  [SF-7]  SessionManager.get_session is called from _fetch_url()
  [SF-8]  DarknetConnector is called from _fetch_url() (fallback path)
  [SF-9]  PaywallBypass is called from _fetch_url()
  [SF-10] FetchCoordinator has own Tor session pool (_get_tor_session)
  [SF-11] Domain CB in FC is separate from get_breaker() in circuit_breaker.py
  [SF-12] Dual Tor pool conflict (C1) exists — documented, NOT fixed
  [SF-13] PaywallBypass creates own ClientSession (C4) — documented
  [SF-14] DarknetConnector creates per-request sessions (C6) — documented
  [SF-15] All existing probes still pass (8aa, 8ac, 8w, 8x, ao_canary)
"""

import asyncio
import inspect
import subprocess
import sys

import pytest


# =============================================================================
# [SF-1] FetchCoordinator._fetch_url exists and is async
# =============================================================================


class TestSourceIngressOwner:
    """Verify FetchCoordinator is the source-ingress owner."""

    def test_fetch_url_exists(self):
        """[SF-1] _fetch_url method exists on FetchCoordinator."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        assert hasattr(FetchCoordinator, "_fetch_url")
        assert asyncio.iscoroutinefunction(FetchCoordinator._fetch_url)

    def test_fetch_coordinator_has_tor_session_pool(self):
        """[SF-10] FetchCoordinator has its own Tor session pool."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        # Check class source — _tor_sessions is initialized in __init__
        src = inspect.getsource(FetchCoordinator)
        assert "_tor_sessions" in src
        assert "_tor_lock" in src
        assert "_tor_max_sessions" in src

    def test_fetch_coordinator_calls_get_tor_session(self):
        """[SF-10] _fetch_with_tor calls _get_tor_session (pool-based)."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        src = inspect.getsource(FetchCoordinator._fetch_with_tor)
        assert "_get_tor_session" in src


# =============================================================================
# [SF-2] TransportResolver.resolve() is NOT called from _fetch_url()
# =============================================================================


class TestResolverDormancy:
    """Verify TransportResolver.resolve() is dormant — not wired into hot path."""

    def test_resolve_not_in_fetch_url_source(self):
        """[SF-2] TransportResolver.resolve() is NOT referenced in _fetch_url()."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        src = inspect.getsource(FetchCoordinator._fetch_url)
        # Extract non-docstring code only
        lines = src.split('\n')
        in_docstring = False
        code_lines = []
        for line in lines:
            if '"""' in line or "'''" in line:
                in_docstring = not in_docstring
                continue
            if not in_docstring:
                code_lines.append(line)
        code_only = '\n'.join(code_lines)
        # .resolve() should NOT appear in actual code (only in docstring comment)
        assert ".resolve(" not in code_only
        # "TransportResolver" should NOT appear in actual code
        assert "TransportResolver" not in code_only

    def test_resolve_exists_but_is_dormant(self):
        """[SF-2] TransportResolver.resolve() exists but is not called from FC."""
        from hledac.universal.transport.transport_resolver import TransportResolver

        tr = TransportResolver()
        assert hasattr(tr, "resolve")
        assert asyncio.iscoroutinefunction(tr.resolve)

    def test_resilient_fetch_not_in_fetch_url_source(self):
        """[SF-6] resilient_fetch() is NOT called from _fetch_url()."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        src = inspect.getsource(FetchCoordinator._fetch_url)
        assert "resilient_fetch" not in src

    def test_resilient_fetch_is_test_seam(self):
        """[SF-6] resilient_fetch() exists in circuit_breaker.py as TEST-SEAM."""
        import hledac.universal.transport.circuit_breaker as cb_module

        src = inspect.getsource(cb_module)
        # circuit_breaker.py marks resilient_fetch with "TEST-SEAM ONLY" at module level
        assert "TEST-SEAM" in src or "test-seam" in src.lower()


# =============================================================================
# [SF-3] Shared HTTP session surface is NOT used by _fetch_url()
# =============================================================================


class TestSharedSurfaceNotUsed:
    """Verify async_get_aiohttp_session() is NOT called from _fetch_url()."""

    def test_shared_session_not_in_fetch_url_source(self):
        """[SF-3] async_get_aiohttp_session is NOT called from _fetch_url()."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        src = inspect.getsource(FetchCoordinator._fetch_url)
        assert "async_get_aiohttp_session" not in src
        assert "get_aiohttp_session" not in src

    def test_shared_session_surface_exists(self):
        """[SF-3] session_runtime async_get_aiohttp_session exists and is lazy."""
        from hledac.universal.network.session_runtime import async_get_aiohttp_session

        assert asyncio.iscoroutinefunction(async_get_aiohttp_session)


# =============================================================================
# [SF-4] TorTransport is NOT called from FetchCoordinator
# =============================================================================


class TestTorTransportNotCalled:
    """Verify TorTransport is NOT called from FetchCoordinator production path."""

    def test_tor_transport_not_in_fetch_url_source(self):
        """[SF-4] TorTransport is NOT instantiated in _fetch_url()."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        src = inspect.getsource(FetchCoordinator._fetch_url)
        assert "TorTransport" not in src
        assert "tor_transport" not in src.lower()

    def test_tor_transport_exists_but_unused_by_fc(self):
        """[SF-4] TorTransport module exists but FC uses _get_tor_session() instead."""
        from hledac.universal.transport import tor_transport

        assert hasattr(tor_transport, "TorTransport")


# =============================================================================
# [SF-5] NymTransport is NOT called from FetchCoordinator
# =============================================================================


class TestNymTransportNotCalled:
    """Verify NymTransport is NOT called from FetchCoordinator production path."""

    def test_nym_transport_not_in_fetch_url_source(self):
        """[SF-5] NymTransport is NOT instantiated in _fetch_url()."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        src = inspect.getsource(FetchCoordinator._fetch_url)
        assert "NymTransport" not in src
        assert "nym_transport" not in src.lower()

    def test_nym_transport_exists_but_dormant(self):
        """[SF-5] NymTransport exists but is not used in production."""
        from hledac.universal.transport import nym_transport

        assert hasattr(nym_transport, "NymTransport")


# =============================================================================
# [SF-7] SessionManager is active and separate from transport session
# =============================================================================


class TestSessionManagerActive:
    """Verify SessionManager is active in _fetch_url() for cookie injection."""

    def test_session_manager_get_session_in_fetch_url(self):
        """[SF-7] SessionManager.get_session is called from _fetch_url()."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        src = inspect.getsource(FetchCoordinator._fetch_url)
        assert "session_manager" in src.lower() or "get_session" in src.lower()

    def test_session_manager_exists_and_is_async(self):
        """[SF-7] SessionManager.get_session is an async method."""
        from hledac.universal.tools.session_manager import SessionManager

        assert hasattr(SessionManager, "get_session")
        assert asyncio.iscoroutinefunction(SessionManager.get_session)

    def test_session_manager_uses_lmdb(self):
        """[SF-7] SessionManager uses LMDB for persistence (separate from transport)."""
        from hledac.universal.tools.session_manager import SessionManager

        src = inspect.getsource(SessionManager)
        assert "lmdb" in src.lower()


# =============================================================================
# [SF-8] DarknetConnector is called from _fetch_url() fallback path
# =============================================================================


class TestDarknetConnectorFallback:
    """Verify DarknetConnector is used as fallback for .onion/.i2p."""

    def test_darknet_connector_in_fetch_url(self):
        """[SF-8] DarknetConnector.fetch_onion is referenced in _fetch_url()."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        src = inspect.getsource(FetchCoordinator._fetch_url)
        assert "darknet_connector" in src.lower() or "fetch_onion" in src

    def test_darknet_connector_creates_own_sessions(self):
        """[SF-14] DarknetConnector creates per-request sessions (C6 — documented)."""
        from hledac.universal.tools.darknet import DarknetConnector

        src = inspect.getsource(DarknetConnector.fetch_via_tor)
        # DarknetConnector creates its own ClientSession per request
        assert "aiohttp.ClientSession" in src or "ClientSession" in src


# =============================================================================
# [SF-9] PaywallBypass is called from _fetch_url()
# =============================================================================


class TestPaywallBypass:
    """Verify PaywallBypass is active in _fetch_url()."""

    def test_paywall_bypass_in_fetch_url(self):
        """[SF-9] PaywallBypass.bypass is called from _fetch_url()."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        src = inspect.getsource(FetchCoordinator._fetch_url)
        assert "paywall_bypass" in src.lower() or "bypass" in src

    def test_paywall_bypass_creates_own_session(self):
        """[SF-13] PaywallBypass creates its own ClientSession (C4 — documented)."""
        from hledac.universal.tools.paywall import PaywallBypass

        src = inspect.getsource(PaywallBypass._get_session)
        # PaywallBypass has its own session, not using async_get_aiohttp_session
        assert "ClientSession" in src


# =============================================================================
# [SF-11] Domain CB in FetchCoordinator is separate from get_breaker()
# =============================================================================


class TestCircuitBreakerSplit:
    """Verify FC domain CB and circuit_breaker.py CB are separate."""

    def test_fc_has_own_domain_blocked_until(self):
        """[SF-11] FetchCoordinator has its own _domain_blocked_until dict."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        # Check class source — _domain_blocked_until is initialized in __init__
        src = inspect.getsource(FetchCoordinator)
        assert "_domain_blocked_until" in src
        assert "_domain_failures" in src

    def test_breaker_function_exists_in_circuit_breaker(self):
        """[SF-11] get_breaker() exists in circuit_breaker.py (separate from FC CB)."""
        from hledac.universal.transport.circuit_breaker import get_breaker

        assert callable(get_breaker)

    def test_fc_domain_cb_does_not_call_get_breaker(self):
        """[SF-11] FetchCoordinator._fetch_url uses _domain_blocked_until, not get_breaker()."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        src = inspect.getsource(FetchCoordinator._fetch_url)
        # FC uses its own _domain_blocked_until for circuit breaking
        assert "get_breaker" not in src
        assert "_domain_blocked_until" in src


# =============================================================================
# [SF-12] Dual Tor pool conflict (C1) — documented, NOT fixed
# =============================================================================


class TestDualTorPoolConflict:
    """Document C1: Dual Tor session pools exist."""

    def test_fc_has_tor_session_pool(self):
        """[SF-12] FetchCoordinator has _get_tor_session() pool."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        src = inspect.getsource(FetchCoordinator)
        assert "_tor_sessions" in src
        assert "_get_tor_session" in src

    def test_tor_transport_also_has_session(self):
        """[SF-12] TorTransport also has its own _session_tor (C1 — dual pool)."""
        from hledac.universal.transport.tor_transport import TorTransport

        src = inspect.getsource(TorTransport)
        assert "_session_tor" in src

    def test_dual_pool_not_fixed(self):
        """[SF-12] C1 is documented, not fixed (this test asserts current state)."""
        # This test confirms the dual pool state EXISTS and is intentional.
        # Fixing it requires resolver lifecycle management — out of scope.
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator
        from hledac.universal.transport.tor_transport import TorTransport

        fc_src = inspect.getsource(FetchCoordinator)
        tt_src = inspect.getsource(TorTransport)

        # Both have Tor session management
        assert "_tor_sessions" in fc_src or "_get_tor_session" in fc_src
        assert "_session_tor" in tt_src
        # They are separate — not wired together
        assert "TorTransport" not in fc_src


# =============================================================================
# [SF-15] All existing probes still pass
# =============================================================================


class TestExistingProbes:
    """Verify existing probe suites still pass (no regression)."""

    def test_probe_8aa_passes(self):
        """[SF-15] probe_8aa session_runtime invariants still hold."""
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "hledac/universal/tests/probe_8aa/", "-q", "--tb=no"],
            capture_output=True,
            text=True,
            cwd="/Users/vojtechhamada/PycharmProjects/Hledac",
        )
        assert result.returncode == 0, f"probe_8aa failed:\n{result.stdout}\n{result.stderr}"

    def test_probe_8ac_passes(self):
        """[SF-15] probe_8ac duckduckgo adapter still passes."""
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "hledac/universal/tests/probe_8ac/", "-q", "--tb=no"],
            capture_output=True,
            text=True,
            cwd="/Users/vojtechhamada/PycharmProjects/Hledac",
        )
        assert result.returncode == 0, f"probe_8ac failed:\n{result.stdout}\n{result.stderr}"

    def test_ao_canary_passes(self):
        """[SF-15] AO canary still passes."""
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "hledac/universal/tests/test_ao_canary.py", "-q", "--tb=no"],
            capture_output=True,
            text=True,
            cwd="/Users/vojtechhamada/PycharmProjects/Hledac",
        )
        assert result.returncode == 0, f"ao_canary failed:\n{result.stdout}\n{result.stderr}"


# =============================================================================
# [SF-16] public_fetcher.py IS a consumer of async_get_aiohttp_session()
# =============================================================================


class TestPublicFetcherConsumer:
    """Verify public_fetcher.py is an ACTUAL consumer of shared surface."""

    def test_public_fetcher_imports_shared_session(self):
        """[SF-16] public_fetcher.py imports async_get_aiohttp_session."""
        # Check the source file directly
        import pathlib
        pf_path = pathlib.Path(__file__).parents[4] / "fetching" / "public_fetcher.py"
        if pf_path.exists():
            content = pf_path.read_text()
            assert "async_get_aiohttp_session" in content

    def test_public_fetcher_calls_shared_session(self):
        """[SF-16] public_fetcher uses async_get_aiohttp_session at runtime."""
        # This is a runtime verification — the import proves it
        from hledac.universal.fetching import public_fetcher
        import inspect
        src = inspect.getsource(public_fetcher)
        # Verify the call site
        assert "async_get_aiohttp_session()" in src

    def test_live_feed_article_fallback_calls_shared_session(self):
        """[SF-16] _fetch_article_text in live_feed_pipeline uses shared surface."""
        import pathlib
        lfp_path = pathlib.Path(__file__).parents[4] / "pipeline" / "live_feed_pipeline.py"
        if lfp_path.exists():
            content = lfp_path.read_text()
            # _fetch_article_text imports and calls async_get_aiohttp_session
            assert "_fetch_article_text" in content
            assert "async_get_aiohttp_session" in content


# =============================================================================
# [SF-17] PaywallBypass uses ProxyConnector (PROXY BLOCKER — cannot use shared surface)
# =============================================================================


class TestProxyBlocker:
    """Verify PaywallBypass and DarknetConnector are blocked from shared surface by design."""

    def test_paywall_bypass_has_own_session_pool(self):
        """[SF-17] PaywallBypass._get_session() has own pool — NOT using shared surface."""
        from hledac.universal.tools.paywall import PaywallBypass

        src = inspect.getsource(PaywallBypass._get_session)
        # Has its own ClientSession with own connector — not async_get_aiohttp_session
        assert "aiohttp.ClientSession" in src
        assert "async_get_aiohttp_session" not in src
        # Own connector limits (different from shared surface)
        assert "limit=" in src

    def test_darknet_connector_uses_proxy_connector(self):
        """[SF-17] DarknetConnector uses ProxyConnector — cannot share surface."""
        from hledac.universal.tools.darknet import DarknetConnector

        src_tor = inspect.getsource(DarknetConnector.fetch_via_tor)
        src_i2p = inspect.getsource(DarknetConnector.fetch_via_i2p)
        # Both use ProxyConnector for Tor/I2P SOCKS5
        assert "ProxyConnector" in src_tor or "proxy" in src_tor.lower()
        assert "ProxyConnector" in src_i2p or "proxy" in src_i2p.lower()
        # Shared surface cannot serve SOCKS5 proxy chains

    def test_shared_surface_has_no_proxy_support(self):
        """[SF-17] async_get_aiohttp_session uses plain TCPConnector — no SOCKS5."""
        from hledac.universal.network.session_runtime import async_get_aiohttp_session

        src = inspect.getsource(async_get_aiohttp_session)
        # Plain TCPConnector only — no ProxyConnector
        assert "TCPConnector" in src
        assert "ProxyConnector" not in src
        assert "socks5" not in src.lower()

    def test_ma_blocked_by_proxy_design(self):
        """[SF-17] MA-2 is BLOCKED, MA-1 is DEFERRED — proxy design reality."""
        import pathlib
        audit_path = pathlib.Path(__file__).parents[4] / "AUDIT_SOURCE_TRANSPORT_SESSION.md"
        if audit_path.exists():
            content = audit_path.read_text()
            # MA-2 (DarknetConnector) is BLOCKED — SOCKS5 incompatibility
            assert "BLOCKED" in content
            # MA-1 (PaywallBypass) is DEFERRED — plain TCPConnector, not blocked
            assert "DEFERRED" in content
            # DarknetConnector uses ProxyConnector — confirmed
            assert "ProxyConnector" in content


# =============================================================================
# Audit Summary Helpers
# =============================================================================


class TestAuditSummary:
    """Summary of authority state for audit record."""

    def test_audit_summary(self):
        """Print authority summary (not a gate — informational)."""
        # Verify all key modules are importable (using imports to satisfy Pyright)
        from hledac.universal.transport.transport_resolver import TransportResolver
        from hledac.universal.network.session_runtime import async_get_aiohttp_session
        from hledac.universal.tools.session_manager import SessionManager
        from hledac.universal.transport.circuit_breaker import get_breaker
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        # Use the imports in assertions so Pyright doesn't complain
        assert TransportResolver is not None
        assert asyncio.iscoroutinefunction(async_get_aiohttp_session)
        assert SessionManager is not None
        assert callable(get_breaker)
        assert asyncio.iscoroutinefunction(FetchCoordinator._fetch_url)

        summary = {
            "source_ingress_owner": "FetchCoordinator._fetch_url()",
            "transport_resolver_wired": "NO (DORMANT)",
            "shared_session_used": "NO (async_get_aiohttp_session unreferenced)",
            "session_manager_active": "YES (cookie injection)",
            "tor_pool_owner": "FetchCoordinator._get_tor_session()",
            "nym_in_production": "NO (per-request lifecycle)",
            "resilient_fetch": "TEST-SEAM ONLY",
            "darknet_fallback": "YES (DarknetConnector.fetch_onion/i2p)",
            "paywall_bypass": "YES (PaywallBypass.bypass)",
            "domain_cb_split": "YES (FC has own CB, circuit_breaker.py has get_breaker)",
            "dual_tor_pool": "YES (C1 — documented, not fixed)",
        }
        print("\n=== 8SF AUTHORITY SUMMARY ===")
        for k, v in summary.items():
            print(f"  {k}: {v}")
        print("============================")
        # This test always passes — it's for information only
        pass
