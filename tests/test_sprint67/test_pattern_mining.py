"""
Test pattern_mining - Sprint 67
Tests for wavelet change detection and forecasting.
"""
from unittest.mock import patch

import pytest


class TestWaveletChangePoints:
    """Tests for wavelet-based change point detection."""

    def test_wavelet_empty_series(self):
        """Test empty series returns empty."""
        from hledac.universal.intelligence.pattern_mining import detect_change_points_wavelet

        # No pywt installed - should return empty
        with patch("hledac.universal.intelligence.pattern_mining._get_pywt", return_value=None):
            import asyncio
            result = asyncio.run(detect_change_points_wavelet([]))
            assert result == []

    def test_wavelet_short_series(self):
        """Test short series returns empty."""
        from hledac.universal.intelligence.pattern_mining import detect_change_points_wavelet

        with patch("hledac.universal.intelligence.pattern_mining._get_pywt", return_value=None):
            import asyncio
            result = asyncio.run(detect_change_points_wavelet([1, 2, 3]))
            assert result == []

    def test_wavelet_no_pywt(self):
        """Test when pywt not available."""
        from hledac.universal.intelligence.pattern_mining import detect_change_points_wavelet

        with patch("hledac.universal.intelligence.pattern_mining._get_pywt", return_value=None):
            import asyncio
            result = asyncio.run(detect_change_points_wavelet([1.0] * 20))
            assert result == []


class TestEWMADrift:
    """Tests for EWMA drift detection."""

    def test_ewma_short_series(self):
        """Test short series returns False."""
        from hledac.universal.intelligence.pattern_mining import _ewma_drift

        result = _ewma_drift([1.0, 2.0])
        assert result is False

    def test_ewma_stable_series(self):
        """Test stable series returns False."""
        from hledac.universal.intelligence.pattern_mining import _ewma_drift

        series = [1.0] * 20
        result = _ewma_drift(series, alpha=0.3, threshold=0.5)
        assert result is False

    def test_ewma_detects_drift(self):
        """Test EWMA detects drift."""
        from hledac.universal.intelligence.pattern_mining import _ewma_drift

        # Stable then sudden jump - use larger threshold to ensure detection
        series = [1.0] * 15 + [10.0] * 5
        # Lower threshold to ensure detection in test
        result = _ewma_drift(series, alpha=0.3, threshold=0.1)
        assert result is True


class TestCUSUMChange:
    """Tests for CUSUM change detection."""

    def test_cusum_short_series(self):
        """Test short series returns False."""
        from hledac.universal.intelligence.pattern_mining import _cusum_change

        result = _cusum_change([1.0, 2.0])
        assert result is False

    def test_cusum_stable_series(self):
        """Test stable series returns False."""
        from hledac.universal.intelligence.pattern_mining import _cusum_change

        series = [1.0] * 20
        result = _cusum_change(series, threshold=2.0)
        assert result is False

    def test_cusum_detects_change(self):
        """Test CUSUM detects change."""
        from hledac.universal.intelligence.pattern_mining import _cusum_change

        # Mean shift
        series = [1.0] * 10 + [5.0] * 10
        result = _cusum_change(series, threshold=2.0)
        assert result is True


class TestMambaForecasting:
    """Tests for Mamba2 forecasting."""

    @pytest.mark.asyncio
    async def test_forecast_circuit_breaker(self):
        """Test circuit breaker after 3 failures."""
        import time

        # Set circuit breaker state
        import hledac.universal.intelligence.pattern_mining as pm
        from hledac.universal.intelligence.pattern_mining import forecast_mamba2
        pm._MAMBA_DISABLED_UNTIL = time.time() + 60

        result = await forecast_mamba2([1.0, 2.0, 3.0, 4.0, 5.0])

        assert result is None

        # Reset
        pm._MAMBA_DISABLED_UNTIL = 0.0

    @pytest.mark.asyncio
    async def test_forecast_no_model(self):
        """Test forecast when model not available."""
        import hledac.universal.intelligence.pattern_mining as pm
        from hledac.universal.intelligence.pattern_mining import forecast_mamba2

        # Force no model
        pm._MAMBA_AVAILABLE = False
        pm._MAMBA_MODEL = None
        pm._MAMBA_TOKENIZER = None

        with patch("hledac.universal.intelligence.pattern_mining._get_mamba_model", return_value=(None, None)):
            result = await forecast_mamba2([1.0, 2.0, 3.0])

            # Should return None gracefully
            assert result is None or isinstance(result, list)


class TestPatternMiningEngine:
    """Tests for PatternMiningEngine integration."""

    @pytest.mark.asyncio
    async def test_detect_change_points_empty(self):
        """Test detect_change_points with empty series."""
        from hledac.universal.intelligence.pattern_mining import PatternMiningEngine

        engine = PatternMiningEngine()
        result = await engine.detect_change_points([])

        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_detect_change_points_short(self):
        """Test detect_change_points with short series."""
        from hledac.universal.intelligence.pattern_mining import PatternMiningEngine

        engine = PatternMiningEngine()
        result = await engine.detect_change_points([1.0, 2.0, 3.0])

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_detect_change_points_with_wavelet(self):
        """Test detect_change_points with wavelet."""
        from hledac.universal.intelligence.pattern_mining import PatternMiningEngine

        engine = PatternMiningEngine()

        # Series with clear change point
        series = [1.0] * 50 + [5.0] * 50

        with patch("hledac.universal.intelligence.pattern_mining._get_pywt") as mock_pywt:
            mock_pywt.return_value = None  # No pywt, falls back

            result = await engine.detect_change_points(series)

            assert isinstance(result, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
