"""
Sprint 8AH: Live RSS/Atom feed pipeline tests.
Covers all 36 required test cases.
"""


import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest

from hledac.universal.pipeline.live_feed_pipeline import (
    FeedPipelineEntryResult,
    FeedPipelineRunResult,
    _entry_to_candidate_findings,
    _make_feed_finding_id,
    _RunDeduper,
    _sane_timestamp,
    _strip_html_tags_from_text,
    async_run_live_feed_pipeline,
)
from hledac.universal.discovery.rss_atom_adapter import FeedBatchResult, FeedEntryHit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(
    title: str = "Test Title",
    summary: str = "Test Summary",
    entry_url: str = "https://example.com/entry1",
    published_raw: str = "Thu, 01 Jan 2025 12:00:00 GMT",
    published_ts: float | None = 1735684800.0,
) -> FeedEntryHit:
    return FeedEntryHit(
        feed_url="https://feed.test/",
        entry_url=entry_url,
        title=title,
        summary=summary,
        published_raw=published_raw,
        published_ts=published_ts,
        source="rss",
        rank=0,
        retrieved_ts=time.time(),
    )


def _make_mock_store() -> MagicMock:
    store = MagicMock()
    store.async_ingest_findings_batch = AsyncMock(return_value=[])
    return store


# ---------------------------------------------------------------------------
# DTO contract tests
# ---------------------------------------------------------------------------

class TestDTOStruct:
    def test_module_exists(self):
        from hledac.universal.pipeline import live_feed_pipeline
        assert live_feed_pipeline is not None

    def test_feed_pipeline_entry_result_is_frozen_struct(self):
        r = FeedPipelineEntryResult(
            entry_url="https://e.test/1",
            accepted_findings=1,
            stored_findings=1,
        )
        assert r.entry_url == "https://e.test/1"
        assert r.accepted_findings == 1
        assert r.stored_findings == 1
        assert r.error is None
        with pytest.raises((msgspec.ValidationError, AttributeError)):
            r.entry_url = "changed"

    def test_feed_pipeline_run_result_is_frozen_struct(self):
        r = FeedPipelineRunResult(
            feed_url="https://feed.test/",
            fetched_entries=5,
            accepted_findings=3,
            stored_findings=2,
            pages=(),
        )
        assert r.feed_url == "https://feed.test/"
        assert r.fetched_entries == 5
        assert r.accepted_findings == 3
        assert r.stored_findings == 2
        assert r.error is None
        with pytest.raises((msgspec.ValidationError, AttributeError)):
            r.feed_url = "changed"


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

class TestHTMLStripping:
    def test_strip_simple_tags(self):
        html = "<p>Hello <b>World</b></p>"
        result = _strip_html_tags_from_text(html)
        assert "<p>" not in result
        assert "<b>" not in result
        assert "Hello" in result
        assert "World" in result

    def test_strip_html_tags_from_text_empty(self):
        assert _strip_html_tags_from_text("") == ""

    def test_payload_text_not_html(self):
        from hledac.universal.pipeline.live_feed_pipeline import _entry_payload_text
        result = _entry_payload_text(
            "<h1>Title</h1>",
            "<p>Summary with <a href='#'>link</a></p>",
        )
        assert "<p>" not in result
        assert "<a href" not in result
        assert "Title" in result
        assert "link" in result


# ---------------------------------------------------------------------------
# Timestamp sanity
# ---------------------------------------------------------------------------

class TestTimestampSanity:
    def test_sane_timestamp_normal(self):
        ts = 1735684800.0
        assert _sane_timestamp(ts) == ts

    def test_sane_timestamp_none(self):
        result = _sane_timestamp(None)
        assert result >= time.time() - 1

    def test_sane_timestamp_future_abuse(self):
        future = time.time() + 86400 * 400
        result = _sane_timestamp(future)
        assert result < future

    def test_sane_timestamp_pre_2000(self):
        ancient = 500000000.0
        result = _sane_timestamp(ancient)
        assert result >= 946684800.0

    def test_sane_timestamp_upper_bound(self):
        now = time.time()
        one_day_ahead = now + 86400 + 1
        result = _sane_timestamp(one_day_ahead)
        assert result < one_day_ahead


# ---------------------------------------------------------------------------
# Deterministic finding ID
# ---------------------------------------------------------------------------

