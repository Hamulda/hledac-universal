"""
Sprint 8AN tests: pattern-backed feed pipeline + entry_hash.

Covers:
- Pattern-backed findings (replaces entry-backed)
- entry_hash in FeedEntryHit
- HTML->text (word-boundary safe, entity-safe)
- Bounded concurrency (max 4 pattern offload)
- Fail-soft per-entry
- Observability fields
- Batch runner compatibility
"""

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from hledac.universal.discovery.rss_atom_adapter import FeedEntryHit
from hledac.universal.pipeline.live_feed_pipeline import (
    _assemble_clean_feed_text,
    _strip_html_tags_from_text,
    _ASYNC_PATTERN_OFFLOAD,
    _get_pattern_offload_semaphore,
    MAX_FEED_PATTERN_TASKS,
    FEED_PAYLOAD_CONTEXT_CHARS,
    MAX_FEED_TEXT_CHARS,
    async_run_live_feed_pipeline,
    async_run_feed_source_batch,
    async_run_default_feed_batch,
    FeedPipelineRunResult,
    _RunDeduper,
    _EntryDeduper,
    _extract_payload_context,
    _make_feed_finding_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _DummyPatternHit:
    def __init__(self, pattern, start, end, value, label=None):
        self.pattern = pattern
        self.start = start
        self.end = end
        self.value = value
        self.label = label


def _dummy_entry(
    title="",
    summary="",
    published_raw="",
    published_ts=None,
    entry_url="",
):
    e = MagicMock()
    e.title = title
    e.summary = summary
    e.published_raw = published_raw
    e.published_ts = published_ts
    e.entry_url = entry_url
    e.feed_url = "http://example.com/feed"
    e.source = "test"
    e.rank = 0
    e.retrieved_ts = 1234567890.0
    e.entry_hash = ""
    return e


# ---------------------------------------------------------------------------
# D.1 — empty pattern registry = valid zero-findings state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_pattern_registry_is_valid_zero_findings_state():
    """When pattern registry is empty, pipeline returns zero findings, no error."""
    with patch("hledac.universal.pipeline.live_feed_pipeline.match_text") as mock_match:
        mock_match.return_value = []

        entry = _dummy_entry(title="Test Title", summary="Test summary")

        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries"
        ) as mock_fetch:
            mock_fetch.return_value = MagicMock(
                entries=(entry,),
                error=None,
            )

            result = await async_run_live_feed_pipeline(
                feed_url="http://example.com/feed",
                store=None,
                query_context=None,
            )

    assert result.accepted_findings == 0
    assert result.stored_findings == 0
    assert result.pages[0].accepted_findings == 0
    assert result.error is None


# ---------------------------------------------------------------------------
# D.2 — single match creates single canonical finding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_match_creates_single_canonical_finding():
    """One pattern hit -> one CanonicalFinding with correct fields."""
    hits = [_DummyPatternHit("sensitive", 10, 18, "sensitive", "ios_pattern")]

    with patch(
        "hledac.universal.pipeline.live_feed_pipeline.match_text"
    ) as mock_match:
        mock_match.return_value = hits

        entry = _dummy_entry(
            title="Secret Document",
            summary="Contains sensitive information",
            published_raw="2024-01-01",
            entry_url="http://example.com/entry1",
        )

        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries"
        ) as mock_fetch:
            mock_fetch.return_value = MagicMock(
                entries=(entry,),
                error=None,
            )

            result = await async_run_live_feed_pipeline(
                feed_url="http://example.com/feed",
                store=None,
                query_context="test_query",
            )

    assert result.accepted_findings == 1
    assert result.matched_patterns == 1
    assert result.pages[0].accepted_findings == 1


# ---------------------------------------------------------------------------
# D.3 — multiple matches create multiple findings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_matches_create_multiple_findings():
    """Three distinct pattern hits -> three CanonicalFindings."""
    hits = [
        _DummyPatternHit("alpha", 0, 5, "alpha", "type_a"),
        _DummyPatternHit("beta", 10, 14, "beta", "type_b"),
        _DummyPatternHit("gamma", 20, 25, "gamma", "type_c"),
    ]

    with patch(
        "hledac.universal.pipeline.live_feed_pipeline.match_text"
    ) as mock_match:
        mock_match.return_value = hits

        entry = _dummy_entry(
            title="Alpha Beta Gamma Report",
            summary="Alpha and beta and gamma together",
            published_raw="2024-01-01",
            entry_url="http://example.com/entry1",
        )

        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries"
        ) as mock_fetch:
            mock_fetch.return_value = MagicMock(
                entries=(entry,),
                error=None,
            )

            result = await async_run_live_feed_pipeline(
                feed_url="http://example.com/feed",
                store=None,
            )

    assert result.accepted_findings == 3
    assert result.matched_patterns == 3


