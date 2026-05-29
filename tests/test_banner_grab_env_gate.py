"""
tests/test_banner_grab_env_gate.py
==================================
Smoke tests for Banner Grab sidecar env var gating.

Verifies HLEDAC_ENABLE_BANNER_GRAB gate behavior:
- HLEDAC_ENABLE_BANNER_GRAB=0 (or unset) → sidecar disabled
- HLEDAC_ENABLE_BANNER_GRAB=1 → sidecar enabled
"""

from __future__ import annotations

import pytest


def sidecar_should_run() -> bool:
    """
    Mirrors the env gate logic from sprint_scheduler.py:_run_banner_grab_sidecar.
    Returns True if Banner Grab sidecar should run.

    Gate: matches sprint_scheduler.py logic (accepts "1", "true", "yes", "on").
    """
    import os

    banner_env = os.environ.get("HLEDAC_ENABLE_BANNER_GRAB", "").lower()
    return banner_env in ("1", "true", "yes", "on")


class TestBannerGrabEnvGate:
    """Test Banner Grab sidecar env var gating."""

    def test_banner_grab_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BANNER_GRAB is not set, sidecar should not run."""
        monkeypatch.delenv("HLEDAC_ENABLE_BANNER_GRAB", raising=False)
        assert sidecar_should_run() is False

    def test_banner_grab_disabled_explicit_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BANNER_GRAB=0, sidecar should not run."""
        monkeypatch.setenv("HLEDAC_ENABLE_BANNER_GRAB", "0")
        assert sidecar_should_run() is False

    def test_banner_grab_disabled_explicit_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BANNER_GRAB=false, sidecar should not run."""
        monkeypatch.setenv("HLEDAC_ENABLE_BANNER_GRAB", "false")
        assert sidecar_should_run() is False

    def test_banner_grab_enabled_explicit_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BANNER_GRAB=1, sidecar should run."""
        monkeypatch.setenv("HLEDAC_ENABLE_BANNER_GRAB", "1")
        assert sidecar_should_run() is True

    def test_banner_grab_enabled_explicit_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BANNER_GRAB=true, sidecar should run."""
        monkeypatch.setenv("HLEDAC_ENABLE_BANNER_GRAB", "true")
        assert sidecar_should_run() is True

    def test_banner_grab_enabled_with_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BANNER_GRAB=yes, sidecar should run."""
        monkeypatch.setenv("HLEDAC_ENABLE_BANNER_GRAB", "yes")
        assert sidecar_should_run() is True

    def test_banner_grab_enabled_with_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BANNER_GRAB=on, sidecar should run."""
        monkeypatch.setenv("HLEDAC_ENABLE_BANNER_GRAB", "on")
        assert sidecar_should_run() is True

    def test_banner_grab_enabled_with_mixed_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_BANNER_GRAB=On (mixed case), .lower() normalizes to "on" → enabled."""
        monkeypatch.setenv("HLEDAC_ENABLE_BANNER_GRAB", "On")
        assert sidecar_should_run() is True  # .lower() normalizes "On" → "on" ✓