"""
Sprint 8VL: Lifecycle Gate Truth — probe testy.

Testuje, že _is_windup_allowed() má správnou truth priority:
  1. injected _lifecycle_adapter → source="runtime"
  2. runtime SprintLifecycleManager direct → source="runtime"
  3. utils SprintLifecycleManager.get_instance() → source="compat"
  4. žádná dostupná → source="unavailable"
  5. force=True → source="forced", mode="forced"

a že structured state (_lifecycle_gate_source/mode) je vždy nastaveno.
"""

from unittest.mock import MagicMock, patch

import pytest

from hledac.universal.brain.synthesis_runner import SynthesisRunner


class TestLifecycleGateTruth:
    """6.1: Truth priority — adapter, runtime, compat, unavailable."""

    def test_adapter_path_sets_source_runtime(self):
        """Path 1: injected adapter → _lifecycle_gate_source='runtime'."""
        runner = SynthesisRunner.__new__(SynthesisRunner)
        runner._lifecycle_adapter = MagicMock()
        runner._lifecycle_adapter.should_enter_windup = MagicMock(return_value=True)

        result = runner._is_windup_allowed(force=False)

        assert result is True
        assert runner._lifecycle_gate_source == "runtime"
        assert runner._lifecycle_gate_mode == "windup"

    def test_adapter_path_blocked_sets_source_runtime(self):
        """Path 1: adapter not in windup → source='runtime', mode='blocked'."""
        runner = SynthesisRunner.__new__(SynthesisRunner)
        runner._lifecycle_adapter = MagicMock()
        runner._lifecycle_adapter.should_enter_windup = MagicMock(return_value=False)

        result = runner._is_windup_allowed(force=False)

        assert result is False
        assert runner._lifecycle_gate_source == "runtime"
        assert runner._lifecycle_gate_mode == "blocked"

    def test_force_flag_sets_source_forced(self):
        """force=True → source='forced', mode='forced', returns True."""
        runner = SynthesisRunner.__new__(SynthesisRunner)
        runner._lifecycle_adapter = None  # shouldn't matter

        result = runner._is_windup_allowed(force=True)

        assert result is True
        assert runner._lifecycle_gate_source == "forced"
        assert runner._lifecycle_gate_mode == "forced"

    def test_utils_sprint_lifecycle_has_get_instance(self):
        """Compat utils SprintLifecycleManager has get_instance() singleton."""
        from hledac.universal.utils.sprint_lifecycle import SprintLifecycleManager
        assert hasattr(SprintLifecycleManager, "get_instance")

    def test_runtime_sprint_lifecycle_is_dataclass_no_singleton(self):
        """Runtime SprintLifecycleManager is a dataclass, no get_instance()."""
        from hledac.universal.runtime.sprint_lifecycle import SprintLifecycleManager
        import dataclasses
        assert dataclasses.is_dataclass(SprintLifecycleManager)
        assert not hasattr(SprintLifecycleManager, "get_instance")


class TestStructuredStateAlwaysSet:
    """6.2: Structured state is ALWAYS set before return."""

    def test_force_always_sets_structured_state(self):
        """force=True vždy nastaví _lifecycle_gate_source a _lifecycle_gate_mode."""
        runner = SynthesisRunner.__new__(SynthesisRunner)
        runner._lifecycle_gate_source = "unknown"
        runner._lifecycle_gate_mode = "unknown"

        runner._is_windup_allowed(force=True)

        assert runner._lifecycle_gate_source == "forced"
        assert runner._lifecycle_gate_mode == "forced"

    def test_adapter_always_sets_structured_state(self):
        """Adapter path vždy nastaví structured state, i když exception."""
        runner = SynthesisRunner.__new__(SynthesisRunner)
        runner._lifecycle_adapter = MagicMock()
        runner._lifecycle_adapter.should_enter_windup = MagicMock(side_effect=RuntimeError("boom"))

        runner._is_windup_allowed(force=False)

        # Should fall through to compat/utils, but structured state is set
        assert runner._lifecycle_gate_source in ("runtime", "compat", "unavailable")
        assert runner._lifecycle_gate_mode in ("windup", "blocked")


class TestInjectLifecycleAdapter:
    """6.3: inject_lifecycle_adapter sets _lifecycle_adapter."""

    def test_inject_lifecycle_adapter_sets_field(self):
        """inject_lifecycle_adapter() nastaví _lifecycle_adapter."""
        runner = SynthesisRunner.__new__(SynthesisRunner)
        mock_adapter = MagicMock()

        runner.inject_lifecycle_adapter(mock_adapter)

        assert runner._lifecycle_adapter is mock_adapter

    def test_inject_lifecycle_adapter_overrides_previous(self):
        """inject_lifecycle_adapter přepíše předchozí hodnotu."""
        runner = SynthesisRunner.__new__(SynthesisRunner)
        runner._lifecycle_adapter = MagicMock()

        new_adapter = MagicMock()
        runner.inject_lifecycle_adapter(new_adapter)

        assert runner._lifecycle_adapter is new_adapter


class TestCompatFallbackExplicit:
    """6.4: Compat fallback je explicitně označený."""

    def test_compat_utils_manager_is_windup_phase(self):
        """Compat utils SprintLifecycleManager.is_windup_phase() exists."""
        from hledac.universal.utils.sprint_lifecycle import SprintLifecycleManager
        manager = SprintLifecycleManager()
        assert hasattr(manager, "is_windup_phase")
        # Returns a bool (not None)
        result = manager.is_windup_phase()
        assert isinstance(result, bool)
