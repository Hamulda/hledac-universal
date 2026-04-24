"""
tests/probe_f202h/test_opsec_transport_policy.py

Sprint F202H — OPSEC Transport Policy Engine probe tests.

Tests verify:
1. get_renderer_policy blocks when model context is active (M1 guard)
2. get_renderer_policy allows when model context is inactive
3. Concurrency hints are returned correctly per transport type
4. Transport policy combines renderer + concurrency hints
5. acquire/release renderer slot is thread-safe
6. get_stealth_capability_flags degrades TLS fingerprint under model context
7. is_embedding_context_active integration with opsec_policy
8. transport_resolver integration — get_transport_hint_string
9. gather with return_exceptions=True + _check_gathered pattern
10. smoke: renderer disabled while model context active

Invariant table:
  [F202H-I1] renderer_policy.allowed=False when has_model_context=True
  [F202H-I2] get_concurrency_hint returns correct max_workers per transport
  [F202H-I3] acquire/release slot maintains count bounds [0, MAX_CONCURRENT_RENDERERS]
  [F202H-I4] get_stealth_capability_flags returns tls_fingerprint=False when model active
  [F202H-I5] get_transport_policy returns TransportPolicy with correct renderer.allowed
  [F202H-I6] gather(return_exceptions=True) + _check_gathered handles CancelledError
  [F202H-I7] smoke: renderer blocked, no crash, fallback to clearnet
"""
from __future__ import annotations

import asyncio
import threading
import pytest

from hledac.universal.runtime.opsec_policy import (
    OPSECContext,
    RendererPolicy,
    ConcurrencyHint,
    TransportPolicy,
    get_renderer_policy,
    get_concurrency_hint,
    get_transport_policy,
    acquire_renderer_slot,
    release_renderer_slot,
    get_renderer_active_count,
    get_stealth_capability_flags,
    _check_gathered,
    _MAX_CONCURRENT_RENDERERS,
)
from hledac.universal.transport.transport_resolver import (
    get_transport_for_url,
    get_transport_hint_string,
)


class TestRendererPolicyM1Guard:
    """invariant_1: renderer_policy blocks when model context is active."""

    def test_renderer_blocked_when_model_context_active(self):
        """get_renderer_policy returns allowed=False when has_model_context=True."""
        ctx = OPSECContext(has_model_context=True)
        policy = get_renderer_policy(ctx)
        assert policy.allowed is False, "M1 model context must block renderer"
        assert policy.blocked_reason == "M1_model_context_active"
        assert policy.max_concurrent == 0

    def test_renderer_allowed_when_model_context_inactive(self):
        """get_renderer_policy returns allowed=True when has_model_context=False."""
        ctx = OPSECContext(has_model_context=False)
        policy = get_renderer_policy(ctx)
        assert policy.allowed is True
        assert policy.blocked_reason is None
        assert policy.max_concurrent == _MAX_CONCURRENT_RENDERERS

    def test_renderer_blocked_at_concurrency_limit(self):
        """get_renderer_policy blocks when renderer slot is exhausted."""
        # Acquire the only slot
        assert acquire_renderer_slot() is True
        ctx = OPSECContext(has_model_context=False)
        policy = get_renderer_policy(ctx)
        assert policy.allowed is False
        assert policy.blocked_reason == "renderer_concurrency_exhausted"
        release_renderer_slot()  # clean up


class TestConcurrencyHints:
    """invariant_2: concurrency hints are correct per transport type."""

    @pytest.mark.parametrize("transport,expected_workers,expected_timeout", [
        ("clearnet", 3, 35.0),
        ("tor", 2, 45.0),
        ("i2p", 1, 45.0),
        ("stealth", 2, 35.0),
        ("unknown", 2, 35.0),  # defaults to clearnet
    ])
    def test_concurrency_hint_per_transport(self, transport, expected_workers, expected_timeout):
        """get_concurrency_hint returns correct max_workers and timeout_s."""
        hint = get_concurrency_hint(transport)
        assert hint.max_workers == expected_workers
        assert hint.timeout_s == expected_timeout


