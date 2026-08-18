"""Scan stage — pattern scan on assembled feed text.

Responsibilities:
- Apply Rust Aho-Corasick pattern matching to assembled text
- Use Rust feed_pipeline if available
- Fall back to Python PatternMatcher
- Batch signal aggregation using Rust signal_batch (NEON-accelerated)

Input: FeedAssembledBatch
Output: FeedMatchedBatch (entry_urls, matched_pattern_counts, matched_pattern_labels, entry_dedup_hits, errors)
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from hledac.universal.pipeline._soa_types import FeedAssembledBatch, FeedMatchedBatch
from hledac.universal.utils.asyncx import parallel_ok
from _core import aclose

logger = logging.getLogger(__name__)

# Signal batch wiring - lazy import to avoid circular dependencies
_signal_batch = None

# M1 8GB Safety: Batch size limits to prevent OOM
_MAX_BATCH_SIZE = 10000  # Maximum items per batch
_MAX_TEXT_LEN = 50000    # Maximum text length per item


def _get_signal_batch():
    """Lazy load signal batch integration."""
    global _signal_batch
    if _signal_batch is None:
        try:
            from rust_extensions.wiring.signal_batch_wiring import signal_batch_wired
            _signal_batch = signal_batch_wired()
        except Exception:
            _signal_batch = None
    return _signal_batch


class ScanStage:
    """Scan stage: FeedAssembledBatch → FeedMatchedBatch.

    Applies pattern matching to assembled feed texts.
    Uses Rust feed_pipeline if available.
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        return "scan"

    async def process(
        self, input_batch: FeedAssembledBatch | None
    ) -> tuple[FeedMatchedBatch, dict[str, Any]]:
        """Scan assembled texts for patterns.

        Args:
            input_batch: FeedAssembledBatch with assembled texts

        Returns:
            Tuple of (FeedMatchedBatch, telemetry)

        """
        if input_batch is None or not input_batch.entry_urls:
            return self._empty_batch(), {}

        telemetry: dict[str, Any] = {
            "entries_scanned": len(input_batch.entry_urls),
            "entries_with_matches": 0,
            "total_matches": 0,
            "entries_no_match": 0,
            "scan_errors": 0,
        }

        # Try Rust feed_pipeline first
        rust_domain = _get_rust_feed_domain()

        if rust_domain is not None:
            results = await _rust_scan_batch(input_batch, rust_domain)
        else:
            results = await _python_scan_batch(input_batch)

        # Build FeedMatchedBatch
        matched_pattern_counts = [r["count"] for r in results]
        matched_pattern_labels = [r["labels"] for r in results]
        errors = [r.get("error") for r in results]

        for r in results:
            if r["count"] > 0:
                telemetry["entries_with_matches"] += 1
                telemetry["total_matches"] += r["count"]
            else:
                telemetry["entries_no_match"] += 1
            if r.get("error"):
                telemetry["scan_errors"] += 1

        batch = FeedMatchedBatch(
            entry_urls=input_batch.entry_urls,
            matched_pattern_counts=matched_pattern_counts,
            matched_pattern_labels=matched_pattern_labels,
            entry_dedup_hits=[False] * len(input_batch.entry_urls),  # filled by dedup stage
            errors=errors,
    )

        return batch, telemetry

    def _empty_batch(self) -> FeedMatchedBatch:
        return FeedMatchedBatch(
            entry_urls=[],
            matched_pattern_counts=[],
            matched_pattern_labels=[],
            entry_dedup_hits=[],
            errors=[],
    )


def _get_rust_feed_domain() -> Any | None:
    """Get Rust feed_pipeline domain if available."""
    try:
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal._core.rust_backend import rust
        _ext = rust.raw.module

        probe = getattr(_ext, "feed_entry_pipeline", None)
        if probe is None:
            return None
        return _ext
    except Exception:
        return None


async def _rust_scan_batch(
    batch: FeedAssembledBatch,
    rust_domain: Any,
) -> list[dict[str, Any]]:
    """Scan using Rust feed_pipeline."""
    results = []

    async def scan_one(idx: int, text: str, entry_url: str) -> dict[str, Any]:
        try:
            result = rust_domain.feed_entry_pipeline(
                text,
                entry_url,
                idx,
    )
            # Result is (entry_idx, entry_url, combined_hits, 0, 0, assembly_phase)
            if result and len(result) >= 3:
                combined_hits = result[2]
                labels = _parse_combined_hits(combined_hits)
                return {"count": len(labels), "labels": labels, "error": None}
            return {"count": 0, "labels": [], "error": None}
        except Exception as exc:
            logger.warning(f"Rust scan failed for entry[{idx}]: {exc}")
            return {"count": 0, "labels": [], "error": str(exc)}

    tasks = [
        scan_one(i, batch.assembled_texts[i] if i < len(batch.assembled_texts) else "", batch.entry_urls[i] if i < len(batch.entry_urls) else "")
        for i in range(len(batch.entry_urls))
    ]
    # F3XX: parallel_ok() replaces asyncio.gather — returns list[T] in original order.
    scanned = await parallel_ok(*tasks, label="scan_stage")

    for r in scanned:
        results.append(r)

    return results


