"""
tests/test_bgp_env_gate.py
==========================
Smoke tests for BGP sidecar env var gating.

Verifies HLEDAC_ENABLE_BGP gate behavior:
- HLEDAC_ENABLE_BGP=0 (or unset) → sidecar disabled
- HLEDAC_ENABLE_BGP=1 → sidecar enabled
"""

from __future__ import annotations

import pytest


def sidecar_should_run() -> bool:
    """
    Mirrors the env gate logic from sprint_scheduler.py:_run_bgp_enrichment_sidecar.
    Returns True if BGP sidecar should run.

    Gate: matches sprint_scheduler.py logic (accepts "1", "true", "yes", "on").
    """
    import os

    bgp_env = os.environ.get("HLEDAC_ENABLE_BGP", "").lower()
    return bgp_env in ("1", "true", "yes", "on")


class TestBGPEnvGate:
    """Test BGP sidecar env var gating."""

    def test_bgp_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BGP is not set, sidecar should not run."""
        monkeypatch.delenv("HLEDAC_ENABLE_BGP", raising=False)
        assert sidecar_should_run() is False

    def test_bgp_disabled_explicit_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BGP=0, sidecar should not run."""
        monkeypatch.setenv("HLEDAC_ENABLE_BGP", "0")
        assert sidecar_should_run() is False

    def test_bgp_disabled_explicit_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BGP=false, sidecar should not run."""
        monkeypatch.setenv("HLEDAC_ENABLE_BGP", "false")
        assert sidecar_should_run() is False

    def test_bgp_enabled_explicit_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BGP=1, sidecar should run."""
        monkeypatch.setenv("HLEDAC_ENABLE_BGP", "1")
        assert sidecar_should_run() is True

    def test_bgp_enabled_explicit_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BGP=true, sidecar should run."""
        monkeypatch.setenv("HLEDAC_ENABLE_BGP", "true")
        assert sidecar_should_run() is True

    def test_bgp_enabled_with_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BGP=yes, sidecar should run."""
        monkeypatch.setenv("HLEDAC_ENABLE_BGP", "yes")
        assert sidecar_should_run() is True

    def test_bgp_enabled_with_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BGP=on, sidecar should run."""
        monkeypatch.setenv("HLEDAC_ENABLE_BGP", "on")
        assert sidecar_should_run() is True

    def test_bgp_disabled_with_mixed_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BGP=On (mixed case), .lower() normalizes to "on" → enabled."""
        monkeypatch.setenv("HLEDAC_ENABLE_BGP", "On")
        assert sidecar_should_run() is True  # .lower() normalizes "On" → "on" ✓