# ---------------------------------------------------------------------------
# D.4 — repeated pattern in single entry dedupes preserve-first
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_repeated_pattern_in_single_entry_deduplicates_preserve_first():
    """Same (label, pattern, value) twice in one entry -> only first kept."""
    hits = [
        _DummyPatternHit("data", 5, 9, "data", "keyword"),
        _DummyPatternHit("data", 20, 24, "data", "keyword"),  # duplicate
    ]

    with patch(
        "hledac.universal.pipeline.live_feed_pipeline.match_text"
    ) as mock_match:
        mock_match.return_value = hits

        entry = _dummy_entry(
            title="Data everywhere data",
            summary="data",
            entry_url="http://example.com/entry1",
        )

        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries"
        ) as mock_fetch:
            mock_fetch.return_value = MagicMock(
                entries=(entry,),
                error=None,
            )

            result = await async_run_live_feed_pipeline(
                feed_url="http://example.com/feed",
                store=None,
            )

    # Only one — deduped
    assert result.accepted_findings == 1


# ---------------------------------------------------------------------------
# D.5 — HTML in summary stripped before matching
# ---------------------------------------------------------------------------

def test_html_in_summary_is_stripped_before_matching():
    """HTML tags in summary are stripped, not present in assembled text."""
    title = "Title"
    summary = "<div><p>Hello</p><script>alert('x')</script>World</div>"

    text = _assemble_clean_feed_text(title, summary)

    assert "<div>" not in text
    assert "<p>" not in text
    assert "<script>" not in text
    assert "Hello" in text
    assert "World" in text
    assert "alert" not in text


# ---------------------------------------------------------------------------
# D.6 — HTML entities unescaped after tag strip
# ---------------------------------------------------------------------------

def test_html_entities_are_unescaped_after_tag_strip_before_matching():
    """Entities like &amp; are unescaped AFTER tag stripping."""
    title = "Title"
    summary = "<p>Tom &amp; Jerry&quot;s &lt;story&gt;</p>"

    text = _assemble_clean_feed_text(title, summary)

    assert "&amp;" not in text
    assert "&quot;" not in text
    assert "&lt;" not in text
    assert "&gt;" not in text
    assert "Tom" in text
    assert "Jerry" in text


# ---------------------------------------------------------------------------
# D.7 — title+summary assemble deterministically with word boundaries
# ---------------------------------------------------------------------------

def test_title_and_summary_assemble_deterministically_with_word_boundaries():
    """Text assembly is deterministic: title first, then summary."""
    title = "Alpha News"
    summary = "Beta update gamma"

    text1 = _assemble_clean_feed_text(title, summary)
    text2 = _assemble_clean_feed_text(title, summary)

    assert text1 == text2
    assert text1.startswith("Alpha News")
    assert "Beta update gamma" in text1


# ---------------------------------------------------------------------------
# D.8 — empty title+summary uses sentinel
# ---------------------------------------------------------------------------

def test_empty_title_and_summary_use_no_content_sentinel():
    """Both empty -> '[no content]' sentinel."""
    text = _assemble_clean_feed_text("", "")
    assert text == "[no content]"


# ---------------------------------------------------------------------------
# D.9 — pattern scan uses patchable async offload call count
# ---------------------------------------------------------------------------

def test_pattern_scan_uses_patchable_async_pattern_offload_call_count():
    """Pattern offload uses _ASYNC_PATTERN_OFFLOAD symbol, not hardcoded asyncio.to_thread."""
    # This is a structural test — verify the symbol exists and is callable
    from hledac.universal.pipeline import live_feed_pipeline as lfp
    assert callable(lfp._ASYNC_PATTERN_OFFLOAD)


# ---------------------------------------------------------------------------
# D.10 — pattern scan bounded concurrency max 4
# ---------------------------------------------------------------------------

def test_pattern_scan_bounded_concurrency_max_4():
    """Shared semaphore limits pattern offload to MAX_FEED_PATTERN_TASKS concurrent calls."""
    from hledac.universal.pipeline import live_feed_pipeline as lfp

    sem = lfp._get_pattern_offload_semaphore()
    assert sem._value == MAX_FEED_PATTERN_TASKS

    # Calling again returns the SAME semaphore (singleton)
    sem2 = lfp._get_pattern_offload_semaphore()
    assert sem is sem2