async def _python_scan_batch(
    batch: FeedAssembledBatch,
) -> list[dict[str, Any]]:
    """Scan using Python PatternMatcher fallback."""
    results = []

    for i in range(len(batch.entry_urls)):
        text = batch.assembled_texts[i] if i < len(batch.assembled_texts) else ""
        try:
            matches = _python_match_text(text)
            labels = [m.get("label", "unknown") for m in matches] if matches else []
            results.append({"count": len(labels), "labels": labels, "error": None})
        except Exception as exc:
            results.append({"count": 0, "labels": [], "error": str(exc)})

    return results


def _python_match_text(text: str) -> list[dict[str, Any]]:
    """Python PatternMatcher fallback."""
    # Import here to avoid circular dependency
    try:
        from hledac.universal.pipeline.live_feed_pipeline import _SYNC_MATCH_TEXT, _ensure_feed_matcher_patched

        _ensure_feed_matcher_patched()

        if _SYNC_MATCH_TEXT is None:
            return []
        return _SYNC_MATCH_TEXT(text)
    except Exception:
        return []


def _parse_combined_hits(combined_hits: int) -> list[str]:
    """Parse combined_hits integer to pattern labels."""
    # combined_hits is a bitmask from Rust Aho-Corasick
    # For now, return empty list (labels extracted from Rust in future)
    return []


# ============================================================================
# Signal Batch Processing (NEON-accelerated)
# ============================================================================


async def batch_compute_signals(
    items: list[dict[str, Any]],
    default_weight: float = 1.0,
) -> list[float]:
    """
    Compute batch source quality scores using Rust NEON SIMD.

    This function wraps the Rust batch_compute_scores with asyncio.to_thread
    for non-blocking execution, with fallback to pure Python implementation.

    M1 8GB Safety:
    - Batches are limited to _MAX_BATCH_SIZE items
    - Large batches are split and processed in chunks

    Args:
        items: List of source stats dicts with keys:
            - fetched (int): items fetched from source
            - accepted (int): items accepted from source
            - current_weight (float): current source weight
            - novelty (bool): source added new IOC types
        default_weight: Weight when current_weight key is absent

    Returns:
        List of computed weights (f32), clamped to [0.3, 2.5] per F199A.

    Example:
        >>> items = [{"fetched": 100, "accepted": 70, "novelty": True}]
        >>> scores = await batch_compute_signals(items)
        >>> print(scores)
        [1.65]  # 1.0 * 1.10 * 1.5 novelty bonus = 1.65
    """
    # M1 8GB safety: chunk large batches
    if len(items) > _MAX_BATCH_SIZE:
        logger.debug(f"Splitting batch of {len(items)} items into chunks of {_MAX_BATCH_SIZE}")
        results = []
        for i in range(0, len(items), _MAX_BATCH_SIZE):
            chunk = items[i:i + _MAX_BATCH_SIZE]
            chunk_results = await batch_compute_signals(chunk, default_weight)
            results.extend(chunk_results)
        return results

    signal_batch = _get_signal_batch()

    if signal_batch is not None:
        try:
            # Use asyncio.to_thread for non-blocking execution of Rust code
            # This releases the event loop while Rust processes the batch
            return await asyncio.to_thread(
                signal_batch.batch_compute_scores,
                items,
                default_weight,
            )
        except Exception as exc:
            logger.warning(f"Rust batch_compute_scores failed: {exc}, using Python fallback")

    # Fallback to pure Python implementation
    return await asyncio.to_thread(
        _python_batch_compute_scores,
        items,
        default_weight,
    )