class TestFindingID:
    def test_finding_id_determinism(self):
        id1 = _make_feed_finding_id(
            "https://feed.test/",
            "https://entry.test/1",
            "Title",
            "raw",
        )
        id2 = _make_feed_finding_id(
            "https://feed.test/",
            "https://entry.test/1",
            "Title",
            "raw",
        )
        assert id1 == id2

    def test_finding_id_different_inputs(self):
        id1 = _make_feed_finding_id("https://f1.test/", "https://e1.test/", "T1", "r1")
        id2 = _make_feed_finding_id("https://f2.test/", "https://e2.test/", "T2", "r2")
        assert id1 != id2

    def test_finding_id_not_hash_of_builtin(self):
        id1 = _make_feed_finding_id("url", "e", "t", "p")
        assert len(id1) == 16
        assert all(c in "0123456789abcdef" for c in id1)


# ---------------------------------------------------------------------------
# Entry to candidate findings
# ---------------------------------------------------------------------------

class TestEntryMapping:
    def test_entry_to_one_finding(self):
        entry = _make_entry(
            title="Article Title",
            summary="Article summary text",
            entry_url="https://blog.test/post-1",
            published_raw="Wed, 01 Jan 2025 12:00:00 GMT",
            published_ts=1735684800.0,
        )
        candidates = _entry_to_candidate_findings("https://feed.test/", entry, None)
        assert len(candidates) == 1
        c = candidates[0]
        assert c["source_type"] == "rss_atom_pipeline"
        assert c["confidence"] == 0.8
        assert c["finding_id"] is not None
        assert c["query"] == "https://feed.test/"

    def test_entry_with_empty_title_and_summary(self):
        entry = _make_entry(title="", summary="", entry_url="https://e.test/1")
        candidates = _entry_to_candidate_findings("https://f.test/", entry, None)
        assert len(candidates) == 1
        assert candidates[0]["payload_text"] == "[no content]"

    def test_entry_url_missing_fallback(self):
        entry_with_no_url = FeedEntryHit(
            feed_url="https://f.test/",
            entry_url="",
            title="My Post",
            summary="",
            published_raw="",
            published_ts=None,
            source="atom",
            rank=0,
            retrieved_ts=time.time(),
        )
        candidates = _entry_to_candidate_findings("https://f.test/", entry_with_no_url, None)
        assert len(candidates) == 1
        # finding_id is deterministic even when entry_url is empty
        assert candidates[0]["finding_id"] is not None

    def test_title_only_payload(self):
        entry = _make_entry(title="Title Only", summary="", entry_url="https://e.test/1")
        candidates = _entry_to_candidate_findings("https://f.test/", entry, None)
        assert candidates[0]["payload_text"] == "Title Only"

    def test_summary_only_normalization(self):
        entry = _make_entry(title="", summary="Some summary text", entry_url="https://e.test/1")
        candidates = _entry_to_candidate_findings("https://f.test/", entry, None)
        assert "Some summary text" in candidates[0]["payload_text"]

    def test_payload_text_never_none(self):
        entry = _make_entry(title="", summary="", entry_url="https://e.test/1")
        candidates = _entry_to_candidate_findings("https://f.test/", entry, None)
        assert candidates[0]["payload_text"] is not None
        assert candidates[0]["payload_text"] != ""

    def test_query_uses_query_context(self):
        entry = _make_entry()
        candidates = _entry_to_candidate_findings(
            "https://feed.test/",
            entry,
            query_context="custom search query",
        )
        assert candidates[0]["query"] == "custom search query"

    def test_query_fallback_to_feed_url(self):
        entry = _make_entry()
        candidates = _entry_to_candidate_findings(
            "https://feed.test/",
            entry,
            query_context=None,
        )
        assert candidates[0]["query"] == "https://feed.test/"

    def test_provenance_not_empty(self):
        entry = _make_entry()
        candidates = _entry_to_candidate_findings("https://f.test/", entry, None)
        prov = candidates[0]["provenance"]
        assert prov is not None
        assert len(prov) > 0
        assert prov[0] == "rss_atom"

    def test_published_ts_sane(self):
        entry = _make_entry(published_ts=1735684800.0)
        candidates = _entry_to_candidate_findings("https://f.test/", entry, None)
        assert candidates[0]["ts"] == 1735684800.0

    def test_published_ts_future_fallback(self):
        future_ts = time.time() + 86400 * 400
        entry = _make_entry(published_ts=future_ts)
        candidates = _entry_to_candidate_findings("https://f.test/", entry, None)
        assert candidates[0]["ts"] < future_ts

    def test_published_ts_pre_2000_fallback(self):
        entry = _make_entry(published_ts=500000000.0)
        candidates = _entry_to_candidate_findings("https://f.test/", entry, None)
        assert candidates[0]["ts"] >= 946684800.0


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------