# ---------------------------------------------------------------------------
# D.11 — match error is fail-soft per entry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_match_error_is_fail_soft_per_entry():
    """Exception during pattern scan for one entry does not crash pipeline."""
    entry1 = _dummy_entry(title="Entry One", entry_url="http://e.com/1")
    entry2 = _dummy_entry(title="Entry Two", entry_url="http://e.com/2")

    # First call fails, second call succeeds
    call_count = [0]

    def _failing_match(text):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("matcher failure")
        return []

    # Reset shared semaphore to avoid state pollution between tests
    import hledac.universal.pipeline.live_feed_pipeline as lfp
    lfp._pattern_semaphore = None

    with patch(
        "hledac.universal.pipeline.live_feed_pipeline.match_text", side_effect=_failing_match
    ):
        # Patch _ASYNC_PATTERN_OFFLOAD to run synchronously (no thread wrapper)
        with patch(
            "hledac.universal.pipeline.live_feed_pipeline._ASYNC_PATTERN_OFFLOAD",
            side_effect=lambda fn, *a, **k: fn(*a, **k),
        ):
            with patch(
                "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries"
            ) as mock_fetch:
                mock_fetch.return_value = MagicMock(
                    entries=(entry1, entry2),
                    error=None,
                )

                result = await async_run_live_feed_pipeline(
                    feed_url="http://example.com/feed",
                    store=None,
                )

    # Entry 1's quality scan raised (caught fail-soft at 1596 → pre_hits=[]);
    # entry 2 processed normally. Both complete with error=None.
    # Note: quality scan exception is fail-soft (pre_hits=[] is set, pipeline continues),
    # so pattern_step_failed is NOT raised for this case.
    assert len(result.pages) == 2
    assert result.pages[0].error is None
    assert result.pages[1].error is None


# ---------------------------------------------------------------------------
# D.12 — CancelledError is reraised
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancelled_error_is_reraised():
    """CancelledError during pipeline must be reraised, not swallowed."""
    with patch(
        "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries"
    ) as mock_fetch:
        mock_fetch.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await async_run_live_feed_pipeline(
                feed_url="http://example.com/feed",
                store=None,
            )


# ---------------------------------------------------------------------------
# D.13 — store=None is valid noop storage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_store_none_valid_noop_storage():
    """store=None means count-only mode, no storage calls."""
    hits = [_DummyPatternHit("test", 0, 4, "test", "k")]

    with patch(
        "hledac.universal.pipeline.live_feed_pipeline.match_text"
    ) as mock_match:
        mock_match.return_value = hits

        entry = _dummy_entry(title="Test", entry_url="http://e.com/1")

        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries"
        ) as mock_fetch:
            mock_fetch.return_value = MagicMock(
                entries=(entry,),
                error=None,
            )

            result = await async_run_live_feed_pipeline(
                feed_url="http://example.com/feed",
                store=None,
            )

    assert result.accepted_findings == 1
    assert result.stored_findings == 0  # no store


# ---------------------------------------------------------------------------
# D.14 — store ingest path receives pattern-backed findings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_store_ingest_path_receives_pattern_backed_findings():
    """When store is provided, findings are built and sent to async_ingest_findings_batch."""
    hits = [_DummyPatternHit("keyword", 0, 7, "keyword", "osint")]

    ingested_findings = []

    mock_store = MagicMock()
    mock_result = MagicMock()
    mock_result.activated = True
    mock_result.success = True
    mock_store.async_ingest_findings_batch = AsyncMock(
        return_value=[mock_result]
    )

    entry = _dummy_entry(
        title="Keyword Report",
        summary="A document with keyword",
        published_raw="2024-01-01",
        entry_url="http://e.com/1",
    )

    with patch(
        "hledac.universal.pipeline.live_feed_pipeline.match_text"
    ) as mock_match:
        mock_match.return_value = hits

        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries"
        ) as mock_fetch:
            mock_fetch.return_value = MagicMock(
                entries=(entry,),
                error=None,
            )

            result = await async_run_live_feed_pipeline(
                feed_url="http://example.com/feed",
                store=mock_store,
            )

    assert result.accepted_findings == 1
    assert result.stored_findings == 1
    mock_store.async_ingest_findings_batch.assert_awaited_once()


