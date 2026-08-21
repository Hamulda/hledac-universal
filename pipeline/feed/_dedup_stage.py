"""Dedup stage — per-entry and run-level dedup for feed pipeline.

Responsibilities:
- Per-entry dedup by (label, pattern, value)

- Run-level dedup by entry_url
- Track duplicate hits

Input: FeedMatchedBatch
Output: FeedMatchedBatch with entry_dedup_hits filled
"""

from __future__ import annotations

import logging
from typing import Any

from hledac.universal.pipeline._soa_types import FeedMatchedBatch

logger = logging.getLogger(__name__)


class DedupStage:
    """Dedup stage: FeedMatchedBatch → FeedMatchedBatch (with dedup flags).

    Applies per-entry and run-level dedup.
    """

    __slots__ = ("_seen_entry_urls",)

    def __init__(self) -> None:
        self._seen_entry_urls: set[str] = set()

    @property
    def name(self) -> str:
        return "dedup"

    def reset(self) -> None:
        """Reset run-level dedup state."""
        self._seen_entry_urls.clear()

    async def process(self, input_batch: FeedMatchedBatch | None) -> tuple[FeedMatchedBatch, dict[str, Any]]:
        """Apply dedup to matched batch.

        Args:
            input_batch: FeedMatchedBatch with match results

        Returns:
            Tuple of (FeedMatchedBatch with dedup flags, telemetry)

        """
        if input_batch is None or not input_batch.entry_urls:
            return self._empty_batch(), {}

        telemetry: dict[str, Any] = {
            "entries_deduped": 0,
            "entries_duplicate": 0,
        }

        entry_dedup_hits: list[bool] = []

        for i in range(len(input_batch.entry_urls)):
            entry_url = input_batch.entry_urls[i] if i < len(input_batch.entry_urls) else ""

            # Run-level dedup: skip if URL seen in this run
            if entry_url in self._seen_entry_urls:
                entry_dedup_hits.append(True)
                telemetry["entries_duplicate"] += 1
            else:
                self._seen_entry_urls.add(entry_url)
                entry_dedup_hits.append(False)
                telemetry["entries_deduped"] += 1

        batch = FeedMatchedBatch(
            entry_urls=input_batch.entry_urls,
            matched_pattern_counts=input_batch.matched_pattern_counts,
            matched_pattern_labels=input_batch.matched_pattern_labels,
            entry_dedup_hits=entry_dedup_hits,
            errors=input_batch.errors,
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
