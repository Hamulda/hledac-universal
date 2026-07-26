# signal_batch.py — NEON-accelerated signal aggregation domain
"""
Rust-backed signal batch processing — ARM NEON SIMD for M1,
scalar fallback on other platforms.

batch_compute_scores: Source quality scoring via F199A formula.
  Input: list of (fetched_count, accepted_count, novelty_flag)
  Output: list of float weights clamped [0.3, 2.5]

batch_aggregate_signals: Weighted signal vector aggregation.
  Input: list of signal vectors (list of floats) + per-source weights
  Output: aggregated vector (weighted average or weighted sum)

M1 8GB: GIL released via _py.allow_threads(), no Metal contention.
"""

from __future__ import annotations

from typing import Any


def get_domain() -> "SignalBatchDomain":
    from hledac.universal.rust_extensions import hledac_rust_extensions as _ext

    _probe = getattr(_ext, "batch_compute_scores", None)
    if _probe is None:
        msg = "hledac_rust_extensions.batch_compute_scores not available"
        raise ImportError(msg)
    return SignalBatchDomain(_ext)


class SignalBatchDomain:
    """NEON-accelerated signal processing (M1) or scalar fallback."""

    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def batch_compute_scores(
        self,
        stats: list[tuple[int, int, bool]],
        default_weight: float = 1.0,
    ) -> list[float]:
        """Compute per-source quality scores via F199A formula (NEON-accelerated).

        Args:
            stats: List of (fetched_count, accepted_count, novelty_flag).
                   novelty_flag=True gives 1.5× novelty bonus.
            default_weight: Starting weight for new sources (default 1.0).

        Returns:
            List of float weights clamped to [0.3, 2.5].
        """
        # Rust expects (u32, u32, bool) per source
        return self._ext.batch_compute_scores(stats, default_weight)

    def batch_aggregate_signals(
        self,
        signals: list[list[float]],
        weights: list[float],
        *,
        normalize: bool = True,
    ) -> list[float]:
        """Aggregate signal vectors using per-source weights (NEON-accelerated).

        Args:
            signals: List of signal vectors (each list of floats).
            weights: Per-source weights (list of floats, same length as signals).
            normalize: If True, return weighted average.
                      If False, return weighted sum.

        Returns:
            Aggregated signal vector, or empty list on error.
        """
        return self._ext.batch_aggregate_signals(signals, weights, normalize)


# ---------------------------------------------------------------------------
# Python fallback — pure scalar implementation
# ---------------------------------------------------------------------------


class PythonFallbackSignalDomain:
    """Scalar Python fallback for non-M1 or if Rust unavailable."""

    __slots__ = ()

    def batch_compute_scores(
        self,
        stats: list[tuple[int, int, bool]],
        default_weight: float = 1.0,
    ) -> list[float]:
        results: list[float] = []
        for fetched, accepted, novelty in stats:
            ratio = accepted / max(fetched, 1)
            if ratio >= 0.7:
                delta = 1.10
            elif ratio >= 0.4:
                delta = 1.05
            elif ratio >= 0.15:
                delta = 1.00
            else:
                delta = 0.95
            novelty_bonus = 1.5 if novelty else 1.0
            new_weight = default_weight * delta * novelty_bonus
            results.append(max(0.3, min(2.5, new_weight)))
            default_weight = new_weight  # type: ignore[unused-variable]
        return results

    def batch_aggregate_signals(
        self,
        signals: list[list[float]],
        weights: list[float],
        *,
        normalize: bool = True,
    ) -> list[float]:
        if not signals or not weights:
            return []
        n = min(len(signals), len(weights))
        if n == 0:
            return []
        m = len(signals[0])
        result = [0.0] * m
        weight_sum = sum(weights[:n])
        for s in range(n):
            for d in range(m):
                result[d] += signals[s][d] * weights[s]
        if normalize and weight_sum > 0:
            result = [x / weight_sum for x in result]
        return result