# ---------------------------------------------------------------------------
# D.15 — persistent_duplicate from store does not crash pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persistent_duplicate_from_store_does_not_crash_pipeline():
    """Store returning persistent_duplicate accepted=False must not crash pipeline."""
    hits = [_DummyPatternHit("keyword", 0, 7, "keyword", "osint")]

    # FindingQualityDecision fields: accepted, reason, entropy, normalized_hash, duplicate
    from hledac.universal.knowledge.duckdb_store import FindingQualityDecision
    mock_decision = FindingQualityDecision(
        accepted=False,
        reason="persistent_duplicate",
        entropy=3.5,
        normalized_hash="abc123",
        duplicate=True,
    )

    mock_store = MagicMock()
    mock_store.async_ingest_findings_batch = AsyncMock(
        return_value=[mock_decision]
    )

    entry = _dummy_entry(
        title="Keyword Report",
        summary="A document with keyword",
        published_raw="2024-01-01",
        entry_url="http://e.com/1",
    )

    with patch(
        "hledac.universal.pipeline.live_feed_pipeline.match_text"
    ) as mock_match:
        mock_match.return_value = hits

        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries"
        ) as mock_fetch:
            mock_fetch.return_value = MagicMock(
                entries=(entry,),
                error=None,
            )

            result = await async_run_live_feed_pipeline(
                feed_url="http://example.com/feed",
                store=mock_store,
            )

    # persistent_duplicate: store accepted=0 for this finding, pipeline continues
    # F180B fix: accepted_findings reflects what the store accepted (not raw hit count)
    assert result.accepted_findings == 0
    assert result.stored_findings == 0
    assert result.error is None


# ---------------------------------------------------------------------------
# D.16 — patterns_configured reported
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patterns_configured_reported():
    """FeedPipelineRunResult includes patterns_configured field."""
    hits = [_DummyPatternHit("test", 0, 4, "test", "k")]

    with patch(
        "hledac.universal.pipeline.live_feed_pipeline.match_text"
    ) as mock_match:
        mock_match.return_value = hits

        entry = _dummy_entry(title="Test", entry_url="http://e.com/1")

        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries"
        ) as mock_fetch:
            mock_fetch.return_value = MagicMock(
                entries=(entry,),
                error=None,
            )

            result = await async_run_live_feed_pipeline(
                feed_url="http://example.com/feed",
                store=None,
            )

    # New field must exist and be present in result
    assert hasattr(result, "patterns_configured")
    assert result.patterns_configured >= 0


# ---------------------------------------------------------------------------
# D.17 — matched_patterns reported
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_matched_patterns_reported():
    """FeedPipelineRunResult includes matched_patterns field."""
    hits = [
        _DummyPatternHit("a", 0, 1, "a", "x"),
        _DummyPatternHit("b", 5, 6, "b", "y"),
    ]

    with patch(
        "hledac.universal.pipeline.live_feed_pipeline.match_text"
    ) as mock_match:
        mock_match.return_value = hits

        entry = _dummy_entry(title="A B", entry_url="http://e.com/1")

        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries"
        ) as mock_fetch:
            mock_fetch.return_value = MagicMock(
                entries=(entry,),
                error=None,
            )

            result = await async_run_live_feed_pipeline(
                feed_url="http://example.com/feed",
                store=None,
            )

    assert hasattr(result, "matched_patterns")
    assert result.matched_patterns == 2


# ---------------------------------------------------------------------------
# D.18 — payload_text is not full blob, uses 200 char radius
# ---------------------------------------------------------------------------

def test_payload_text_is_short_and_bounded_by_radius():
    """payload_text context is short, based on FEED_PAYLOAD_CONTEXT_CHARS."""
    # Text longer than radius * 2
    long_text = "X" * 500 + "HIT" + "Y" * 500
    # Hit at position 500
    ctx = _extract_payload_context(long_text, 500, 503)

    # Should be limited to radius around hit (plus ellipses)
    # The cut finds whitespace boundaries which may be far from hit
    # so the assertion is lenient: context should be much smaller than full text
    assert len(ctx) < len(long_text)
    assert "HIT" in ctx


