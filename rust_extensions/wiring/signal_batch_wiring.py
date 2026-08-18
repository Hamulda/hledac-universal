"""
Signal Batch Rust Integration Wiring
====================================

Wires rust_extensions/src/signal_batch.rs to feed pipeline.

Purpose:
- NEON-accelerated source quality score computation
- Batch signal aggregation with weighted averaging
- Batch page quality scoring via rayon parallelization

Integration Point:
- pipeline/feed/_scan_stage.py feed quality scoring
- Replaces pure Python signal aggregation loops

Usage:
    from rust_extensions.wiring.signal_batch_wiring import signal_batch

    # In ScanStage.process():
    scores = await asyncio.to_thread(signal_batch.batch_compute_scores, items, weights)
    aggregated = signal_batch.batch_aggregate_signals(signals, weights)
    qualities = signal_batch.batch_quality_score(text_lens, texts, errors, stages)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Import the integration layer
from rust_extensions.integrations import get_signal_batch

# Create singleton instance
_signal_batch = get_signal_batch()


def signal_batch_wired():
    """Get the wired signal batch integration."""
    return _signal_batch


def batch_compute_scores(
    stats: list[dict[str, Any]],
    default_weight: float = 1.0,
) -> list[float]:
    """
    Compute batch source quality scores using Rust NEON SIMD.

    Falls back to pure Python implementation on error.

    Args:
        stats: List of dicts with keys:
            - fetched (u32): items fetched from source
            - accepted (u32): items accepted from source
            - current_weight (f32): current source weight (default 1.0)
            - novelty (bool): source added new IOC types (default False)
        default_weight: Weight when current_weight key is absent

    Returns:
        List of computed weights (f32), clamped to [0.3, 2.5] per F199A.
    """
    return _signal_batch.batch_compute_scores(stats, default_weight)


def batch_aggregate_signals(
    signals: list[list[float]],
    weights: list[float],
    normalize: bool = True,
) -> list[float]:
    """
    Aggregate signal vectors using per-source weights.

    Args:
        signals: List of signal vectors (list of floats).
        weights: Per-source weights (list of floats).
        normalize: If True, return weighted average. If False, return weighted sum.

    Returns:
        Aggregated signal vector (list of floats).
    """
    return _signal_batch.batch_aggregate_signals(signals, weights, normalize)


def batch_quality_score(
    text_lens: list[int],
    texts: list[str],
    fetch_errors: list[str | None],
    failure_stages: list[str | None],
) -> list[tuple[float, str, str, str, bool, str | None]]:
    """
    Compute page quality scores for a batch using rayon parallelization.

    Args:
        text_lens: List of page text lengths.
        texts: List of page text strings.
        fetch_errors: List of fetch error strings (None = success).
        failure_stages: List of failure stage strings (None = success).

    Returns:
        List of (quality_signal, value_tier, waste_category, structural_quality,
                 is_fp, skip_reason) tuples per page.
    """
    return _signal_batch.batch_quality_score(
        text_lens, texts, fetch_errors, failure_stages
    )


# Check availability at import time for logging
if _signal_batch.available:
    logger.info("[SignalBatch] Rust signal_batch.rs integration: ENABLED")
else:
    logger.info("[SignalBatch] Rust signal_batch.rs integration: DISABLED (using Python fallback)")