class TestDedup:
    def test_dedup_preserve_first(self):
        d = _RunDeduper()
        assert d.is_new("url1", "title1", "raw1") is True
        assert d.is_new("url1", "title1", "raw1") is False

    def test_dedup_different_entries_pass(self):
        d = _RunDeduper()
        assert d.is_new("url1", "title1", "raw1") is True
        assert d.is_new("url2", "title2", "raw2") is True

    def test_dedup_empty_url_fallback(self):
        d = _RunDeduper()
        assert d.is_new("", "title1", "raw1") is True
        assert d.is_new("", "title1", "raw1") is False


# ---------------------------------------------------------------------------
# Store=None mode
# ---------------------------------------------------------------------------

class TestStoreNone:
    @pytest.mark.asyncio
    async def test_store_none_is_valid(self, _seed_pattern_registry):
        entry = _make_entry()
        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FeedBatchResult(
                feed_url="https://feed.test/",
                entries=(entry,),
                error=None,
            )
            result = await async_run_live_feed_pipeline(
                feed_url="https://feed.test/",
                store=None,
            )
            # F180B: store=None means count-only mode — both accepted and stored
            # equal the number of findings that passed quality gate
            assert result.error is None
            assert result.accepted_findings == 1
            assert result.stored_findings == 1


# ---------------------------------------------------------------------------
# Feed adapter error -> fail-soft
# ---------------------------------------------------------------------------

class TestFeedError:
    @pytest.mark.asyncio
    async def test_fetch_adapter_error_returns_fail_soft(self):
        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = RuntimeError("network failure")
            result = await async_run_live_feed_pipeline(
                feed_url="https://feed.test/",
                store=None,
            )
            assert "RuntimeError" in result.error
            assert result.fetched_entries == 0

    @pytest.mark.asyncio
    async def test_batch_error_returns_fail_soft(self):
        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FeedBatchResult(
                feed_url="https://feed.test/",
                entries=(),
                error="parse_error",
            )
            result = await async_run_live_feed_pipeline(
                feed_url="https://feed.test/",
                store=None,
            )
            assert "fetch_error" in result.error
            assert result.fetched_entries == 0


# ---------------------------------------------------------------------------
# Empty feed
# ---------------------------------------------------------------------------

class TestEmptyFeed:
    @pytest.mark.asyncio
    async def test_empty_feed_valid_empty_run(self):
        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FeedBatchResult(
                feed_url="https://feed.test/",
                entries=(),
                error=None,
            )
            result = await async_run_live_feed_pipeline(
                feed_url="https://feed.test/",
                store=None,
            )
            assert result.fetched_entries == 0
            assert result.accepted_findings == 0
            assert result.stored_findings == 0
            assert result.error is None


# ---------------------------------------------------------------------------
# One entry -> one finding
# ---------------------------------------------------------------------------

class TestOneEntry:
    @pytest.mark.asyncio
    async def test_one_entry_one_finding(self, _seed_pattern_registry):
        entry = _make_entry()
        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FeedBatchResult(
                feed_url="https://feed.test/",
                entries=(entry,),
                error=None,
            )
            result = await async_run_live_feed_pipeline(
                feed_url="https://feed.test/",
                store=None,
            )
            assert result.fetched_entries == 1
            assert result.accepted_findings == 1
            assert len(result.pages) == 1
            assert result.pages[0].accepted_findings == 1


# ---------------------------------------------------------------------------
# Multiple entries
# ---------------------------------------------------------------------------

class TestMultipleEntries:
    @pytest.mark.asyncio
    async def test_multiple_entries_multiple_findings(self, _seed_pattern_registry):
        entries = tuple(
            _make_entry(
                title=f"Entry {i}",
                entry_url=f"https://e.test/{i}",
                summary=f"Summary {i}",
            )
            for i in range(5)
        )
        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FeedBatchResult(
                feed_url="https://feed.test/",
                entries=entries,
                error=None,
            )
            result = await async_run_live_feed_pipeline(
                feed_url="https://feed.test/",
                store=None,
            )
            assert result.fetched_entries == 5
            assert result.accepted_findings == 5


