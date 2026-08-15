"""Assemble stage — text assembly from feed entries for feed pipeline.

Responsibilities:
- Assemble clean text from feed entry title + summary
- Compute entry quality signals
- Handle HTML stripping

Input: FeedEntryBatch
Output: FeedAssembledBatch (entry_urls, assembled_texts, assembled_text_lens, quality_signals)
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from hledac.universal.pipeline._soa_types import FeedAssembledBatch, FeedEntryBatch
from core import aclose

logger = logging.getLogger(__name__)

# Compiled once at module level for O(1) reuse
_SCRIPT_TAG_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_TAG_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


class AssembleStage:
    """Assemble stage: FeedEntryBatch → FeedAssembledBatch.

    Assembles clean text from feed entries and computes quality signals.
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        return "assemble"

    async def process(
        self, input_batch: FeedEntryBatch | None
    ) -> tuple[FeedAssembledBatch, dict[str, Any]]:
        """Assemble feed entry texts.

        Args:
            input_batch: FeedEntryBatch with raw feed entries

        Returns:
            Tuple of (FeedAssembledBatch, telemetry)

        """
        if input_batch is None or not input_batch.entry_urls:
            return self._empty_batch(), {}

        telemetry: dict[str, Any] = {
            "entries_assembled": len(input_batch.entry_urls),
            "entries_empty": 0,
            "assemble_errors": 0,
        }

        assembled_texts: list[str] = []
        assembled_text_lens: list[int] = []
        quality_signals: list[dict[str, Any]] = []

        for i in range(len(input_batch.entry_urls)):
            title = input_batch.entry_titles[i] if i < len(input_batch.entry_titles) else ""
            summary = input_batch.entry_summaries[i] if i < len(input_batch.entry_summaries) else ""

            try:
                assembled = _assemble_clean_feed_text(title, summary)
                assembled_texts.append(assembled)
                assembled_text_lens.append(len(assembled))

                # Compute quality signal
                signal = _compute_entry_quality_signal(assembled, len(assembled))
                quality_signals.append(signal)

                if not assembled:
                    telemetry["entries_empty"] += 1

            except Exception as exc:
                telemetry["assemble_errors"] += 1
                assembled_texts.append("")
                assembled_text_lens.append(0)
                quality_signals.append({})

        batch = FeedAssembledBatch(
            entry_urls=input_batch.entry_urls,
            assembled_texts=assembled_texts,
            assembled_text_lens=assembled_text_lens,
            quality_signals=quality_signals,
        )

        return batch, telemetry

    def _empty_batch(self) -> FeedAssembledBatch:
        return FeedAssembledBatch(
            entry_urls=[],
            assembled_texts=[],
            assembled_text_lens=[],
            quality_signals=[],
        )


def _assemble_clean_feed_text(title: str, summary: str) -> str:
    """Assemble clean text from feed entry title + summary.

    From pipeline/scoring.py _assemble_clean_feed_text.
    """
    # Strip HTML tags from summary
    clean_summary = _strip_html_tags(summary)

    # Combine title and summary
    parts = [title, clean_summary]
    assembled = " | ".join(p.strip() for p in parts if p.strip())

    return assembled


def _strip_html_tags(text: str) -> str:
    """Strip HTML tags from text."""
    if not text:
        return ""

    # Remove script and style elements
    text = _SCRIPT_TAG_RE.sub("", text)
    text = _STYLE_TAG_RE.sub("", text)

    # Replace common HTML entities
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')

    # Replace tags with spaces
    text = _HTML_TAG_RE.sub(" ", text)

    # Normalize whitespace
    text = _WHITESPACE_RE.sub(" ", text).strip()

    return text


def _compute_entry_quality_signal(text: str, text_len: int) -> dict[str, Any]:
    """Compute quality signal for a feed entry.

    From pipeline/scoring.py _compute_entry_quality_signal.
    Returns EntryQualitySignal as dict.
    """
    if not text:
        return {
            "signal": 0.0,
            "has_content": False,
            "entropy_score": 0.0,
            "length_score": 0.0,
        }

    # Entropy-based signal
    unique_chars = len(set(text.lower()))
    entropy_score = min(unique_chars / 50.0, 1.0)

    # Length-based signal
    length_score = min(text_len / 2000.0, 1.0)

    # Combined signal
    signal = (entropy_score * 0.4) + (length_score * 0.6)

    return {
        "signal": min(signal, 1.0),
        "has_content": text_len > 100,
        "entropy_score": entropy_score,
        "length_score": length_score,
    }