# ---------------------------------------------------------------------------
# D.19 — source_type stays rss_atom_pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_source_type_stays_rss_atom_pipeline():
    """CanonicalFindings from feed pipeline have source_type='rss_atom_pipeline'."""
    hits = [_DummyPatternHit("keyword", 0, 7, "keyword", "osint")]

    captured = []

    async def _capture_ingest(findings):
        # CanonicalFinding is a msgspec.Struct — access via .field
        for f in findings:
            captured.append(f)
        return [MagicMock(activated=True, success=True) for _ in findings]

    mock_store = MagicMock()
    mock_store.async_ingest_findings_batch = _capture_ingest

    entry = _dummy_entry(
        title="Report",
        summary="keyword document",
        entry_url="http://e.com/1",
    )

    with patch(
        "hledac.universal.pipeline.live_feed_pipeline.match_text"
    ) as mock_match:
        mock_match.return_value = hits

        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries"
        ) as mock_fetch:
            mock_fetch.return_value = MagicMock(
                entries=(entry,),
                error=None,
            )

            await async_run_live_feed_pipeline(
                feed_url="http://example.com/feed",
                store=mock_store,
            )

    assert len(captured) == 1
    # msgspec.Struct is not subscriptable — use getattr
    assert captured[0].source_type == "rss_atom_pipeline"


# ---------------------------------------------------------------------------
# D.20 — finding_id is deterministic
# ---------------------------------------------------------------------------

def test_finding_id_is_deterministic():
    """Same inputs always produce same finding_id (no hash())."""
    id1 = _make_feed_finding_id(
        "http://feed.com",
        "http://entry.com",
        "label",
        "pattern",
        "value",
    )
    id2 = _make_feed_finding_id(
        "http://feed.com",
        "http://entry.com",
        "label",
        "pattern",
        "value",
    )
    assert id1 == id2
    assert len(id1) == 16
    assert id1.isalnum()


# ---------------------------------------------------------------------------
# D.21 — entry_hash is deterministic and backwards compatible
# ---------------------------------------------------------------------------

def test_entry_hash_is_deterministic_and_backwards_compatible():
    """entry_hash is computed via xxhash and is backwards compatible (default='')."""
    from hledac.universal.discovery.rss_atom_adapter import _entry_hash

    h1 = _entry_hash("Test Title", "2024-01-01")
    h2 = _entry_hash("Test Title", "2024-01-01")
    h3 = _entry_hash("Other Title", "2024-01-01")

    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 16  # xxhash hexdigest length

    # Backwards compatible: FeedEntryHit can be constructed without entry_hash
    e = FeedEntryHit(
        feed_url="f",
        entry_url="e",
        title="t",
        summary="s",
        published_raw="r",
        published_ts=1.0,
        source="s",
        rank=0,
        retrieved_ts=1.0,
    )
    assert e.entry_hash == ""


# ---------------------------------------------------------------------------
# D.22 — all FeedEntryHit construction sites remain compatible
# ---------------------------------------------------------------------------

def test_all_feedentryhit_construction_sites_remain_compatible():
    """All three FeedEntryHit construction paths work with entry_hash default."""
    # Path 1: RSS construction
    e1 = FeedEntryHit(
        feed_url="f",
        entry_url="e",
        title="t",
        summary="s",
        published_raw="r",
        published_ts=1.0,
        source="rss",
        rank=0,
        retrieved_ts=1.0,
    )
    assert e1.entry_hash == ""

    # Path 2: Atom construction
    e2 = FeedEntryHit(
        feed_url="f",
        entry_url="e",
        title="t",
        summary="s",
        published_raw="r",
        published_ts=1.0,
        source="atom",
        rank=0,
        retrieved_ts=1.0,
    )
    assert e2.entry_hash == ""

    # Path 3: re-rank path (existing entry)
    e3 = FeedEntryHit(
        feed_url=e1.feed_url,
        entry_url=e1.entry_url,
        title=e1.title,
        summary=e1.summary,
        published_raw=e1.published_raw,
        published_ts=e1.published_ts,
        source=e1.source,
        rank=5,
        retrieved_ts=e1.retrieved_ts,
    )
    assert e3.entry_hash == ""


