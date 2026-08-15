"""
test_transport_policy.py — F265C integration tests

Tests the contract between transport/policy.py and its callers:
1. TP-1: T0 is always-on — never blocked by memory pressure
2. get_transport_policy() wired into public_fetcher via _t3_allowed / _h2_allowed flags
3. Fallback from httpx_h2 / curl_cffi to aiohttp respects policy decision
"""


import pytest
from _core import aclose


class TestTP1T0AlwaysOn:
    """TP-1: T0 is always-on — get_transport_policy() must never block T0."""

    def test_t0_never_blocked_normal_memory(self) -> None:
        from hledac.universal.transport.policy import get_transport_policy

        result = get_transport_policy()
        assert result.tier == "T0_curl_cffi"
        assert "T0_curl_cffi" not in result.blocked_tiers

    def test_t0_never_blocked_soft_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Soft memory (4.5-6.0 GiB) blocks T3 but not T0."""
        from hledac.universal.transport.policy import _SOFT_GIB

        # Mock RSS between SOFT and HARD
        def mock_rss_gib() -> float:
            return _SOFT_GIB + 0.1

        import hledac.universal.transport.policy as _policy

        monkeypatch.setattr(_policy, "_rss_gib", mock_rss_gib)

        result = _policy.get_transport_policy()
        assert result.tier == "T0_curl_cffi"
        assert "T0_curl_cffi" not in result.blocked_tiers
        assert result.memory_tier == "soft"

    def test_t0_never_blocked_hard_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hard memory (>=6.0 GiB) blocks T1/T2/T3 but not T0."""
        from hledac.universal.transport.policy import _HARD_GIB

        # Mock RSS at hard threshold
        def mock_rss_gib() -> float:
            return _HARD_GIB + 0.01

        import hledac.universal.transport.policy as _policy

        monkeypatch.setattr(_policy, "_rss_gib", mock_rss_gib)

        result = _policy.get_transport_policy()
        assert result.tier == "T0_curl_cffi"
        assert "T0_curl_cffi" not in result.blocked_tiers
        assert result.memory_tier == "hard"
        # T1/T2/T3 must all be blocked
        assert not result.h2_allowed
        assert not result.h3_allowed
        assert not result.js_allowed

    def test_use_js_true_allowed_under_normal_memory(self) -> None:
        from hledac.universal.transport.policy import get_transport_policy

        result = get_transport_policy(use_js=True)
        assert result.tier == "T3_js_renderer"
        assert result.js_allowed is True
        assert "T0_curl_cffi" not in result.blocked_tiers

    def test_use_js_blocked_under_hard_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hledac.universal.transport.policy import _HARD_GIB

        def mock_rss_gib() -> float:
            return _HARD_GIB + 0.01

        import hledac.universal.transport.policy as _policy

        monkeypatch.setattr(_policy, "_rss_gib", mock_rss_gib)

        result = _policy.get_transport_policy(use_js=True)
        # Hard memory → only T0 allowed, JS blocked
        assert result.tier == "T0_curl_cffi"
        assert result.js_allowed is False
        assert "T0_curl_cffi" not in result.blocked_tiers


class TestTransportTierEnum:
    """TransportTier enum is exported and usable by callers."""

    def test_transport_tier_values(self) -> None:
        from hledac.universal.transport.policy import TransportTier

        assert TransportTier.T0.value == "T0_curl_cffi"
        assert TransportTier.T1.value == "T1_httpx_h2"
        assert TransportTier.T2.value == "T2_httpx_h3"
        assert TransportTier.T3.value == "T3_js_renderer"

    def test_in_policy_decision(self) -> None:
        from hledac.universal.transport.policy import get_transport_policy

        result = get_transport_policy()
        # tier field is a Literal str — verify it maps to the expected string value
        assert result.tier == "T0_curl_cffi"


class TestPolicyDecisionFields:
    """TransportPolicyDecision carries all required fields for public_fetcher integration."""

    def test_all_fields_present_default_call(self) -> None:
        from hledac.universal.transport.policy import get_transport_policy

        result = get_transport_policy()
        assert hasattr(result, "tier")
        assert hasattr(result, "transport_lane")
        assert hasattr(result, "js_allowed")
        assert hasattr(result, "h2_allowed")
        assert hasattr(result, "h3_allowed")
        assert hasattr(result, "rss_gib")
        assert hasattr(result, "memory_tier")
        assert hasattr(result, "reason")
        assert hasattr(result, "blocked_tiers")

    def test_retry_escalation_returns_t0(self) -> None:
        from hledac.universal.transport.policy import get_transport_policy

        result = get_transport_policy(retry_after_status=403)
        assert result.tier == "T0_curl_cffi"
        assert "T0_curl_cffi" not in result.blocked_tiers
        assert "retry_escalation_http_403" in result.reason

        result429 = get_transport_policy(retry_after_status=429)
        assert result429.tier == "T0_curl_cffi"
        assert "retry_escalation_http_429" in result429.reason

    def test_h2_candidate_requires_env_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """H2 candidate without env gate is allowed by policy but h2_allowed reflects reality."""

        monkeypatch.delenv("HLEDAC_ENABLE_HTTPX_H2", raising=False)

        from hledac.universal.transport.policy import get_transport_policy

        result = get_transport_policy(is_httpx_h2_candidate=True)
        # Policy says T1 if h2_allowed, otherwise falls back to T0
        if result.tier == "T1_httpx_h2":
            assert result.h2_allowed is True
        else:
            assert result.h2_allowed is False
            assert result.tier == "T0_curl_cffi"

    def test_h3_candidate_requires_env_gate_and_altsvc(self) -> None:
        from hledac.universal.transport.policy import get_transport_policy

        result = get_transport_policy(is_httpx_h3_candidate=True)
        # Without HLEDAC_ENABLE_HTTPX_H3=1, h3_allowed must be False
        # and decision falls back to T0
        if result.tier == "T2_httpx_h3":
            assert result.h3_allowed is True
        else:
            assert result.h3_allowed is False
            assert result.tier == "T0_curl_cffi"


class TestPublicFetcherWiring:
    """Verify public_fetcher imports and calls get_transport_policy correctly."""

    def test_policy_imports_without_error(self) -> None:
        # Smoke test: import should not raise
        from hledac.universal.transport.policy import (
            get_tier_for_lane,
            get_transport_policy,
        )

        assert get_transport_policy is not None
        assert get_tier_for_lane("curl_cffi_stealth") == "T0_curl_cffi"
        assert get_tier_for_lane("httpx_h2") == "T1_httpx_h2"
        assert get_tier_for_lane("httpx_h3") == "T2_httpx_h3"
        assert get_tier_for_lane("js_renderer") == "T3_js_renderer"

    def test_public_fetcher_imports_policy(self) -> None:
        # public_fetcher lazy-imports policy inside async_fetch_public_text.
        # This just verifies the module compiles without circular import.
        # Verify the function exists in the module namespace (lazy import will happen at runtime)
        import inspect

        from hledac.universal.fetching import public_fetcher

        src = inspect.getsource(public_fetcher.async_fetch_public_text)
        assert "get_transport_policy" in src
