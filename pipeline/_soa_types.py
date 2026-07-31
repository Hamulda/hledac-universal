"""SoA (Structure of Arrays) batch types for pipeline stage data flow.

These msgspec Structs represent batched data passed between pipeline stages.
Using SoA (not AoS) enables:
- Zero-copy Arrow conversion for Rust/MLX stages
- SIMD vectorization in Rust rayon stages
- Cache-friendly memory layout
- Bounded memory allocation (batch size explicit)

Each field is a list of homogeneous values — all indexes correspond to the same item.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import msgspec

if TYPE_CHECKING:
    pass


class PageBatch(msgspec.Struct, frozen=True, gc=False):
    """Structure of Arrays for batch page processing.

    Represents a batch of discovered pages awaiting fetch, extract, and match.
    All lists are index-aligned: urls[i], titles[i], etc. describe page i.

    Bounded: max batch size enforced by stage orchestrator (default 256).
    Zero-copy: Arrow conversion via msgspec.Struct.as_bytes() + PyArrow.
    """

    urls: list[str]
    titles: list[str]
    snippets: list[str]
    ranks: list[int]
    discovery_scores: list[float]
    fetch_blocked_reasons: list[str | None]  # None = not blocked
    errors: list[str | None]  # None = no error


class FetchedBatch(msgspec.Struct, frozen=True, gc=False):
    """Structure of Arrays for batch after fetch stage.

    index-aligned with PageBatch: fetched_batch.urls[i] == page_batch.urls[i]
    """

    urls: list[str]
    texts: list[str]  # extracted page text (may be empty if fetch failed)
    text_lens: list[int]
    fetch_errors: list[str | None]  # None = success
    failure_stages: list[str | None]  # validation | connection | tls | http | body | size
    redirects: list[str | None]  # redirect target URL if redirected
    js_renderer_skipped_reasons: list[str | None]  # why JS renderer was skipped
    fetch_blocked_reasons: list[str | None]  # uma_memory | quality_skip


class ScoredBatch(msgspec.Struct, frozen=True, gc=False):
    """Structure of Arrays after quality scoring stage.

    index-aligned with FetchedBatch.
    """

    urls: list[str]
    texts: list[str]  # preserved from FetchedBatch for MatchStage
    quality_signals: list[float]  # 0.0-1.0 page quality score
    usable_signals: list[bool]  # True if page converted to usable value
    value_tiers: list[str]  # high | medium | low | waste
    waste_categories: list[str]  # "" | structural | signalless | false_positive | error
    structural_qualities: list[str]  # "" | healthy | thin | dead
    discovery_false_positives: list[bool]
    skipped_reasons: list[str | None]  # why page was skipped (quality gate)


class MatchedBatch(msgspec.Struct, frozen=True, gc=False):
    """Structure of Arrays after pattern matching stage.

    index-aligned with ScoredBatch.
    """

    urls: list[str]
    matched_pattern_counts: list[int]  # how many patterns matched
    matched_pattern_labels: list[list[str]]  # labels of matched patterns per URL
    match_errors: list[str | None]  # None = success


class FindingBatch(msgspec.Struct, frozen=True, gc=False):
    """Structure of Arrays for CanonicalFinding-ready batch.

    This is the output of the build stage and input to FindingPipeline.
    Converted to individual CanonicalFinding objects by the store stage.
    """

    finding_ids: list[str]
    urls: list[str]
    titles: list[str]
    snippets: list[str]
    query_contexts: list[str]
    timestamps: list[float]
    confidences: list[float]
    source_types: list[str]  # live_public_pipeline | rss_atom_pipeline | ...
    payloads: list[bytes]  # zero-copy bytes for payload_text
    raw_payloads: list[bytes]  # zero-copy bytes for raw_payload_text
    matched_pattern_labels: list[list[str]]


class FeedEntryBatch(msgspec.Struct, frozen=True, gc=False):
    """Structure of Arrays for feed pipeline entry batch.

    index-aligned arrays for RSS/Atom feed processing.
    """

    entry_urls: list[str]
    entry_titles: list[str]
    entry_summaries: list[str]
    entry_published_dates: list[str | None]
    feed_url: str  # scalar — same for all entries in batch
    entry_hashes: list[str]  # for dedup


class FeedAssembledBatch(msgspec.Struct, frozen=True, gc=False):
    """Structure of Arrays after feed text assembly stage."""

    entry_urls: list[str]
    assembled_texts: list[str]  # clean text for pattern scan
    assembled_text_lens: list[int]
    quality_signals: list[dict]  # EntryQualitySignal as dict (msgspec compatible)


class FeedMatchedBatch(msgspec.Struct, frozen=True, gc=False):
    """Structure of Arrays after feed pattern scan stage."""

    entry_urls: list[str]
    matched_pattern_counts: list[int]
    matched_pattern_labels: list[list[str]]
    entry_dedup_hits: list[bool]  # True if entry URL was dupe in this run
    errors: list[str | None]
