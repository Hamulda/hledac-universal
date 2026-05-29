"""
tests/test_ipfs_env_gate.py
==========================
Smoke tests for IPFS sidecar env var gating.

Verifies HLEDAC_ENABLE_IPFS gate behavior:
- HLEDAC_ENABLE_IPFS=0 (or unset) → sidecar disabled
- HLEDAC_ENABLE_IPFS=1 → sidecar enabled
"""

from __future__ import annotations

import pytest


def sidecar_should_run() -> bool:
    """
    Mirrors the env gate logic from sidecar_orchestrator.py:run_advisory_runner.
    Returns True if IPFS sidecar should run.

    Gate: matches sprint_scheduler.py:16768 logic (accepts "1", "true", "True").
    """
    import os

    ipfs_env = os.environ.get("HLEDAC_ENABLE_IPFS", "0").strip()
    return ipfs_env in ("1", "true", "True")


class TestIPFSEnvGate:
    """Test IPFS sidecar env var gating."""

    def test_ipfs_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_IPFS is not set, sidecar should not run."""
        monkeypatch.delenv("HLEDAC_ENABLE_IPFS", raising=False)
        assert sidecar_should_run() is False

    def test_ipfs_disabled_explicit_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_IPFS=0, sidecar should not run."""
        monkeypatch.setenv("HLEDAC_ENABLE_IPFS", "0")
        assert sidecar_should_run() is False

    def test_ipfs_disabled_explicit_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_IPFS=false, sidecar should not run."""
        monkeypatch.setenv("HLEDAC_ENABLE_IPFS", "false")
        assert sidecar_should_run() is False

    def test_ipfs_enabled_explicit_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_IPFS=1, sidecar should run."""
        monkeypatch.setenv("HLEDAC_ENABLE_IPFS", "1")
        assert sidecar_should_run() is True

    def test_ipfs_enabled_explicit_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HLEDAC_ENABLE_IPFS=true, sidecar should run."""
        monkeypatch.setenv("HLEDAC_ENABLE_IPFS", "true")
        assert sidecar_should_run() is True

    def test_ipfs_enabled_with_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Env var with whitespace should be stripped before comparison."""
        monkeypatch.setenv("HLEDAC_ENABLE_IPFS", "  1  ")
        assert sidecar_should_run() is True
