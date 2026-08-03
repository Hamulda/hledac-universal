"""Scan stage — pattern scan on assembled feed text.

Responsibilities:
- Apply Rust Aho-Corasick pattern matching to assembled text
- Use Rust feed_pipeline if available
- Fall back to Python PatternMatcher

Input: FeedAssembledBatch
Output: FeedMatchedBatch (entry_urls, matched_pattern_counts, matched_pattern_labels, entry_dedup_hits, errors)
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from hledac.universal.pipeline._soa_types import FeedAssembledBatch, FeedMatchedBatch
from hledac.universal.utils.async_helpers import parallel_ok

logger = logging.getLogger(__name__)


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
        from hledac.universal.core.rust_backend import rust
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
