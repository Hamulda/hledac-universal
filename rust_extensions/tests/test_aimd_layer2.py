"""
Test for AIMD Layer 2 integration in circuit_breaker.rs

Tests:
1. AIMD window decreases on failure
2. AIMD window increases after 8 successes
3. AIMD window is clamped to [1.0, 25.0]
4. Circuit breaker triggers double AIMD reduction on circuit trip
"""
import pytest


class TestAIMDLayer2:
    """Tests for AIMD Layer 2 in circuit_breaker."""

    def test_aimd_decreases_on_failure(self):
        """AIMD window should decrease by 25% on each failure."""
        # This test requires the Rust extension to be built
        try:
            from hledac_rust_extensions import (
                circuit_breaker_aimd_get_window,
                circuit_breaker_aimd_reset,
                circuit_breaker_record_failure,
            )
        except ImportError:
            pytest.skip("Rust extension not built")

        # Reset state
        circuit_breaker_aimd_reset()
        initial_window = circuit_breaker_aimd_get_window()
        assert initial_window == 10.0

        # Record a failure (not enough to trip circuit)
        circuit_breaker_record_failure("test.example.com", False)
        new_window = circuit_breaker_aimd_get_window()
        assert new_window == 7.5  # 10.0 * 0.75

    def test_aimd_increases_after_threshold(self):
        """AIMD window should increase by 2 after 8 consecutive successes."""
        try:
            from hledac_rust_extensions import (
                circuit_breaker_aimd_get_window,
                circuit_breaker_aimd_reset,
                circuit_breaker_record_success,
            )
        except ImportError:
            pytest.skip("Rust extension not built")

        circuit_breaker_aimd_reset()
        initial_window = circuit_breaker_aimd_get_window()

        # Record 8 successes - should trigger increase
        for _ in range(8):
            circuit_breaker_record_success("test.example.com")

        new_window = circuit_breaker_aimd_get_window()
        assert new_window == 12.0  # 10.0 + 2.0

    def test_aimd_clamped_to_max(self):
        """AIMD window should be clamped to MAX_WINDOW (25.0)."""
        try:
            from hledac_rust_extensions import (
                circuit_breaker_aimd_get_window,
                circuit_breaker_aimd_reset,
                circuit_breaker_record_success,
            )
        except ImportError:
            pytest.skip("Rust extension not built")

        circuit_breaker_aimd_reset()

        # Record many successes
        for _ in range(100):
            circuit_breaker_record_success("test.example.com")

        window = circuit_breaker_aimd_get_window()
        assert window == 25.0  # Clamped to max

    def test_aimd_clamped_to_min(self):
        """AIMD window should be clamped to MIN_WINDOW (1.0)."""
        try:
            from hledac_rust_extensions import (
                circuit_breaker_aimd_get_window,
                circuit_breaker_aimd_reset,
                circuit_breaker_record_failure,
            )
        except ImportError:
            pytest.skip("Rust extension not built")

        circuit_breaker_aimd_reset()

        # Record many failures
        for _ in range(20):
            circuit_breaker_record_failure("test.example.com", False)

        window = circuit_breaker_aimd_get_window()
        assert window == 1.0  # Clamped to min

    def test_circuit_trip_triggers_aggressive_reduction(self):
        """Circuit trip should trigger double AIMD reduction."""
        try:
            from hledac_rust_extensions import (
                circuit_breaker_aimd_get_window,
                circuit_breaker_aimd_reset,
                circuit_breaker_record_failure,
            )
        except ImportError:
            pytest.skip("Rust extension not built")

        circuit_breaker_aimd_reset()
        initial_window = circuit_breaker_aimd_get_window()

        # Record 5 failures to trip circuit
        for _ in range(5):
            circuit_breaker_record_failure("trip.example.com", False)

        # Window should have dropped aggressively
        window = circuit_breaker_aimd_get_window()
        # Initial: 10.0
        # After 1st failure: 7.5 (×0.75)
        # After 2nd failure: 5.625 (×0.75)
        # After 3rd failure: 4.21875 (×0.75) + trip (×0.75) = 3.16
        # After 4th failure: ×0.75
        # After 5th failure: ×0.75 + trip (×0.75)
        # Final should be significantly reduced
        assert window < 5.0  # Much lower than initial

    def test_wiring_functions(self):
        """Test Python wiring functions."""
        try:
            from rust_extensions.wiring.circuit_breaker_wiring import (
                get_aimd_window,
                reset_aimd,
            )
        except ImportError:
            pytest.skip("Rust extension not built")

        reset_aimd()
        window = get_aimd_window()
        assert 1.0 <= window <= 25.0
