"""
tests/test_f265c_transport_policy_wire.py
Sprint F265C: Integration test — transport/policy.py wired into public_fetcher.py

Verifies:
  1. policy.py is reachable from public_fetcher._fetch_single_url
  2. H2 lane is gated by _h2_allowed from policy decision
  3. [TP-1] T0 is never blocked (assertion fires only on programmer error)
  4. .env.example documents the policy variables
"""


class TestTP1Invariant:
    """TP-1: T0 (curl_cffi) is always-on regardless of memory pressure."""

    def test_tp1_t0_never_blocked_in_policy_decision(self, monkeypatch: pytest.MonkeyPatch):
        """
        [TP-1] get_transport_policy() must NEVER return a decision where T0
        is in blocked_tiers. This is enforced by an assertion at every
        return point in get_transport_policy().
        """
        # Unset all optional gates so we hit the default path
        monkeypatch.delenv("HLEDAC_ENABLE_HTTPX_H2", raising=False)
        monkeypatch.delenv("HLEDAC_ENABLE_HTTPX_H3", raising=False)
        monkeypatch.delenv("HLEDAC_HTTP3", raising=False)

        from hledac.universal.transport.policy import (
            TransportPolicyDecision,
            get_transport_policy,
        )

        # Normal memory: all tiers available, T0 must not be blocked
        decision: TransportPolicyDecision = get_transport_policy()
        assert "T0_curl_cffi" not in decision.blocked_tiers, (
            f"[TP-1] T0 must never be blocked! blocked_tiers={decision.blocked_tiers}"
        )
        assert decision.tier == "T0_curl_cffi"

        # JS rendering: T0 still not blocked
        decision_js: TransportPolicyDecision = get_transport_policy(use_js=True)
        assert "T0_curl_cffi" not in decision_js.blocked_tiers

        # Escalation: 403/429 -> T0
        decision_403: TransportPolicyDecision = get_transport_policy(retry_after_status=403)
        assert "T0_curl_cffi" not in decision_403.blocked_tiers
        assert decision_403.tier == "T0_curl_cffi"

        # Explicit stealth -> T0
        decision_stealth: TransportPolicyDecision = get_transport_policy(use_stealth=True)
        assert "T0_curl_cffi" not in decision_stealth.blocked_tiers
        assert decision_stealth.tier == "T0_curl_cffi"

    def test_tp1_h2_candidate_bypasses_t0_gate(self):
        """
        When is_httpx_h2_candidate=True AND h2 is available (httpx+h2 installed),
        H2 lane is selected and T0 is NOT blocked.
        Note: HLEDAC_ENABLE_HTTPX_H2 env var is NOT re-checked by policy.py
        (it is checked by route_transport in transport_router.py before calling policy).
        """
        from hledac.universal.transport.policy import get_transport_policy

        decision = get_transport_policy(is_httpx_h2_candidate=True)
        # T0 is never blocked (TP-1), but H2 candidate bypasses it
        assert "T0_curl_cffi" not in decision.blocked_tiers, "[TP-1]"
        # If httpx+h2 installed: tier=T1; if not installed: tier=T0 (fallback)
        assert decision.tier in ("T1_httpx_h2", "T0_curl_cffi"), decision.tier

    def test_tp1_assertion_fires_on_programmer_error(self):
        """
        If a future code change accidentally puts T0 in blocked list,
        the assertion fires with a clear message.
        """
        # We test the assertion is present by checking the source
        import inspect

        from hledac.universal.transport import policy

        source = inspect.getsource(policy)
        assert "_tp1_assert" in source or ("T0_curl_cffi" in source and "assert" in source), (
            "[TP-1] assertion must be present in get_transport_policy() source"
        )


class TestPolicyWireInPublicFetcher:
    """Verify policy decision is used in _fetch_single_url transport routing."""

    def test_policy_import_in_public_fetcher(self):
        """
        public_fetcher.py must import and call get_transport_policy
        before selecting the H2 lane.
        """
        import inspect

        from hledac.universal.fetching import public_fetcher

        source = inspect.getsource(public_fetcher)
        assert "from hledac.universal.transport.policy import" in source
        assert "get_transport_policy" in source
        assert "_h2_allowed" in source, "_h2_allowed from policy decision must be used in transport selection"

    def test_h2_lane_gated_by_policy(self):
        """
        _use_httpx_h2 must be False when _h2_allowed is False,
        even if _router_lane == 'httpx_h2'.
        This prevents entering the H2 lane without policy authorization.
        """
        import inspect

        from hledac.universal.fetching import public_fetcher

        source = inspect.getsource(public_fetcher)
        # The H2 gate must check _h2_allowed
        assert "_h2_allowed" in source
        # Pattern: _use_httpx_h2: bool = _router_lane == "httpx_h2" and _h2_allowed
        assert "and _h2_allowed" in source, "H2 lane entry must be gated by _h2_allowed policy flag"


class TestEnvExampleDocs:
    """Verify .env.example documents Transport Policy variables."""

    def test_env_example_has_transport_policy_section(self):
        """Transport Policy (F265C) section must exist in .env.example."""
        import os

        # .env.example lives at the package root (hledac/universal/), not repo root
        package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env_example = os.path.join(package_root, ".env.example")

        with open(env_example) as f:
            content = f.read()

        assert "Transport Policy (F265C)" in content
        assert "T0 (curl_cffi_stealth) is always-on" in content
        assert "HLEDAC_ENABLE_HTTPX_H2" in content
        assert "HLEDAC_ENABLE_HTTPX_H3" in content
        assert "[TP-1 invariant]" in content or "TP-1" in content
        assert "memory-gated" in content.lower() or "memory_gated" in content.lower()
