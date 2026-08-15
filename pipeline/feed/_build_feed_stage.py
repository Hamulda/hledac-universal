"""Build feed stage — CanonicalFinding construction for feed pipeline.

Responsibilities:
- Build CanonicalFinding objects from matched feed entries

- Assign finding IDs, timestamps, confidences
- Encode payloads as bytes for zero-copy storage

Input: FeedMatchedBatch
Output: FindingBatch (finding_ids, urls, titles, snippets, timestamps, confidences, payloads, ...)
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any

from hledac.universal.pipeline._soa_types import FeedMatchedBatch, FindingBatch
from core import aclose

logger = logging.getLogger(__name__)

_DEFAULT_CONFIDENCE: float = 0.8
_SOURCE_TYPE: str = "rss_atom_pipeline"


class BuildFeedStage:
    """Build feed stage: FeedMatchedBatch → FindingBatch.

    Builds CanonicalFinding-ready batch from matched feed entries.
    """

    __slots__ = ("_source_type", "_default_confidence")

    def __init__(
        self,
        source_type: str = _SOURCE_TYPE,
        default_confidence: float = _DEFAULT_CONFIDENCE,
    ) -> None:
        self._source_type = source_type
        self._default_confidence = default_confidence

    @property
    def name(self) -> str:
        return "build_feed"

    async def process(
        self, input_tuple: tuple[FeedMatchedBatch, Any] | FeedMatchedBatch | None
    ) -> tuple[FindingBatch, dict[str, Any]]:
        """Build findings from matched feed entries.

        Args:
            input_tuple: Tuple of (FeedMatchedBatch, query_context) or just FeedMatchedBatch

        Returns:
            Tuple of (FindingBatch, telemetry)

        """
        # Handle both tuple and single batch input
        if isinstance(input_tuple, tuple):
            matched_batch = input_tuple[0]
            query_context = input_tuple[1] if len(input_tuple) > 1 else ""
        else:
            matched_batch = input_tuple
            query_context = ""

        if matched_batch is None or not matched_batch.entry_urls:
            return self._empty_batch(), {}

        telemetry: dict[str, Any] = {
            "findings_built": 0,
            "findings_filtered": 0,
            "build_errors": 0,
        }

        finding_ids: list[str] = []
        urls: list[str] = []
        titles: list[str] = []
        snippets: list[str] = []
        query_contexts: list[str] = []
        timestamps: list[float] = []
        confidences: list[float] = []
        source_types: list[str] = []
        payloads: list[bytes] = []
        raw_payloads: list[bytes] = []
        matched_pattern_labels: list[list[str]] = []

        for i in range(len(matched_batch.entry_urls)):
            entry_url = matched_batch.entry_urls[i] if i < len(matched_batch.entry_urls) else ""
            pattern_count = (
                matched_batch.matched_pattern_counts[i]
                if i < len(matched_batch.matched_pattern_counts)
                else 0
            )
            pattern_labels = (
                matched_batch.matched_pattern_labels[i]
                if i < len(matched_batch.matched_pattern_labels)
                else []
            )
            is_dup = (
                matched_batch.entry_dedup_hits[i]
                if i < len(matched_batch.entry_dedup_hits)
                else False
            )
            error = matched_batch.errors[i] if i < len(matched_batch.errors) else None

            # Skip duplicates and pages with no matches or errors
            if is_dup:
                telemetry["findings_filtered"] += 1
                continue
            if error:
                telemetry["findings_filtered"] += 1
                continue
            if pattern_count == 0:
                telemetry["findings_filtered"] += 1
                continue

            # Build finding
            try:
                finding_id = _make_feed_finding_id(entry_url, query_context)
                timestamp = time.time()
                confidence = min(self._default_confidence + (pattern_count * 0.01), 1.0)
                payload = _encode_payload(entry_url, pattern_labels)
                raw_payload = _encode_raw_payload(entry_url)

                finding_ids.append(finding_id)
                urls.append(entry_url)
                titles.append("")  # filled from FeedMatchedBatch if available
                snippets.append("")
                query_contexts.append(str(query_context))
                timestamps.append(timestamp)
                confidences.append(confidence)
                source_types.append(self._source_type)
                payloads.append(payload)
                raw_payloads.append(raw_payload)
                matched_pattern_labels.append(pattern_labels)
                telemetry["findings_built"] += 1

            except Exception as exc:
                telemetry["build_errors"] += 1
                logger.warning(f"Build feed failed for {entry_url}: {exc}")

        batch = FindingBatch(
            finding_ids=finding_ids,
            urls=urls,
            titles=titles,
            snippets=snippets,
            query_contexts=query_contexts,
            timestamps=timestamps,
            confidences=confidences,
            source_types=source_types,
            payloads=payloads,
            raw_payloads=raw_payloads,
            matched_pattern_labels=matched_pattern_labels,
        )

        return batch, telemetry

    def _empty_batch(self) -> FindingBatch:
        return FindingBatch(
            finding_ids=[],
            urls=[],
            titles=[],
            snippets=[],
            query_contexts=[],
            timestamps=[],
            confidences=[],
            source_types=[],
            payloads=[],
            raw_payloads=[],
            matched_pattern_labels=[],
        )


def _make_feed_finding_id(entry_url: str, query_context: str = "") -> str:
    """Generate deterministic finding ID from entry URL + query."""
    data = f"{entry_url}:{query_context}".encode()
    return hashlib.sha256(data).hexdigest()[:32]


def _encode_payload(entry_url: str, pattern_labels: list[str]) -> bytes:
    """Encode URL + patterns as bytes for zero-copy storage."""
    import msgspec

    data = {"url": entry_url, "patterns": pattern_labels}
    return msgspec.json.encode(data)


def _encode_raw_payload(entry_url: str) -> bytes:
    """Encode URL as raw bytes."""
    return entry_url.encode("utf-8")
