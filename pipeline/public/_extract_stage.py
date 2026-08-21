"""Extract stage — text extraction + quality scoring for public OSINT pipeline.

Responsibilities:
- Compute quality signals from fetched page text
- Determine usable vs waste pages
- Assign value tiers (high/medium/low/waste)
- Identify discovery false positives

Input: FetchedBatch (urls, texts, text_lens, fetch_errors, ...)
Output: ScoredBatch (urls, quality_signals, usable_signals, value_tiers, ...)
"""

from __future__ import annotations

import logging
from typing import Any

from hledac.universal.pipeline._soa_types import FetchedBatch, ScoredBatch

logger = logging.getLogger(__name__)

# Thresholds from live_public_pipeline
_DISCOVERY_SKIP_THRESHOLD: float = 0.15
_PRE_FETCH_TEXT_MIN_CHARS: int = 80

# Rust batch_quality_score — lazy import, falls back to Python loop
_rust_batch_quality_score: Any | None = None


def _get_rust_batch_quality_score() -> Any | None:
    """Lazily import batch_quality_score from Rust signal_batch module."""
    global _rust_batch_quality_score
    if _rust_batch_quality_score is None:
        try:
            from hledac.universal.rust_extensions import signal_batch as _sig

            _rust_batch_quality_score = getattr(_sig, "batch_quality_score", None)
        except Exception:
            _rust_batch_quality_score = None
    return _rust_batch_quality_score


