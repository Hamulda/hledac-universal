"""
Sprint 8QC D.5: SynthesisRunner EMERGENCY skip.
100% offline — mocks all MLX/Outlines calls.
"""

from unittest.mock import MagicMock, patch

from hledac.universal.brain.synthesis_runner import SynthesisRunner


class TestEmergencySkip:
    """D.5: evaluate_uma_state() == 'emergency' → skip synthesis."""

    def test_emergency_guard_returns_false_on_emergency(self):
        """_check_uma_guard returns False when evaluate_uma_state returns 'emergency'."""
        runner = SynthesisRunner(MagicMock())  # type: ignore[arg-type]

        with patch("hledac.universal.core.resource_governor.evaluate_uma_state", return_value="emergency"):
            with patch("hledac.universal.core.resource_governor.sample_uma_status") as mock_sample:
                mock_sample.return_value = MagicMock(system_used_gib=8.0)
                result = runner._check_uma_guard()

        assert result is False  # Guard blocked synthesis

    def test_emergency_guard_returns_true_when_ok(self):
        """_check_uma_guard returns True when evaluate_uma_state returns 'ok'."""
        runner = SynthesisRunner(MagicMock())  # type: ignore[arg-type]

        with patch("hledac.universal.core.resource_governor.evaluate_uma_state", return_value="ok"):
            with patch("hledac.universal.core.resource_governor.sample_uma_status") as mock_sample:
                mock_sample.return_value = MagicMock(system_used_gib=2.0)
                result = runner._check_uma_guard()

        assert result is True  # Guard allows synthesis
