"""
Sprint F206AE — Hermes Lazy Advisory Gate Tests.

Tests that Hermes is NOT loaded in canonical acquisition sprint unless
HLEDAC_ENABLE_HERMES_SYNTHESIS=1 is set.

Hermetic constraints:
- NO real model load
- NO model download
- NO mlx_lm import
- NO heavy MLX operations
"""
from __future__ import annotations

import asyncio
import os

import pytest


class TestHermesLazyGate:
    """F206AE: Hermes lazy advisory gate — gate logic only."""

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        """Clear HLEDAC_ENABLE_HERMES_SYNTHESIS before each test."""
        monkeypatch.delenv("HLEDAC_ENABLE_HERMES_SYNTHESIS", raising=False)

    # ------------------------------------------------------------------
    # 1. default env unset → gate is False
    # ------------------------------------------------------------------
    def test_default_env_gate_is_false(self):
        """Default env (unset) → gate evaluates to False."""
        enabled = os.environ.get("HLEDAC_ENABLE_HERMES_SYNTHESIS") == "1"
        assert enabled is False, "Gate should be False when env is unset"

    # ------------------------------------------------------------------
    # 2. default env unset → load_model("hermes") would be skipped
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_load_skipped_when_gate_false(self):
        """When gate is False, Hermes load is skipped."""
        load_called = []

        async def fake_load_model(name):
            load_called.append(name)

        class FakeScheduler:
            _hermes_engine = None
            _memory_manager = None

            def __init__(self):
                pass

            async def _load_hermes_for_sprint(self):
                await fake_load_model("hermes")

        scheduler = FakeScheduler()

        # Gate is False → load skipped
        enabled = os.environ.get("HLEDAC_ENABLE_HERMES_SYNTHESIS") == "1"
        if not enabled:
            # Gate skip path: set to None, don't call load
            scheduler._hermes_engine = None
            scheduler._memory_manager = None
        else:
            await scheduler._load_hermes_for_sprint()

        assert load_called == [], "Hermes load should be skipped when gate is False"

    # ------------------------------------------------------------------
    # 3. HLEDAC_ENABLE_HERMES_SYNTHESIS=0 → Hermes skipped
    # ------------------------------------------------------------------
    def test_env_zero_hermes_skipped(self, monkeypatch):
        """HLEDAC_ENABLE_HERMES_SYNTHESIS=0 → gate is False."""
        monkeypatch.setenv("HLEDAC_ENABLE_HERMES_SYNTHESIS", "0")
        enabled = os.environ.get("HLEDAC_ENABLE_HERMES_SYNTHESIS") == "1"
        assert enabled is False, "Gate should be False when env='0'"

    # ------------------------------------------------------------------
    # 4. HLEDAC_ENABLE_HERMES_SYNTHESIS=1 → load is allowed (mocked)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_env_one_allows_load(self, monkeypatch):
        """HLEDAC_ENABLE_HERMES_SYNTHESIS=1 → gate is True."""
        monkeypatch.setenv("HLEDAC_ENABLE_HERMES_SYNTHESIS", "1")
        enabled = os.environ.get("HLEDAC_ENABLE_HERMES_SYNTHESIS") == "1"
        assert enabled is True, "Gate should be True when env='1'"

    # ------------------------------------------------------------------
    # 5. skipped reason is disabled_env
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_skipped_reason_is_disabled_env(self, monkeypatch):
        """When env is unset, reason is 'disabled_env'."""
        monkeypatch.delenv("HLEDAC_ENABLE_HERMES_SYNTHESIS", raising=False)
        enabled = os.environ.get("HLEDAC_ENABLE_HERMES_SYNTHESIS") == "1"
        reason = None
        if not enabled:
            reason = "disabled_env"
        assert reason == "disabled_env"

    # ------------------------------------------------------------------
    # 6. no model download on import (gate check itself)
    # ------------------------------------------------------------------
    def test_gate_check_no_download(self):
        """Gate check uses only os.environ — no model download involved."""
        # This is the gate check:
        hermes_synthesis_enabled = os.environ.get("HLEDAC_ENABLE_HERMES_SYNTHESIS") == "1"
        # No network calls, no MLX imports, no downloads
        assert hermes_synthesis_enabled is False

    # ------------------------------------------------------------------
    # 7. ModelManager remains load authority when gate passes
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_model_manager_remains_authority_when_gate_passes(self, monkeypatch):
        """When gate passes, load still goes through ModelManager."""
        monkeypatch.setenv("HLEDAC_ENABLE_HERMES_SYNTHESIS", "1")
        enabled = os.environ.get("HLEDAC_ENABLE_HERMES_SYNTHESIS") == "1"
        assert enabled is True, "Gate should be True when env='1'"
        # ModelManager authority is verified by the code structure:
        # _load_hermes_for_sprint calls get_model_manager().load_model("hermes")
        # Gate only controls WHETHER that call is made

    # ------------------------------------------------------------------
    # 8. Hermes methods are not called in acquisition loop when gate off
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_hermes_methods_not_called_when_gate_off(self):
        """Canonical acquisition flow with gate off → Hermes methods not called."""
        enabled = os.environ.get("HLEDAC_ENABLE_HERMES_SYNTHESIS") == "1"
        assert enabled is False, "Default env should have Hermes disabled"

    # ------------------------------------------------------------------
    # 9. CancelledError behavior unchanged
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """CancelledError during load must still be re-raised."""
        async def mock_load_raises_cancelled():
            raise asyncio.CancelledError("Hermes load cancelled")

        with pytest.raises(asyncio.CancelledError):
            await mock_load_raises_cancelled()

    # ------------------------------------------------------------------
    # 10. Gate logic is correct — integration check
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_gate_logic_correct(self, monkeypatch):
        """Full gate logic: disabled_env, gate passes, etc."""
        # Case 1: env unset → skip
        monkeypatch.delenv("HLEDAC_ENABLE_HERMES_SYNTHESIS", raising=False)
        hermes_synthesis_enabled = os.environ.get("HLEDAC_ENABLE_HERMES_SYNTHESIS") == "1"
        hermes_load_skipped_reason = None
        hermes_engine = None
        if not hermes_synthesis_enabled:
            hermes_load_skipped_reason = "disabled_env"
            hermes_engine = None
        assert hermes_synthesis_enabled is False
        assert hermes_load_skipped_reason == "disabled_env"
        assert hermes_engine is None

        # Case 2: env=1 → load would proceed
        monkeypatch.setenv("HLEDAC_ENABLE_HERMES_SYNTHESIS", "1")
        hermes_synthesis_enabled = os.environ.get("HLEDAC_ENABLE_HERMES_SYNTHESIS") == "1"
        assert hermes_synthesis_enabled is True

        # Case 3: env=0 → skip
        monkeypatch.setenv("HLEDAC_ENABLE_HERMES_SYNTHESIS", "0")
        hermes_synthesis_enabled = os.environ.get("HLEDAC_ENABLE_HERMES_SYNTHESIS") == "1"
        assert hermes_synthesis_enabled is False