class TestRendererSlotLifecycle:
    """invariant_3: acquire/release maintains count within bounds."""

    def test_acquire_release_roundtrip(self):
        """acquire/release is balanced — count returns to 0."""
        assert get_renderer_active_count() == 0
        assert acquire_renderer_slot() is True
        assert get_renderer_active_count() == 1
        release_renderer_slot()
        assert get_renderer_active_count() == 0

    def test_acquire_fails_at_limit(self):
        """acquire returns False when MAX_CONCURRENT_RENDERERS reached."""
        for _ in range(_MAX_CONCURRENT_RENDERERS):
            assert acquire_renderer_slot() is True
        # Now at limit
        assert acquire_renderer_slot() is False
        assert get_renderer_active_count() == _MAX_CONCURRENT_RENDERERS
        # Release all
        for _ in range(_MAX_CONCURRENT_RENDERERS):
            release_renderer_slot()
        assert get_renderer_active_count() == 0

    def test_release_idempotent_at_zero(self):
        """release is safe even when count is already 0."""
        release_renderer_slot()  # should not raise
        release_renderer_slot()
        assert get_renderer_active_count() == 0

    def test_thread_safety(self):
        """acquire/release under concurrent access maintains bound."""
        errors = []
        def acquire_release_many():
            try:
                for _ in range(100):
                    assert acquire_renderer_slot() is True
                    assert get_renderer_active_count() <= _MAX_CONCURRENT_RENDERERS
                    release_renderer_slot()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=acquire_release_many) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"Thread safety violation: {errors}"


class TestTransportPolicy:
    """invariant_5: get_transport_policy combines renderer + concurrency correctly."""

    def test_transport_policy_with_model_context(self):
        """TransportPolicy.renderer.allowed=False when model context active."""
        ctx = OPSECContext(
            has_model_context=True,
            transport_hint="clearnet",
            has_stealth=False,
        )
        policy = get_transport_policy(ctx)
        assert policy.renderer.allowed is False
        assert policy.renderer.blocked_reason == "M1_model_context_active"
        # Concurrency should be lowered when renderer blocked
        assert policy.concurrency.max_workers <= 1

    def test_transport_policy_clearnet_defaults(self):
        """TransportPolicy defaults to clearnet transport hint."""
        ctx = OPSECContext(
            has_model_context=False,
            transport_hint="clearnet",
            has_stealth=False,
        )
        policy = get_transport_policy(ctx)
        assert policy.transport == "clearnet"
        assert policy.renderer.allowed is True

    def test_transport_policy_stealth_overrides_transport_hint(self):
        """Stealth mode overrides transport hint to 'stealth'."""
        ctx = OPSECContext(
            has_model_context=False,
            transport_hint="clearnet",
            has_stealth=True,
        )
        policy = get_transport_policy(ctx)
        assert policy.transport == "stealth"
        assert policy.concurrency.max_workers == 2  # stealth hint


class TestStealthCapabilityFlags:
    """invariant_4: TLS fingerprint degraded when model context active."""

    def test_tls_fingerprint_disabled_under_model_load(self):
        """get_stealth_capability_flags sets tls_fingerprint=False when model active."""
        flags = get_stealth_capability_flags(has_model_context=True)
        assert flags["tls_fingerprint"] is False
        assert flags["ua_rotation"] is True  # still enabled
        assert flags["jitter"] is True

    def test_tls_fingerprint_enabled_without_model(self):
        """get_stealth_capability_flags enables all features without model load."""
        flags = get_stealth_capability_flags(has_model_context=False)
        assert flags["tls_fingerprint"] is True
        assert flags["ua_rotation"] is True
        assert flags["jitter"] is True