def _python_batch_compute_scores(
    items: list[dict[str, Any]],
    default_weight: float = 1.0,
) -> list[float]:
    """
    Pure Python fallback for batch_compute_scores.

    Computes source quality scores using the F199A formula:
    - ratio = accepted / max(fetched, 1)
    - delta: 1.10 if ratio >= 0.7, 1.05 if >= 0.4, 1.00 if >= 0.15, else 0.95
    - novelty_bonus: 1.5 if novel, else 1.0
    - weight = clamp(current_weight * delta * novelty_bonus, 0.3, 2.5)

    Args:
        items: List of source stats dicts
        default_weight: Weight when current_weight key is absent

    Returns:
        List of computed weights clamped to [0.3, 2.5]
    """
    results = []
    for item in items:
        fetched = item.get("fetched", 0)
        accepted = item.get("accepted", 0)
        current_weight = item.get("current_weight", default_weight)
        novelty = item.get("novelty", False)

        # Compute ratio
        ratio = accepted / max(fetched, 1)

        # Determine delta based on ratio thresholds
        if ratio >= 0.7:
            delta = 1.10
        elif ratio >= 0.4:
            delta = 1.05
        elif ratio >= 0.15:
            delta = 1.00
        else:
            delta = 0.95

        # Novelty bonus: 1.5 if novel, else 1.0
        novelty_bonus = 1.5 if novelty else 1.0

        # Compute weighted score
        weighted = current_weight * delta * novelty_bonus

        # Clamp to [0.3, 2.5] per F199A
        clamped = max(0.3, min(2.5, weighted))
        results.append(clamped)

    return results


async def batch_aggregate_signals(
    signals: list[list[float]],
    weights: list[float],
    normalize: bool = True,
) -> list[float]:
    """
    Aggregate signal vectors using per-source weights.

    Uses Rust batch_aggregate_signals with asyncio.to_thread for
    non-blocking execution, with fallback to pure Python implementation.

    M1 8GB Safety:
    - Large batches are split and processed in chunks

    Args:
        signals: List of signal vectors (list of floats).
        weights: Per-source weights (list of floats).
        normalize: If True, return weighted average. If False, return weighted sum.

    Returns:
        Aggregated signal vector (list of floats).
    """
    if not signals or not weights:
        return []

    # M1 8GB safety: chunk large batches
    if len(signals) > _MAX_BATCH_SIZE:
        logger.debug(f"Splitting aggregate batch of {len(signals)} sources into chunks")
        chunk_size = _MAX_BATCH_SIZE
        results = None
        for i in range(0, len(signals), chunk_size):
            chunk_signals = signals[i:i + chunk_size]
            chunk_weights = weights[i:i + chunk_size]
            chunk_result = await asyncio.to_thread(
                _python_batch_aggregate_signals,
                chunk_signals,
                chunk_weights,
                False,  # Don't normalize intermediate chunks
            )
            if results is None:
                results = chunk_result
            else:
                # Merge results
                for j in range(min(len(results), len(chunk_result))):
                    results[j] += chunk_result[j]
        if results and normalize:
            total_weight = sum(weights)
            if total_weight > 0:
                inv = 1.0 / total_weight
                results = [r * inv for r in results]
        return results or []

    signal_batch = _get_signal_batch()

    if signal_batch is not None:
        try:
            return await asyncio.to_thread(
                signal_batch.batch_aggregate_signals,
                signals,
                weights,
                normalize,
            )
        except Exception as exc:
            logger.warning(f"Rust batch_aggregate_signals failed: {exc}, using Python fallback")

    # Fallback to pure Python implementation
    return await asyncio.to_thread(
        _python_batch_aggregate_signals,
        signals,
        weights,
        normalize,
    )


def _python_batch_aggregate_signals(
    signals: list[list[float]],
    weights: list[float],
    normalize: bool = True,
) -> list[float]:
    """
    Pure Python fallback for batch_aggregate_signals.

    Aggregates signal vectors using per-source weights.

    Args:
        signals: List of signal vectors.
        weights: Per-source weights.
        normalize: If True, return weighted average.

    Returns:
        Aggregated signal vector.
    """
    if not signals or not weights:
        return []

    n_sources = min(len(signals), len(weights))
    if n_sources == 0:
        return []

    # Determine output vector length (min across all sources)
    out_len = min(len(sig) for sig in signals[:n_sources] if sig)

    if out_len == 0:
        return []

    result = [0.0] * out_len
    weight_sum = 0.0

    for i in range(n_sources):
        w = weights[i]
        if w <= 0.0:
            continue
        weight_sum += w

        sig = signals[i]
        for j in range(min(out_len, len(sig))):
            result[j] += sig[j] * w

    if normalize and weight_sum > 0.0:
        inv = 1.0 / weight_sum
        result = [r * inv for r in result]

    return result
