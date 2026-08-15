"""Match stage — PatternMatcher + SIMD IOC scanning for public OSINT pipeline.

Responsibilities:
- Dispatch pattern matching to PatternMatcher (offloaded to thread pool)
- Parallel SIMD IOC scanning via Rust Aho-Corasick NEON (HEIST-01)

- Batch process multiple URLs concurrently
- Track matched pattern counts and labels

Input: ScoredBatch (urls, quality_signals, usable_signals, value_tiers, ...)
Output: MatchedBatch (urls, matched_pattern_counts, matched_pattern_labels, ...)
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from hledac.universal.core.rust_backend.ioc_stream import (
    get_ioc_scanner,
    get_scanner_stats,
    scan_bytes_with_ioc_scanner,
)
from hledac.universal.pipeline._soa_types import MatchedBatch, ScoredBatch
from hledac.universal.utils.asyncx import parallel_ok
from core import aclose

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
        # HEIST-01: Parallel SIMD IOC scan via Rust Aho-Corasick NEON
        simd_results = await _simd_scan_batch(
            texts=usable_texts,
            concurrency=self._match_concurrency,
        )
        match_results = await _match_batch(
            texts=usable_texts,
            concurrency=self._match_concurrency,
        )

        # Build result arrays (all URLs, not just usable)
        matched_pattern_counts = [0] * len(input_batch.urls)
        matched_pattern_labels: list[list[str]] = [[] for _ in input_batch.urls]
        errors: list[str | None] = [None] * len(input_batch.urls)

        # HEIST-01: Merge SIMD results with PatternMatcher results
        simd_total = 0
        for i, idx in enumerate(usable_indices_list):
            if idx < len(matched_pattern_counts):
                result = match_results[i]
                simd_result = simd_results[i] if i < len(simd_results) else {"count": 0, "labels": []}

                # Combine regex + SIMD hits
                combined_count = result["count"] + simd_result["count"]
                
                # Deduplicate labels across both scanners (case-insensitive)
                seen_labels: set[str] = set()
                combined_labels: list[str] = []
                for label in result["labels"] + simd_result["labels"]:
                    label_lower = label.lower()
                    if label_lower not in seen_labels:
                        seen_labels.add(label_lower)
                        combined_labels.append(label)

                matched_pattern_counts[idx] = combined_count
                matched_pattern_labels[idx] = combined_labels
                errors[idx] = result.get("error")

                simd_total += simd_result["count"]

                if combined_count > 0:
                    telemetry["pages_matched"] += 1
                    telemetry["total_matches"] += combined_count
                else:
                    telemetry["pages_no_match"] += 1

                if result.get("error"):
                    telemetry["match_errors"] += 1

        # HEIST-01: Telemetry for SIMD scan stats
        telemetry["simd_total_hits"] = simd_total
        
        # Scanner stats: availability, pattern count, automaton size
        scanner_stats = get_scanner_stats()
        telemetry["simd_scanner_available"] = scanner_stats["available"]
        telemetry["simd_scanner_pattern_count"] = scanner_stats["pattern_count"]
        telemetry["simd_scanner_automaton_bytes"] = scanner_stats["automaton_bytes"]

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

    return list(results)


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


# HEIST-01: SIMD IOC Scanning via Rust Aho-Corasick NEON
# ---------------------------------------------------------------------------


async def _simd_scan_batch(
    texts: list[str],
    concurrency: int = _DEFAULT_MATCH_CONCURRENCY,
) -> list[dict[str, Any]]:
    """Batch SIMD IOC scan using Rust Aho-Corasick streaming scanner.

    Uses asyncio.to_thread() for non-blocking execution on M1.
    Falls back to empty results if Rust extension unavailable.

    Args:
        texts: List of text strings to scan
        concurrency: Max concurrent scan operations

    Returns:
        List of scan results (one per text) with keys: count, labels
    """
    if not texts:
        return []

    # Pre-encode texts once to avoid repeated encoding in async tasks
    # This is an optimization: encoding is done once upfront
    encoded_texts: list[bytes] = [t.encode("utf-8") for t in texts]

    semaphore = asyncio.Semaphore(concurrency)

    async def simd_scan_one(buffer: bytes, idx: int) -> dict[str, Any]:
        async with semaphore:
            try:
                hits = await scan_bytes_with_ioc_scanner(buffer)

                # Extract labels from hits (deduplicated, order preserved, case-insensitive)
                seen: set[str] = set()
                labels: list[str] = []
                for hit in hits:
                    label = hit.get("label", "unknown")
                    label_lower = label.lower()
                    if label_lower not in seen:
                        seen.add(label_lower)
                        labels.append(label)  # Keep original case from first hit

                return {"count": len(hits), "labels": labels, "error": None}

            except Exception as exc:
                logger.debug(f"SIMD scan failed for text[{idx}]: {exc}")
                return {"count": 0, "labels": [], "error": str(exc)}

    tasks = [simd_scan_one(buffer, i) for i, buffer in enumerate(encoded_texts)]
    results = await parallel_ok(*tasks, label="simd_scan")

    return list(results)
