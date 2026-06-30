"""
Sprint 8QC D.6+D.7: Windup guard + force override.
Tests guard logic directly with patched global state.

Sprint 8VL UPDATE: _is_windup_allowed now uses 3-path truth priority.
Old tests updated to work with new structured state.
"""

from unittest.mock import patch, MagicMock

from hledac.universal.brain.synthesis_runner import SynthesisRunner


class TestWindupGuard:
    """D.6: is_windup_phase()=False, force_synthesis=False → skip."""

    def test_windup_guard_returns_false_when_not_windup(self):
        """_is_windup_allowed returns False when not in windup and force=False."""
        runner = SynthesisRunner.__new__(SynthesisRunner)
        # Sprint 8VL: _lifecycle_adapter must be set (Path 1) or falls to Path 3
        runner._lifecycle_adapter = None  # Force Path 3 (utils compat)

        # Patch get_instance to return a manager where is_windup_phase=False
        mock_instance = MagicMock()
        mock_instance.is_windup_phase = MagicMock(return_value=False)

        with patch("hledac.universal.utils.sprint_lifecycle.SprintLifecycleManager.get_instance", return_value=mock_instance):
            result = runner._is_windup_allowed(force=False)

        assert result is False

    def test_windup_guard_returns_true_when_in_windup(self):
        """_is_windup_allowed returns True when in windup phase."""
        runner = SynthesisRunner.__new__(SynthesisRunner)
        runner._lifecycle_adapter = None  # Force Path 3 (utils compat)

        mock_instance = MagicMock()
        mock_instance.is_windup_phase = MagicMock(return_value=True)

        with patch("hledac.universal.utils.sprint_lifecycle.SprintLifecycleManager.get_instance", return_value=mock_instance):
            result = runner._is_windup_allowed(force=False)

        assert result is True


class TestForceOverride:
    """D.7: force_synthesis=True bypasses windup check."""

    def test_force_true_bypasses_windup_check(self):
        """force=True makes _is_windup_allowed return True even when manager unavailable."""
        runner = SynthesisRunner.__new__(SynthesisRunner)
        # _lifecycle_adapter not set — force path doesn't need it
        runner._lifecycle_adapter = None

        result = runner._is_windup_allowed(force=True)

        assert result is True  # Force overrides everything
        assert runner._lifecycle_gate_source == "forced"
        assert runner._lifecycle_gate_mode == "forced"

    def test_force_false_without_windup_returns_false(self):
        """force=False + not in windup = blocked."""
        runner = SynthesisRunner.__new__(SynthesisRunner)
        runner._lifecycle_adapter = None  # Force Path 3

        with patch("hledac.universal.utils.sprint_lifecycle.SprintLifecycleManager.get_instance", side_effect=Exception("no manager")):
            result = runner._is_windup_allowed(force=False)

        assert result is False  # Blocked when manager unavailable and no force
        assert runner._lifecycle_gate_source == "unavailable"
