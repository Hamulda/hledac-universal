"""
Sprint F227K — AcquisitionContext is_deep_osint_m1 probe tests.

Tests:
  1. AcquisitionContext with is_deep_osint_m1=True: valid construction
  2. AcquisitionContext without is_deep_osint_m1: uses default False, no crash
  3. is_deep_osint_m1_profile() returns True for deep_osint_m1 family

INVARIANT: is_deep_osint_m1=True must activate M1 memory governor
           (RAM pressure threshold lowered to 5.5GB instead of 7GB).

Run: pytest tests/probe/test_acquisition_context_m1.py -v
"""
from __future__ import annotations

from unittest.mock import patch

from hledac.universal.runtime.acquisition_strategy import (
    AcquisitionContext,
    AcquisitionProfile,
    build_acquisition_plan,
    is_deep_osint_m1_profile,
)


def test_acquisition_context_with_is_deep_osint_m1_true():
    """AcquisitionContext with is_deep_osint_m1=True constructs without error."""
    ctx = AcquisitionContext(
        query="test.example.com",
        duration_s=180.0,
        aggressive_mode=False,
        uma_state="ok",
        swap_detected=False,
        hardware_critical=False,
        has_domain=True,
        has_url=False,
        has_crypto=False,
        has_long_duration=False,
        is_nonfeed_diagnostic=False,
        transport_degraded=False,
        stealth_ready=True,
        base_concurrency=5,
        is_academic=False,
        is_deep_osint_m1=True,
        has_ip=False,
        cid_present=False,
    )
    assert ctx.is_deep_osint_m1 is True
    # F266: FEED runs in warn state — only blocked at critical/emergency.
    # Isolated from HLEDAC_ACQUISITION_PROFILE env var interference.
    with patch.dict("os.environ", {}, clear=True):
        plan = build_acquisition_plan(
            query="test.example.com",
            duration_s=180.0,
            aggressive_mode=False,
            uma_state="ok",
            swap_detected=False,
            acquisition_profile="deep_osint_m1",
        )
    feed_plan = next((p for p in plan.plans if p.lane == "FEED"), None)
    assert feed_plan is not None, "FEED lane missing from snapshot"
    assert feed_plan.enabled is True, "FEED should be enabled when uma_state=ok"


def test_acquisition_context_default_is_false():
    """AcquisitionContext without is_deep_osint_m1 uses default False — no crash."""
    # Omit is_deep_osint_m1 — relies on default False
    ctx = AcquisitionContext(
        query="example.com",
        duration_s=180.0,
        aggressive_mode=False,
        uma_state="ok",
        swap_detected=False,
        hardware_critical=False,
        has_domain=True,
        has_url=False,
        has_crypto=False,
        has_long_duration=False,
        is_nonfeed_diagnostic=False,
        transport_degraded=False,
        stealth_ready=False,
        base_concurrency=5,
        is_academic=False,
        has_ip=False,
        cid_present=False,
    )
    assert ctx.is_deep_osint_m1 is False


def test_acquisition_context_deep_osint_m1_blocks_feed_when_hardware_critical():
    """FEED disabled when hardware_critical=True even with is_deep_osint_m1=True."""
    AcquisitionContext(
        query="test.example.com",
        duration_s=180.0,
        aggressive_mode=False,
        uma_state="critical",
        swap_detected=False,
        hardware_critical=True,
        has_domain=True,
        has_url=False,
        has_crypto=False,
        has_long_duration=False,
        is_nonfeed_diagnostic=False,
        transport_degraded=False,
        stealth_ready=True,
        base_concurrency=2,
        is_academic=False,
        is_deep_osint_m1=True,
        has_ip=False,
        cid_present=False,
    )
    plan = build_acquisition_plan(
        query="test.example.com",
        duration_s=180.0,
        aggressive_mode=False,
        uma_state="critical",
        swap_detected=False,
        acquisition_profile="deep_osint_m1",
    )
    feed_plan = next((p for p in plan.plans if p.lane == "FEED"), None)
    assert feed_plan is not None, "FEED lane missing from snapshot"
    assert feed_plan.enabled is False, "FEED should be disabled when hardware_critical"


def test_is_deep_osint_m1_profile_family():
    """Verify is_deep_osint_m1_profile() returns True for the deep_osint_m1 family."""
    assert is_deep_osint_m1_profile(AcquisitionProfile.DEEP_OSINT_M1) is True
    assert is_deep_osint_m1_profile("research") is True
    assert is_deep_osint_m1_profile("academic") is True
    assert is_deep_osint_m1_profile("geopolitical") is True
    assert is_deep_osint_m1_profile("default") is False
    assert is_deep_osint_m1_profile("nonfeed_diagnostic") is False
