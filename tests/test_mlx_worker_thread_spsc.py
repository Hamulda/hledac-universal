"""
Test SPSC queue integration in MLXWorkerThread.
P0: Validates SPSC queue initialization and stats.
"""

import pytest
from core import aclose


class TestSPSCIntegration:
    """Test SPSC queue integration with MLXWorkerThread."""

    def test_spsc_stats_available(self):
        """Verify SPSC stats appear in get_stats() after worker start."""
        from brain.mlx_worker_thread import MLXWorkerThread

        worker = MLXWorkerThread()
        worker.start()

        try:
            stats = worker.get_stats()
            # SPSC should appear in stats (Rust available or not)
            assert "spsc_available" in stats
            # If Rust backend available, has_space should be True
            if stats["spsc_available"]:
                assert isinstance(stats["spsc_available_slots"], int)
                assert stats["spsc_available_slots"] >= 0
                assert isinstance(stats["spsc_has_space"], bool)
        finally:
            worker.shutdown()

    def test_spsc_submit_returns_false_when_full(self):
        """When SPSC queue is full, sender should return False on send."""
        try:
            from core.rust_backend import rust
            if not rust.is_available:
                pytest.skip("Rust backend not available")

            pair, sender = rust.spsc.SPSCQueuePair()
            # Fill the queue to capacity (16 items)
            for _ in range(16):
                result = sender.send(b"test payload")
                assert result is True
            # Queue is now full
            result = sender.send(b"overflow")
            assert result is False
            # Explicit cleanup via GC hint
            _ = pair
            _ = sender
        except ImportError:
            pytest.skip("Rust spsc module not available")

    def test_spsc_receiver_taken_once(self):
        """take_receiver() must be called exactly once per pair."""
        try:
            from core.rust_backend import rust
            if not rust.is_available:
                pytest.skip("Rust backend not available")

            pair, sender = rust.spsc.SPSCQueuePair()
            ptr1 = pair.take_receiver()
            assert ptr1 != 0
            _ = sender  # sender is valid after take_receiver
            # Second call should panic
            with pytest.raises(Exception):
                pair.take_receiver()
        except ImportError:
            pytest.skip("Rust spsc module not available")

    def test_worker_spsc_cleanup_on_shutdown(self):
        """SPSC resources cleaned up properly on worker shutdown."""
        from brain.mlx_worker_thread import MLXWorkerThread

        worker = MLXWorkerThread()
        worker.start()
        worker.shutdown()

        # After shutdown, SPSC should be cleaned
        assert worker._spsc_sender is None
        assert worker._spsc_pair is None
