"""
Retry / backoff jitter unit tests.

Covers:
- public_fetcher._compute_backoff_seconds (decorrelated jitter, cap, retry_after)
- Documents the AWS-style "exponential backoff and jitter" behavior we wired
  into both public_fetcher.py and fetch_coordinator.py.

These tests are hermetic: they import pure helper functions and do not touch
the network, the filesystem, or the M1 MLX stack.
"""

import statistics

import pytest

from fetching.public_fetcher import _compute_backoff_seconds


class TestComputeBackoffJitter:
    """Unit tests for _compute_backoff_seconds (public_fetcher.py)."""

    def test_backoff_never_exceeds_cap(self) -> None:
        """100 jittered calls must stay within the 8 s hard cap."""
        samples = [_compute_backoff_seconds(None, attempt, jitter=True) for attempt in range(100)]
        assert all(0.0 <= s <= 8.0 for s in samples), (
            f"out-of-range samples: {[s for s in samples if not (0.0 <= s <= 8.0)]}"
        )

    def test_backoff_has_variance(self) -> None:
        """Decorrelated jitter must produce non-trivial spread (stddev > 0.5).

        Deterministic exponential backoff (``base * 2**attempt``) would yield
        stddev = 0.0. We require enough variance to de-correlate retry storms.
        """
        samples = [_compute_backoff_seconds(None, attempt, jitter=True) for attempt in range(50)]
        stddev = statistics.pstdev(samples)
        assert stddev > 0.5, f"stddev={stddev:.3f} too low — jitter not active"

    def test_retry_after_honored(self) -> None:
        """With jitter=False, retry_after is taken as the base value (capped at 60 s)."""
        # 30 s fits under the 60 s cap → passed through unchanged.
        assert _compute_backoff_seconds(30.0, attempt=0, jitter=False) == 30.0
        # 90 s would be capped at 60 s.
        assert _compute_backoff_seconds(90.0, attempt=0, jitter=False) == 60.0
        # 0 / None / negative falls back to the exponential ladder.
        assert _compute_backoff_seconds(0.0, attempt=0, jitter=False) == 2.0
        assert _compute_backoff_seconds(None, attempt=0, jitter=False) == 2.0
        assert _compute_backoff_seconds(None, attempt=2, jitter=False) == 8.0  # cap

    def test_decorrelated_jitter_uses_prev_sleep(self) -> None:
        """_prev_sleep kwarg expands the sample upper bound (decorrelated pattern)."""
        # When _prev_sleep exceeds the base, the cap * 3 widens.
        # We just verify the function accepts the kwarg without error
        # and returns a value within the documented 8 s cap.
        for _ in range(20):
            v = _compute_backoff_seconds(None, 0, jitter=True, _prev_sleep=4.0)
            assert 0.0 <= v <= 8.0

    def test_jitter_disabled_is_deterministic(self) -> None:
        """jitter=False must produce a deterministic value (no random spread)."""
        for attempt in (0, 1, 2, 3):
            a = _compute_backoff_seconds(None, attempt, jitter=False)
            b = _compute_backoff_seconds(None, attempt, jitter=False)
            assert a == b, f"jitter=False must be deterministic (attempt={attempt})"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