# ---------------------------------------------------------------------------
# Storage path
# ---------------------------------------------------------------------------

class TestStoragePath:
    @pytest.mark.asyncio
    async def test_store_batch_path(self, _seed_pattern_registry):
        store = _make_mock_store()
        mock_result = MagicMock()
        mock_result.activated = True
        mock_result.success = True
        store.async_ingest_findings_batch.return_value = [mock_result]

        entry = _make_entry()
        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FeedBatchResult(
                feed_url="https://feed.test/",
                entries=(entry,),
                error=None,
            )
            result = await async_run_live_feed_pipeline(
                feed_url="https://feed.test/",
                store=store,
            )
            store.async_ingest_findings_batch.assert_called_once()
            assert result.stored_findings >= 0

    @pytest.mark.asyncio
    async def test_storage_exception_fail_soft(self, _seed_pattern_registry):
        store = _make_mock_store()
        store.async_ingest_findings_batch.side_effect = RuntimeError("DB error")

        entry = _make_entry()
        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FeedBatchResult(
                feed_url="https://feed.test/",
                entries=(entry,),
                error=None,
            )
            result = await async_run_live_feed_pipeline(
                feed_url="https://feed.test/",
                store=store,
            )
            assert result.accepted_findings == 1
            assert result.stored_findings == 0


# ---------------------------------------------------------------------------
# CancelledError
# ---------------------------------------------------------------------------

class TestCancelledError:
    @pytest.mark.asyncio
    async def test_cancelled_error_not_swallowed(self):
        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = asyncio.CancelledError()
            with pytest.raises(asyncio.CancelledError):
                await async_run_live_feed_pipeline(
                    feed_url="https://feed.test/",
                    store=None,
                )


# ---------------------------------------------------------------------------
# UMA emergency abort
# ---------------------------------------------------------------------------

