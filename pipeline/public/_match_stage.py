"""Match stage — PatternMatcher dispatch for public OSINT pipeline.

Responsibilities:
- Dispatch pattern matching to PatternMatcher (offloaded to thread pool)
- Batch process multiple URLs concurrently
- Track matched pattern counts and labels

Input: ScoredBatch (urls, quality_signals, usable_signals, value_tiers, ...)
Output: MatchedBatch (urls, matched_pattern_counts, matched_pattern_labels, ...)
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from hledac.universal.pipeline._soa_types import MatchedBatch, ScoredBatch
from hledac.universal.utils.async_helpers import parallel_ok

logger = logging.getLogger(__name__)

# Default concurrency for pattern matching
_DEFAULT_MATCH_CONCURRENCY: int = 8


class MatchStage:
    """Match stage: ScoredBatch → MatchedBatch.

    Applies PatternMatcher to each page's text.
    """

    __slots__ = ("_match_concurrency",)

    def __init__(self, match_concurrency: int = _DEFAULT_MATCH_CONCURRENCY) -> None:
        self._match_concurrency = match_concurrency

    @property
    def name(self) -> str:
        return "match"

    async def process(
        self, input_batch: ScoredBatch | None
    ) -> tuple[MatchedBatch, dict[str, Any]]:
        """Match patterns in scored pages.

        Args:
            input_batch: ScoredBatch with scored pages

        Returns:
            Tuple of (MatchedBatch, telemetry)

        """
        if input_batch is None or not input_batch.urls:
            return self._empty_batch(), {}

        telemetry: dict[str, Any] = {
            "pages_matched": 0,
            "total_matches": 0,
            "pages_no_match": 0,
            "match_errors": 0,
        }

        # Filter to usable pages only
        usable_indices = [
            i
            for i, usable in enumerate(input_batch.usable_signals)
            if usable and i < len(input_batch.urls)
        ]

        if not usable_indices:
            # Return empty results for all URLs
            matched_pattern_counts = [0] * len(input_batch.urls)
            matched_pattern_labels: list[list[str]] = [[] for _ in input_batch.urls]
            errors = [None] * len(input_batch.urls)

            return MatchedBatch(
                urls=input_batch.urls,
                matched_pattern_counts=matched_pattern_counts,
                matched_pattern_labels=matched_pattern_labels,
                errors=errors,
            ), telemetry

        # Extract usable texts (preserved from FetchedBatch via ExtractStage)
        usable_texts: list[str] = []
        usable_indices_list: list[int] = []
        for idx in usable_indices:
            if idx < len(input_batch.urls) and idx < len(input_batch.texts):
                text = input_batch.texts[idx]
                usable_texts.append(text)
                usable_indices_list.append(idx)

        # Match patterns concurrently
        match_results = await _match_batch(
            texts=usable_texts,
            concurrency=self._match_concurrency,
        )

        # Build result arrays (all URLs, not just usable)
        matched_pattern_counts = [0] * len(input_batch.urls)
        matched_pattern_labels: list[list[str]] = [[] for _ in input_batch.urls]
        errors: list[str | None] = [None] * len(input_batch.urls)

        for i, idx in enumerate(usable_indices_list):
            if idx < len(matched_pattern_counts):
                result = match_results[i]
                matched_pattern_counts[idx] = result["count"]
                matched_pattern_labels[idx] = result["labels"]
                errors[idx] = result.get("error")

                if result["count"] > 0:
                    telemetry["pages_matched"] += 1
                    telemetry["total_matches"] += result["count"]
                else:
                    telemetry["pages_no_match"] += 1

                if result.get("error"):
                    telemetry["match_errors"] += 1

        batch = MatchedBatch(
            urls=input_batch.urls,
            matched_pattern_counts=matched_pattern_counts,
            matched_pattern_labels=matched_pattern_labels,
            errors=errors,
        )

        return batch, telemetry

    def _empty_batch(self) -> MatchedBatch:
        return MatchedBatch(
            urls=[],
            matched_pattern_counts=[],
            matched_pattern_labels=[],
            errors=[],
        )


async def _match_batch(
    texts: list[str],
    concurrency: int = _DEFAULT_MATCH_CONCURRENCY,
) -> list[dict[str, Any]]:
    """Match patterns in a batch of texts.

    Args:
        texts: List of text strings to match against
        concurrency: Max concurrent match operations

    Returns:
        List of match results (one per text)

    """
    if not texts:
        return []

    semaphore = asyncio.Semaphore(concurrency)

    async def match_one(text: str, idx: int) -> dict[str, Any]:
        async with semaphore:
            return await asyncio.to_thread(_sync_match_text, text, idx)

    tasks = [match_one(text, i) for i, text in enumerate(texts)]
    # F3XX: parallel_ok() replaces asyncio.gather — returns list[T] in original order.
    results = await parallel_ok(*tasks, label="match_stage")

    output: list[dict[str, Any]] = []
    for result in results:
        output.append(result)

    return output


def _sync_match_text(text: str, idx: int) -> dict[str, Any]:
    """Match patterns synchronously in thread pool.

    Delegates to PatternMatcher from live_public_pipeline.py.
    """
    try:
        # Import here to avoid circular dependency
        from hledac.universal.pipeline.live_public_pipeline import (
            _SYNC_MATCH_TEXT,
            _ensure_patched,
        )

        _ensure_patched()

        if _SYNC_MATCH_TEXT is None:
            return {"count": 0, "labels": [], "error": "PatternMatcher not patched"}

        matches = _SYNC_MATCH_TEXT(text)
        labels = [m.get("label", "unknown") for m in matches] if matches else []

        return {"count": len(labels), "labels": labels, "error": None}

    except Exception as exc:
        logger.warning(f"Pattern match failed for text[{idx}]: {exc}")
        return {"count": 0, "labels": [], "error": str(exc)}