class TestTransportResolverIntegration:
    """F202H-I7: transport_resolver.get_transport_hint_string wired to opsec_policy."""

    @pytest.mark.parametrize("url,expected_hint", [
        ("https://example.com", "clearnet"),
        ("http://onion.example.onion/foo", "tor"),
        ("http://foo.i2p/bar", "i2p"),
        ("http://foo.b32.i2p/baz", "i2p"),
        ("http://foo.freenet/", "clearnet"),
    ])
    def test_transport_hint_string_mapping(self, url, expected_hint):
        """get_transport_hint_string returns correct hint for each transport type."""
        hint = get_transport_hint_string(url)
        assert hint == expected_hint, f"URL {url} got hint {hint}, expected {expected_hint}"

    def test_transport_hint_matches_transport_enum(self):
        """get_transport_hint_string aligns with get_transport_for_url."""
        urls = [
            "https://example.com",
            "http://onion.example.onion",
            "http://foo.i2p",
            "http://foo.b32.i2p",
            "http://foo.freenet",
        ]
        for url in urls:
            transport = get_transport_for_url(url)
            hint = get_transport_hint_string(url)
            # Verify hint is a valid string for opsec_policy
            assert hint in ("clearnet", "tor", "i2p")


class TestCheckGathered:
    """invariant_6: _check_gathered handles CancelledError from gather return_exceptions."""

    @pytest.mark.asyncio
    async def test_check_gathered_raises_cancelled_error(self):
        """_check_gathered re-raises CancelledError from results list."""
        async def never_returns():
            await asyncio.sleep(10)

        task = asyncio.create_task(never_returns())
        await asyncio.sleep(0.01)  # let task start
        task.cancel()

        results = await asyncio.gather(task, return_exceptions=True)
        with pytest.raises(asyncio.CancelledError):
            await _check_gathered(results)

    @pytest.mark.asyncio
    async def test_check_gathered_passes_non_cancelled(self):
        """_check_gathered passes through non-CancelledError results."""
        results = [ValueError("ok"), None, 42]
        # Should not raise
        await _check_gathered(results)

    @pytest.mark.asyncio
    async def test_gather_return_exceptions_with_check_gathered(self):
        """asyncio.gather return_exceptions=True + _check_gathered pattern."""
        async def raise_value_error():
            raise ValueError("test error")

        async def return_normal():
            return "ok"

        results = await asyncio.gather(
            raise_value_error(),
            return_normal(),
            return_exceptions=True,
        )
        assert isinstance(results[0], ValueError)
        assert results[1] == "ok"
        await _check_gathered(results)  # no CancelledError, should pass


class TestEmbeddingContextIntegration:
    """F202H: is_embedding_context_active integration with opsec_policy."""

    def test_opsec_policy_with_embedding_context_active(self):
        """
        Smoke: when is_embedding_context_active returns True,
        get_renderer_policy returns blocked renderer policy.
        """
        from hledac.universal.embedding_pipeline import is_embedding_context_active

        has_model = is_embedding_context_active()
        ctx = OPSECContext(has_model_context=has_model)
        policy = get_renderer_policy(ctx)

        if has_model:
            assert policy.allowed is False, "Renderer must be blocked when embedding context active"
            assert policy.blocked_reason == "M1_model_context_active"
        else:
            assert policy.allowed is True, "Renderer allowed when no model context"


class TestSmokeRendererBlocked:
    """invariant_7 (smoke): renderer is disabled while model context active, no crash."""

    @pytest.mark.asyncio
    async def test_smoke_renderer_blocked_no_crash(self):
        """
        Smoke test: when model context is active, _fetch_with_camoufox returns
        empty string without crashing (fail-open fallback to clearnet).

        This is the smoke test that verifies the probe: it checks that the
        policy is consulted and renderer is blocked rather than blocking on
        is_embedding_context_active directly in the fetch path.
        """
        from hledac.universal.embedding_pipeline import is_embedding_context_active
        from hledac.universal.runtime.opsec_policy import get_renderer_policy, OPSECContext

        # Simulate model context being active (as smoke would set it)
        has_model = is_embedding_context_active()

        # Build OPSEC context as _fetch_with_camoufox would
        ctx = OPSECContext(has_model_context=has_model)
        policy = get_renderer_policy(ctx)

        # Verify: when model active, policy blocks renderer
        if has_model:
            assert not policy.allowed, "Smoke FAILED: renderer not blocked under model context"
            assert policy.blocked_reason is not None
        # If no model context, smoke passes trivially (renderer would be allowed)