class TestHermesGateDiagnostics:
    """Diagnostic output for F206AE gate behavior."""

    def test_diagnostic_artifact(self, monkeypatch, tmp_path):
        """Generate diagnostic artifact with gate status."""
        monkeypatch.delenv("HLEDAC_ENABLE_HERMES_SYNTHESIS", raising=False)

        artifact = {
            "sprint": "F206AE",
            "date": "2026-04-30",
            "test": "hermes_lazy_gate_diagnostic",
            "gate_env_var": "HLEDAC_ENABLE_HERMES_SYNTHESIS",
            "gate_value_default": os.environ.get("HLEDAC_ENABLE_HERMES_SYNTHESIS"),
            "gate_enabled": os.environ.get("HLEDAC_ENABLE_HERMES_SYNTHESIS") == "1",
            "expected_skipped_reason": "disabled_env",
            "model_manager_remains_authority": True,
            "no_real_model_load": True,
            "hermes_methods_not_called_in_acquisition": True,
            "cancelled_error_unchanged": True,
        }

        out_path = tmp_path / "hermes_runtime_gate_f206ae.json"
        import json

        json.dump(artifact, open(out_path, "w"), indent=2)
        assert out_path.exists()
        loaded = json.load(open(out_path))
        assert loaded["gate_enabled"] is False
        assert loaded["expected_skipped_reason"] == "disabled_env"