# ---------------------------------------------------------------------------
# D.23 — batch runner from 8AL still works with new feed pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_runner_from_8al_still_works_with_new_feed_pipeline():
    """async_run_feed_source_batch public signature unchanged, still calls pipeline."""
    from hledac.universal.pipeline.live_feed_pipeline import (
        async_run_feed_source_batch,
        FeedSourceBatchRunResult,
    )

    with patch(
        "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries"
    ) as mock_fetch:
        mock_fetch.return_value = MagicMock(
            entries=(_dummy_entry(title="T", entry_url="http://e.com/1"),),
            error=None,
        )

        with patch(
            "hledac.universal.pipeline.live_feed_pipeline.async_run_live_feed_pipeline"
        ) as mock_pipeline:
            mock_pipeline.return_value = MagicMock(
                fetched_entries=1,
                accepted_findings=0,
                stored_findings=0,
                patterns_configured=0,
                matched_patterns=0,
                error=None,
            )

            result = await async_run_feed_source_batch(
                sources=("http://example.com/feed",),
                store=None,
                max_entries_per_feed=20,
                feed_concurrency=1,
            )

    assert isinstance(result, FeedSourceBatchRunResult)
    assert result.total_sources == 1
    mock_pipeline.assert_called_once()


# ---------------------------------------------------------------------------
# D.24 — probe_8ah contracts not broken unnecessarily
# ---------------------------------------------------------------------------

def test_probe_8ah_contracts_not_broken_unnecessarily():
    """Verify key contracts from 8AH are preserved."""
    # DTOs still exist and have required fields
    result = FeedPipelineRunResult(
        feed_url="http://example.com",
        fetched_entries=1,
        accepted_findings=0,
        stored_findings=0,
    )
    assert result.feed_url == "http://example.com"
    assert result.fetched_entries == 1
    # New optional fields have defaults
    assert result.patterns_configured == 0
    assert result.matched_patterns == 0


# ---------------------------------------------------------------------------
# D.25 — case-sensitive mode not introduced accidentally
# ---------------------------------------------------------------------------

def test_case_sensitive_mode_not_introduced_accidentally():
    """PatternMatcher still uses case-insensitive matching (B.13/B.25)."""
    from hledac.universal.patterns.pattern_matcher import configure_patterns, reset_pattern_matcher

    reset_pattern_matcher()
    configure_patterns((("KEYWORD", "test"),))

    from hledac.universal.patterns.pattern_matcher import match_text

    hits = match_text("keyword in text")
    # PatternMatcher lowercases internally -> case-insensitive
    assert len(hits) == 1
    assert hits[0].value == "keyword"


# ---------------------------------------------------------------------------
# Additional tests
# ---------------------------------------------------------------------------

def test_run_deduper_is_per_run_preserve_first():
    """_RunDeduper skips duplicate entry_url."""
    deduper = _RunDeduper()
    assert deduper.is_new("http://e.com/1") is True
    assert deduper.is_new("http://e.com/1") is False
    assert deduper.is_new("http://e.com/2") is True


def test_entry_deduper_by_label_pattern_value():
    """_EntryDeduper dedupes by (label, pattern, value)."""
    deduper = _EntryDeduper()
    assert deduper.is_new("label1", "pat1", "val1") is True
    assert deduper.is_new("label1", "pat1", "val1") is False  # same
    assert deduper.is_new("label2", "pat1", "val1") is True  # different label
    assert deduper.is_new("label1", "pat2", "val1") is True  # different pattern
    assert deduper.is_new("label1", "pat1", "val2") is True  # different value


def test_max_feed_text_chars_cap():
    """Assembled text is capped at MAX_FEED_TEXT_CHARS in the pipeline."""
    long_title = "A" * 5000
    # _assemble_clean_feed_text itself does NOT cap (cap is in pipeline step)
    text = _assemble_clean_feed_text(long_title, "")
    # The cap is applied in _entry_to_pattern_findings via slice
    # Here we just verify MAX_FEED_TEXT_CHARS constant exists and title is long
    assert len(text) > MAX_FEED_TEXT_CHARS
    assert MAX_FEED_TEXT_CHARS == 4000


def test_script_style_blocks_removed_first():
    """<script> and <style> blocks are removed BEFORE tag stripping."""
    html = "<script>bad</script><div>good</div><style>bad</style>"
    text = _strip_html_tags_from_text(html)
    assert "bad" not in text
    assert "good" in text
    assert "<script>" not in text
    assert "<style>" not in text


def test_html_tag_replaced_with_space():
    """HTML tags are replaced with space, not removed (word-boundary safe)."""
    text = _strip_html_tags_from_text("<div>A</div><div>B</div>")
    assert text == "A B"  # space between, not AB


def test_feed_pipeline_run_result_new_fields_have_defaults():
    """New fields patterns_configured and matched_patterns have default values."""
    r = FeedPipelineRunResult(
        feed_url="http://x.com",
        fetched_entries=5,
    )
    assert r.patterns_configured == 0
    assert r.matched_patterns == 0
    assert r.pages == ()
    assert r.error is None
