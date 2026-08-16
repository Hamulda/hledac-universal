"""Build stage — CanonicalFinding construction for public OSINT pipeline.

Responsibilities:
- Build CanonicalFinding objects from matched pages

- Assign finding IDs, timestamps, confidences
- Encode payloads as bytes for zero-copy storage

Input: MatchedBatch (urls, matched_pattern_counts, matched_pattern_labels, ...)
Output: FindingBatch (finding_ids, urls, titles, snippets, timestamps, confidences, payloads, ...)
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any

from hledac.universal.pipeline._soa_types import FindingBatch, MatchedBatch
from _core import aclose

logger = logging.getLogger(__name__)

_DEFAULT_CONFIDENCE: float = 0.8


class BuildStage:
    """Build stage: MatchedBatch → FindingBatch.

    Builds CanonicalFinding-ready batch from matched pages.
    """

    __slots__ = ("_source_type", "_default_confidence")

    def __init__(
        self,
        source_type: str = "live_public_pipeline",
        default_confidence: float = _DEFAULT_CONFIDENCE,
    ) -> None:
        self._source_type = source_type
        self._default_confidence = default_confidence

    @property
    def name(self) -> str:
        return "build"

    async def process(
        self, input_tuple: tuple[MatchedBatch, Any] | MatchedBatch | None
    ) -> tuple[FindingBatch, dict[str, Any]]:
        """Build findings from matched pages.

        Args:
            input_tuple: Tuple of (MatchedBatch, query_context) or just MatchedBatch

        Returns:
            Tuple of (FindingBatch, telemetry)

        """
        # Handle both tuple and single batch input
        if isinstance(input_tuple, tuple):
            matched_batch, query_context = input_tuple[0], input_tuple[1] if len(input_tuple) > 1 else ""
        else:
            matched_batch = input_tuple
            query_context = ""

        if matched_batch is None or not matched_batch.urls:
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

        for i in range(len(matched_batch.urls)):
            url = matched_batch.urls[i] if i < len(matched_batch.urls) else ""
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
            error = matched_batch.errors[i] if i < len(matched_batch.errors) else None

            # Skip pages with no matches or errors
            if error:
                telemetry["findings_filtered"] += 1
                continue
            if pattern_count == 0:
                telemetry["findings_filtered"] += 1
                continue

            # Build finding
            try:
                finding_id = _make_finding_id(url, query_context)
                timestamp = time.time()
                confidence = min(self._default_confidence + (pattern_count * 0.01), 1.0)
                payload = _encode_payload(url, pattern_labels)
                raw_payload = _encode_raw_payload(url)

                finding_ids.append(finding_id)
                urls.append(url)
                titles.append("")  # filled from matched_batch if available
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
                logger.warning(f"Build failed for {url}: {exc}")

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


def _make_finding_id(url: str, query_context: str = "") -> str:
    """Generate deterministic finding ID from URL + query."""
    data = f"{url}:{query_context}".encode()
    return hashlib.sha256(data).hexdigest()[:32]


def _encode_payload(url: str, pattern_labels: list[str]) -> bytes:
    """Encode URL + patterns as bytes for zero-copy storage."""
    import msgspec

    data = {"url": url, "patterns": pattern_labels}
    return msgspec.json.encode(data)


def _encode_raw_payload(url: str) -> bytes:
    """Encode URL as raw bytes."""
    return url.encode("utf-8")
