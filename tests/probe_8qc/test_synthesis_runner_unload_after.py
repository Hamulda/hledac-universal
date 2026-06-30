"""
Sprint 8QC D.9: SynthesisRunner unload verification.
Tests unload guard behavior directly.
"""

from unittest.mock import MagicMock, patch
import asyncio

from hledac.universal.brain.synthesis_runner import SynthesisRunner


class TestUnloadGuard:
    """D.9: Unload must be called after synthesis."""

    def test_unload_guard_blocks_on_emergency(self):
        """If _check_uma_guard returns False, synthesis is skipped."""
        runner = SynthesisRunner.__new__(SynthesisRunner)

        with patch("hledac.universal.core.resource_governor.evaluate_uma_state", return_value="emergency"):
            with patch("hledac.universal.core.resource_governor.sample_uma_status") as mock_s:
                mock_s.return_value = MagicMock(system_used_gib=8.0)
                result = runner._check_uma_guard()

        assert result is False  # Blocked by EMERGENCY

    def test_unload_guard_allows_on_ok(self):
        """If _check_uma_guard returns True, synthesis proceeds."""
        runner = SynthesisRunner.__new__(SynthesisRunner)

        with patch("hledac.universal.core.resource_governor.evaluate_uma_state", return_value="ok"):
            with patch("hledac.universal.core.resource_governor.sample_uma_status") as mock_s:
                mock_s.return_value = MagicMock(system_used_gib=2.0)
                result = runner._check_uma_guard()

        assert result is True  # Allowed

    def test_close_calls_unload(self):
        """close() must call lifecycle.unload()."""
        mock_lc = MagicMock()
        mock_close_result = asyncio.Future()
        mock_close_result.set_result(None)
        mock_lc.unload = MagicMock(return_value=mock_close_result)

        runner = SynthesisRunner.__new__(SynthesisRunner)
        runner._lifecycle = mock_lc
        runner._ioc_graph = None

        asyncio.run(runner.close())

        assert mock_lc.unload.called or mock_lc.unload.call_count >= 1