class ExtractStage:
    """Extract stage: FetchedBatch → ScoredBatch.

    Applies quality scoring to each fetched page.
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        return "extract"

    async def process(self, input_batch: FetchedBatch | None) -> tuple[ScoredBatch, dict[str, Any]]:
        """Score fetched pages.

        Args:
            input_batch: FetchedBatch with fetched page texts

        Returns:
            Tuple of (ScoredBatch, telemetry)

        """
        if input_batch is None or not input_batch.urls:
            return self._empty_batch(), {}

        telemetry: dict[str, Any] = {
            "pages_scored": len(input_batch.urls),
            "pages_usable": 0,
            "pages_waste": 0,
            "pages_skipped": 0,
            "discovery_false_positives": 0,
            "rust_accelerated": False,
        }

        quality_signals: list[float] = []
        usable_signals: list[bool] = []
        value_tiers: list[str] = []
        waste_categories: list[str] = []
        structural_qualities: list[str] = []
        discovery_false_positives: list[bool] = []
        skipped_reasons: list[str | None] = []

        # Try Rust batch_quality_score first (rayon parallel MAP)
        rust_fn = _get_rust_batch_quality_score()
        if rust_fn is not None and len(input_batch.urls) > 0:
            try:
                # Call Rust batch_quality_score
                n = len(input_batch.urls)
                texts_list: list[str] = [input_batch.texts[i] if i < len(input_batch.texts) else "" for i in range(n)]
                text_lens_list: list[int] = [
                    input_batch.text_lens[i] if i < len(input_batch.text_lens) else 0 for i in range(n)
                ]
                fetch_errors_list: list[str | None] = [
                    input_batch.fetch_errors[i] if i < len(input_batch.fetch_errors) else None for i in range(n)
                ]
                failure_stages_list: list[str | None] = [
                    input_batch.failure_stages[i] if i < len(input_batch.failure_stages) else None for i in range(n)
                ]

                rust_results = rust_fn(text_lens_list, texts_list, fetch_errors_list, failure_stages_list)

                for result in rust_results:
                    signal, tier, waste_cat, structural, is_fp, skip_reason = result
                    quality_signals.append(float(signal))
                    usable_signals.append(tier != "waste")
                    value_tiers.append(str(tier))
                    waste_categories.append(str(waste_cat))
                    structural_qualities.append(str(structural))
                    discovery_false_positives.append(bool(is_fp))
                    skipped_reasons.append(str(skip_reason) if skip_reason else None)

                    if tier != "waste":
                        telemetry["pages_usable"] += 1
                    else:
                        telemetry["pages_waste"] += 1
                    if is_fp:
                        telemetry["discovery_false_positives"] += 1
                    if skip_reason:
                        telemetry["pages_skipped"] += 1

                telemetry["rust_accelerated"] = True
            except Exception as exc:
                logger.warning(f"Rust batch_quality_score failed, falling back to Python: {exc}")
                # Fall through to Python loop
                quality_signals.clear()
                usable_signals.clear()
                value_tiers.clear()
                waste_categories.clear()
                structural_qualities.clear()
                discovery_false_positives.clear()
                skipped_reasons.clear()
                telemetry["pages_usable"] = 0
                telemetry["pages_waste"] = 0
                telemetry["pages_skipped"] = 0
                telemetry["discovery_false_positives"] = 0
                telemetry["rust_accelerated"] = False

        # Python loop fallback (or if Rust unavailable)
        if not telemetry["rust_accelerated"]:
            for i in range(len(input_batch.urls)):
                text = input_batch.texts[i] if i < len(input_batch.texts) else ""
                text_len = input_batch.text_lens[i] if i < len(input_batch.text_lens) else 0
                fetch_error = input_batch.fetch_errors[i] if i < len(input_batch.fetch_errors) else None
                failure_stage = input_batch.failure_stages[i] if i < len(input_batch.failure_stages) else None

                # Compute quality signal
                signal, tier, waste_cat, structural, is_fp, skip_reason = _score_one(
                    text=text,
                    text_len=text_len,
                    fetch_error=fetch_error,
                    failure_stage=failure_stage,
                )

                quality_signals.append(signal)
                usable_signals.append(tier != "waste")
                value_tiers.append(tier)
                waste_categories.append(waste_cat)
                structural_qualities.append(structural)
                discovery_false_positives.append(is_fp)
                skipped_reasons.append(skip_reason)

                if tier != "waste":
                    telemetry["pages_usable"] += 1
                else:
                    telemetry["pages_waste"] += 1
                if is_fp:
                    telemetry["discovery_false_positives"] += 1
                if skip_reason:
                    telemetry["pages_skipped"] += 1

        # Preserve texts for MatchStage (index-aligned with urls)
        texts: list[str] = [
            input_batch.texts[i] if i < len(input_batch.texts) else "" for i in range(len(input_batch.urls))
        ]

        batch = ScoredBatch(
            urls=input_batch.urls,
            texts=texts,
            quality_signals=quality_signals,
            usable_signals=usable_signals,
            value_tiers=value_tiers,
            waste_categories=waste_categories,
            structural_qualities=structural_qualities,
            discovery_false_positives=discovery_false_positives,
            skipped_reasons=skipped_reasons,
        )

        return batch, telemetry

    def _empty_batch(self) -> ScoredBatch:
        return ScoredBatch(
            urls=[],
            texts=[],
            quality_signals=[],
            usable_signals=[],
            value_tiers=[],
            waste_categories=[],
            structural_qualities=[],
            discovery_false_positives=[],
            skipped_reasons=[],
        )


def _score_one(
    text: str,
    text_len: int,
    fetch_error: str | None,
    failure_stage: str | None,
) -> tuple[float, str, str, str, bool, str | None]:
    """Score a single page.

    Returns: (quality_signal, value_tier, waste_category, structural_quality, discovery_false_positive, skipped_reason)
    """
    # Error case
    if fetch_error:
        return 0.0, "waste", "error", "", False, f"fetch_error:{fetch_error[:50]}"

    # Empty page
    if not text or text_len < _PRE_FETCH_TEXT_MIN_CHARS:
        return 0.0, "waste", "signalless", "thin", False, "text_too_short"

    # Failure stage
    if failure_stage:
        return 0.0, "waste", "error", "", False, f"failure_stage:{failure_stage}"

    # Compute quality signal from text characteristics
    # (simplified from live_public_pipeline._score_page_quality)
    signal = _compute_quality_signal(text, text_len)

    # Determine tier
    if signal >= 0.7:
        tier = "high"
    elif signal >= 0.4:
        tier = "medium"
    elif signal >= _DISCOVERY_SKIP_THRESHOLD:
        tier = "low"
    else:
        tier = "waste"

    # Structural quality
    if text_len > 1000:
        structural = "healthy"
    elif text_len > 200:
        structural = "thin"
    else:
        structural = "dead"

    return signal, tier, "", structural, False, None


def _compute_quality_signal(text: str, text_len: int) -> float:
    """Compute quality signal from text.

    Simplified from live_public_pipeline._score_page_quality.
    """
    if not text:
        return 0.0

    # Entropy-based signal (simple heuristic)
    unique_chars = len(set(text.lower()))
    entropy_score = min(unique_chars / 50.0, 1.0)

    # Length-based signal
    length_score = min(text_len / 5000.0, 1.0)

    # Combined signal
    signal = (entropy_score * 0.4) + (length_score * 0.6)

    return min(signal, 1.0)