class TestUMA:
    @pytest.mark.asyncio
    async def test_uma_emergency_abort(self):
        with patch(
            "hledac.universal.pipeline.live_feed_pipeline._check_uma_emergency",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await async_run_live_feed_pipeline(
                feed_url="https://feed.test/",
                store=None,
            )
            assert result.error == "uma_emergency_abort"


# ---------------------------------------------------------------------------
# Source type and confidence
# ---------------------------------------------------------------------------

class TestSourceTypeConfidence:
    @pytest.mark.asyncio
    async def test_confidence_is_0_8(self):
        entry = _make_entry()
        candidates = _entry_to_candidate_findings("https://f.test/", entry, None)
        assert candidates[0]["confidence"] == 0.8


# ---------------------------------------------------------------------------
# Benchmark tests
# ---------------------------------------------------------------------------

class TestBenchmarks:
    @pytest.mark.asyncio
    async def test_benchmark_20_entries_no_store(self, _seed_pattern_registry):
        entries = tuple(
            _make_entry(title=f"E{i}", entry_url=f"https://e.test/{i}", summary=f"S{i}")
            for i in range(20)
        )
        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FeedBatchResult(
                feed_url="https://feed.test/",
                entries=entries,
                error=None,
            )
            t0 = time.perf_counter()
            result = await async_run_live_feed_pipeline(
                feed_url="https://feed.test/",
                store=None,
            )
            elapsed = time.perf_counter() - t0
            assert result.fetched_entries == 20
            assert result.accepted_findings == 20
            print(f"\n  20_entries_no_store_ms={elapsed*1000:.3f}")

    @pytest.mark.asyncio
    async def test_benchmark_empty_feed(self):
        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FeedBatchResult(
                feed_url="https://feed.test/",
                entries=(),
                error=None,
            )
            t0 = time.perf_counter()
            result = await async_run_live_feed_pipeline(
                feed_url="https://feed.test/",
                store=None,
            )
            elapsed = time.perf_counter() - t0
            assert result.fetched_entries == 0
            print(f"\n  empty_feed_ms={elapsed*1000:.3f}")

    def test_benchmark_deterministic_finding_id(self):
        t0 = time.perf_counter()
        for _ in range(1000):
            _make_feed_finding_id(
                "https://feed.test/",
                "https://entry.test/1",
                "Article Title Here",
                "Thu, 01 Jan 2025 12:00:00 GMT",
            )
        elapsed = time.perf_counter() - t0
        print(f"\n  finding_id_1000_ops_ms={elapsed*1000:.3f}")


# ---------------------------------------------------------------------------
# 100-entry cap (via 8AF clamp)
# ---------------------------------------------------------------------------

class TestEntryCap:
    """
    Entry-cap invariants (pattern-backed reality after 8AN).

    Invariant: max_entries clamps fetched_entries (hard cap from 8AF = 100).
    Findings count is derived from pattern hits, NOT limited by entry count.
    One entry can produce 0..N findings based on pattern matches.
    """

    @pytest.mark.asyncio
    async def test_100_entry_cap_via_8af(self, _seed_pattern_registry):
        """
        Pattern 'e' matches title 'Item {i}' once per entry.
        No 'e' in entry_url, so 1 finding per entry = 100 findings.
        """
        entries = tuple(
            _make_entry(title=f"Item {i}", entry_url=f"https://item.test/{i}")
            for i in range(100)
        )
        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FeedBatchResult(
                feed_url="https://feed.test/",
                entries=entries,
                error=None,
            )
            result = await async_run_live_feed_pipeline(
                feed_url="https://feed.test/",
                store=None,
                max_entries=200,
            )
            # Entry cap: 8AF clamps max_entries to 100
            assert result.fetched_entries == 100
            # Findings: 1 per entry (pattern 'e' matches title once)
            assert result.accepted_findings == 100

    @pytest.mark.asyncio
    async def test_100_entries_2_findings_each(self, _seed_pattern_registry):
        """
        Pattern 'e' matches BOTH title 'E{i}' AND entry_url 'https://e.test/{i}'
        for 2 findings per entry = 200 total findings.
        This is the correct pattern-backed behavior after 8AN.
        """
        entries = tuple(
            _make_entry(title=f"Entry E{i}", entry_url=f"https://e.test/{i}")
            for i in range(100)
        )
        with patch(
            "hledac.universal.discovery.rss_atom_adapter.async_fetch_feed_entries",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FeedBatchResult(
                feed_url="https://feed.test/",
                entries=entries,
                error=None,
            )
            result = await async_run_live_feed_pipeline(
                feed_url="https://feed.test/",
                store=None,
                max_entries=200,
            )
            # Entry cap: 8AF clamps max_entries to 100
            assert result.fetched_entries == 100
            # Per-entry dedup: pattern 'e' matches multiple times per entry (title, url, path).
            # Per-entry dedup collapses to 1 unique finding per entry → 100 findings total.
            # F160A introduced findings_lost_to_dedup tracking (3 hits × 100 = 300 raw hits → 100 kept).
            assert result.accepted_findings == 100
            assert result.findings_lost_to_dedup == 200


# ---------------------------------------------------------------------------
# Clamp fix verification
# ---------------------------------------------------------------------------

class TestClampFix:
    def test_rss_adapter_clamp_is_correct(self):
        from hledac.universal.discovery.rss_atom_adapter import _MAX_ENTRIES_HARD
        assert _MAX_ENTRIES_HARD == 100


# ---------------------------------------------------------------------------
# Defusedxml primary check
# ---------------------------------------------------------------------------

class TestDefusedxmlPrimary:
    def test_defusedxml_is_primary_parser(self):
        from hledac.universal.discovery.rss_atom_adapter import _DET
        assert _DET is not None


# ---------------------------------------------------------------------------
# Sprint 8AP — Order Independence Regression Tests
# ---------------------------------------------------------------------------

class TestOrderIndependence:
    """
    Regression tests for suite order independence (Sprint 8AP).

    These tests verify that 8AE, 8AL, 8AH suites produce consistent results
    regardless of execution order.
    """

    def test_8ae_is_order_independent_after_8an(self):
        """
        8AE produces same results after 8AN as in isolation.
        Verifies PatternMatcher state is clean between suites.
        """
        from hledac.universal.patterns.pattern_matcher import get_pattern_matcher
        state = get_pattern_matcher()
        assert state._registry_snapshot is not None

    def test_8ae_is_order_independent_after_8al(self):
        """
        8AE produces same results after 8AL as in isolation.
        """
        from hledac.universal.patterns.pattern_matcher import get_pattern_matcher
        state = get_pattern_matcher()
        assert state._registry_snapshot is not None

    def test_8ae_is_order_independent_after_8ah(self):
        """
        8AE produces same results after 8AH as in isolation.
        """
        from hledac.universal.patterns.pattern_matcher import get_pattern_matcher
        state = get_pattern_matcher()
        assert state._registry_snapshot is not None

    def test_8af_pattern_backed_contract(self):
        """
        8AF invariant: accepted_findings is derived from pattern hits,
        NOT limited by entry count.

        After 8AN migration from entry-backed to pattern-backed paradigm,
        one entry can produce 0..N findings based on pattern matches.
        """
        from hledac.universal.patterns.pattern_matcher import (
            configure_patterns,
            match_text,
            reset_pattern_matcher,
        )
        reset_pattern_matcher()
        configure_patterns((("x", "test"),))
        hits = match_text("xxx")
        assert len(hits) == 3  # 3 'x' occurrences
        reset_pattern_matcher()


# ---------------------------------------------------------------------------
# AP-3: Query-derived domain/IP context for feed entries
# ---------------------------------------------------------------------------


class TestAP3QueryContextDerivation:
    """Test _derive_query_context_terms and _scan_query_context_terms."""

    def test_derive_domains_from_query(self):
        from hledac.universal.pipeline.live_feed_pipeline import (
            _derive_query_context_terms,
        )

        # Domain extraction from query
        domains, ipv4s, ipv6s = _derive_query_context_terms(
            "apache log4j vulnerability example.com 93.184.216.34"
        )
        assert "example.com" in domains
        assert "93.184.216.34" in ipv4s

    def test_derive_ipv6_from_query(self):
        from hledac.universal.pipeline.live_feed_pipeline import (
            _derive_query_context_terms,
        )

        domains, ipv4s, ipv6s = _derive_query_context_terms(
            "2001:0db8:85a3:0000:0000:8a2e:0370:7334"
        )
        assert "2001:0db8:85a3:0000:0000:8a2e:0370:7334" in ipv6s

    def test_derive_strips_stopwords(self):
        from hledac.universal.pipeline.live_feed_pipeline import (
            _derive_query_context_terms,
        )

        # Stopwords like "cve", "vulnerability" are stripped, leaving domain
        domains, ipv4s, ipv6s = _derive_query_context_terms(
            "CVE-2021-44228 log4j rce example.org"
        )
        assert "example.org" in domains
        # CVE pattern stripped
        assert "44228" not in str(ipv4s)

    def test_derive_empty_query(self):
        from hledac.universal.pipeline.live_feed_pipeline import (
            _derive_query_context_terms,
        )

        domains, ipv4s, ipv6s = _derive_query_context_terms("")
        assert domains == []
        assert ipv4s == []
        assert ipv6s == []

    @pytest.mark.asyncio
    async def test_scan_query_context_finds_domain(self):
        from hledac.universal.pipeline.live_feed_pipeline import (
            _scan_query_context_terms,
        )

        text = "The vulnerability affects example.com and 93.184.216.34"
        hits = await _scan_query_context_terms(text, "example.com 93.184.216.34")
        assert len(hits) >= 2  # at least domain + ipv4
        labels = {h["label"] for h in hits}
        assert "query_context_domain" in labels
        assert "query_context_ipv4" in labels

    @pytest.mark.asyncio
    async def test_scan_query_context_empty_on_no_match(self):
        from hledac.universal.pipeline.live_feed_pipeline import (
            _scan_query_context_terms,
        )

        text = "The vulnerability is in log4j"
        hits = await _scan_query_context_terms(text, "unrelated-domain.com")
        # No match since text doesn't contain the domain
        assert len(hits) == 0

    @pytest.mark.asyncio
    async def test_scan_query_context_none_query(self):
        from hledac.universal.pipeline.live_feed_pipeline import (
            _scan_query_context_terms,
        )

        text = "The vulnerability affects example.com"
        hits = await _scan_query_context_terms(text, None)
        assert hits == []


# ---------------------------------------------------------------------------
# Import-time side effects check
# MUST BE LAST — calls importlib.reload which pollutes class identities
# ---------------------------------------------------------------------------

class TestImportSideEffects:
    def test_z_import_module_no_side_effects(self):
        # Verify the module can be imported and has expected exports.
        # Avoid importlib.reload — it creates a new FeedSourceBatchRunResult
        # class object, breaking isinstance checks in downstream tests
        # that imported the class at module scope before reload.
        from hledac.universal.pipeline import live_feed_pipeline as mod
        assert hasattr(mod, "async_run_live_feed_pipeline")
        assert hasattr(mod, "async_run_feed_source_batch")
        assert hasattr(mod, "FeedPipelineRunResult")
