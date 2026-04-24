"""
Sprint F200A — Test bounded prefetch oracle integration.

Invariant F200A-1: suggest_scores returns {feed_url: float} or empty dict on error
Invariant F200A-2: suggest_scores bounded at MAX_CANDIDATES items
Invariant F200A-3: record_outcome bounded at MAX_SOURCE_HISTORY (LRU eviction)
Invariant F200A-4: LRU eviction removes least-recently-used source
Invariant F200A-5: scheduler falls back to default ordering when oracle is None
Invariant F200A-6: scheduler calls oracle.suggest_scores during sort
Invariant F200A-7: oracle.reset() clears all state
Invariant F200A-8: get_stats returns diagnostic dict
"""

import pytest

from hledac.universal.prefetch.prefetch_oracle_integration import (
    PrefetchOracleIntegration,
    MAX_CANDIDATES,
    MAX_SOURCE_HISTORY,
    SCORE_HOT,
    SCORE_WARM,
    SCORE_COLD,
    SCORE_NEUTRAL,
)


class FakeWorkItem:
    """Minimal SourceWork stand-in for testing."""
    def __init__(self, feed_url: str):
        self.feed_url = feed_url


class TestF200AOracleSuggestScores:
    """Test F200A-1,2: suggest_scores returns dict or empty on error, bounded."""

    def test_returns_dict_with_scores(self):
        """F200A-1: suggest_scores returns {feed_url: float} for valid items."""
        oracle = PrefetchOracleIntegration()
        items = [FakeWorkItem("https://example.com/feed")]
        scores = oracle.suggest_scores(items, current_cycle=0)
        assert isinstance(scores, dict)
        assert "https://example.com/feed" in scores
        assert isinstance(scores["https://example.com/feed"], float)

    def test_returns_empty_dict_for_empty_items(self):
        """F200A-1: empty items → empty dict."""
        oracle = PrefetchOracleIntegration()
        scores = oracle.suggest_scores([], current_cycle=0)
        assert scores == {}

    def test_returns_empty_dict_on_exception(self):
        """F200A-1: exception → empty dict (fail-soft)."""
        oracle = PrefetchOracleIntegration()
        scores = oracle.suggest_scores(None, current_cycle=0)
        assert scores == {}

    def test_bounded_at_max_candidates(self):
        """F200A-2: only first MAX_CANDIDATES items processed."""
        oracle = PrefetchOracleIntegration()
        items = [FakeWorkItem(f"https://source-{i}.example/feed") for i in range(200)]
        scores = oracle.suggest_scores(items, current_cycle=0)
        assert len(scores) <= MAX_CANDIDATES

    def test_cache_reuse_same_cycle(self):
        """F200A-3: same cycle call reuses cache (cache hit)."""
        oracle = PrefetchOracleIntegration()
        items = [FakeWorkItem("https://example.com/feed")]

        # First call — cache miss
        oracle.record_outcome("https://example.com/feed", fetched=10, accepted=8, cycle=0)
        scores1 = oracle.suggest_scores(items, current_cycle=0)
        assert scores1["https://example.com/feed"] == SCORE_HOT

        # Second call same cycle — cache hit
        scores2 = oracle.suggest_scores(items, current_cycle=0)
        assert scores2["https://example.com/feed"] == SCORE_HOT

        stats = oracle.get_stats()
        assert stats["cache_hits"] >= 1


class TestF200AOracleRecordOutcome:
    """Test F200A-3,4: record_outcome bounded with LRU eviction."""

    def test_accumulates_outcome(self):
        """F200A-3: record_outcome accumulates fetched/accepted."""
        oracle = PrefetchOracleIntegration()
        oracle.record_outcome("https://example.com/feed", fetched=10, accepted=5, cycle=0)
        oracle.record_outcome("https://example.com/feed", fetched=10, accepted=3, cycle=1)

        # Score reflects accumulated data at same cycle (no recency bonus)
        items = [FakeWorkItem("https://example.com/feed")]
        scores = oracle.suggest_scores(items, current_cycle=1)
        # accepted/fetched = 8/20 = 0.4 → SCORE_WARM + recency bonus
        # cycles_since = 1 - 1 = 0 → no recency bonus
        assert scores["https://example.com/feed"] == SCORE_WARM

    def test_bounded_at_max_source_history(self):
        """F200A-3: max MAX_SOURCE_HISTORY sources tracked."""
        oracle = PrefetchOracleIntegration()
        for i in range(MAX_SOURCE_HISTORY + 50):
            oracle.record_outcome(f"https://source-{i}.example/feed", fetched=5, accepted=2, cycle=0)

        stats = oracle.get_stats()
        assert stats["sources_tracked"] == MAX_SOURCE_HISTORY

    def test_lru_eviction_removes_oldest(self):
        """F200A-4: LRU eviction removes least-recently-used source."""
        oracle = PrefetchOracleIntegration()

        # Record MAX+1 sources: indices 0 through MAX
        for i in range(MAX_SOURCE_HISTORY + 1):
            oracle.record_outcome(f"https://source-{i}.example/feed", fetched=5, accepted=2, cycle=0)

        # Verify MAX tracked
        assert oracle._source_signals[next(iter(oracle._source_signals))] is not None
        assert len(oracle._source_signals) == MAX_SOURCE_HISTORY

        # Source-0 was first (oldest/L RU), should be evicted
        assert "https://source-0.example/feed" not in oracle._source_signals
        # Source-MAX should still be there
        assert f"https://source-{MAX_SOURCE_HISTORY}.example/feed" in oracle._source_signals

    def test_record_outcome_fail_soft(self):
        """F200A-3: record_outcome never raises."""
        oracle = PrefetchOracleIntegration()
        # No assertion — just verify it doesn't raise
        oracle.record_outcome("https://example.com", fetched=0, accepted=0, cycle=0)


class TestF200AOracleScoreComposition:
    """Test oracle score composition: yield + recency + novelty."""

    def test_hot_source_score_no_recency(self):
        """High yield (>=0.7) → SCORE_HOT when same cycle (no recency bonus)."""
        oracle = PrefetchOracleIntegration()
        oracle.record_outcome("https://hot.example/feed", fetched=100, accepted=80, cycle=0)
        items = [FakeWorkItem("https://hot.example/feed")]
        # current_cycle=0 same as last_cycle=0 → no recency bonus
        scores = oracle.suggest_scores(items, current_cycle=0)
        assert scores["https://hot.example/feed"] == SCORE_HOT

    def test_warm_source_score_no_recency(self):
        """Medium yield (0.4-0.7) → SCORE_WARM when same cycle."""
        oracle = PrefetchOracleIntegration()
        oracle.record_outcome("https://warm.example/feed", fetched=100, accepted=40, cycle=0)
        items = [FakeWorkItem("https://warm.example/feed")]
        scores = oracle.suggest_scores(items, current_cycle=0)
        assert scores["https://warm.example/feed"] == SCORE_WARM

    def test_cold_source_score_no_recency(self):
        """Low yield (<0.05) → SCORE_COLD when same cycle."""
        oracle = PrefetchOracleIntegration()
        oracle.record_outcome("https://cold.example/feed", fetched=100, accepted=3, cycle=0)
        items = [FakeWorkItem("https://cold.example/feed")]
        scores = oracle.suggest_scores(items, current_cycle=0)
        assert scores["https://cold.example/feed"] == SCORE_COLD

    def test_unknown_source_returns_neutral(self):
        """Unknown source → SCORE_NEUTRAL."""
        oracle = PrefetchOracleIntegration()
        items = [FakeWorkItem("https://unknown.example/feed")]
        scores = oracle.suggest_scores(items, current_cycle=0)
        assert scores["https://unknown.example/feed"] == SCORE_NEUTRAL

    def test_recency_bonus_applies(self):
        """Recency bonus applies when source was active in previous cycle."""
        oracle = PrefetchOracleIntegration()
        oracle.record_outcome("https://recent.example/feed", fetched=100, accepted=50, cycle=0)
        items = [FakeWorkItem("https://recent.example/feed")]
        # current_cycle=1, last_cycle=0 → cycles_since=1 → recency bonus
        scores = oracle.suggest_scores(items, current_cycle=1)
        # Base SCORE_WARM + 0.05 recency bonus
        assert scores["https://recent.example/feed"] > SCORE_WARM

    def test_score_clamped_to_valid_range(self):
        """Score clamped to [0.1, 3.0] range."""
        oracle = PrefetchOracleIntegration()
        # Very high yield should stay under 3.0
        oracle.record_outcome("https://high.example/feed", fetched=100, accepted=95, cycle=0)
        items = [FakeWorkItem("https://high.example/feed")]
        scores = oracle.suggest_scores(items, current_cycle=100)  # large recency bonus
        assert scores["https://high.example/feed"] <= 3.0
        assert scores["https://high.example/feed"] >= 0.1


class TestF200AOracleSchedulerIntegration:
    """Test F200A-5,6: scheduler falls back gracefully."""

    def test_scheduler_fallback_when_oracle_none(self):
        """F200A-5: scheduler uses default ordering when oracle is None."""
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
        )
        sched = SprintScheduler(SprintSchedulerConfig())

        # Oracle should be None by default
        assert sched._prefetch_oracle is None

        # _sort_work_items_by_economics should not raise with None oracle
        from hledac.universal.runtime.sprint_scheduler import SourceWork, SourceTier
        items = [
            SourceWork(feed_url="https://a.example/feed", source="other", tier=SourceTier.OTHER),
            SourceWork(feed_url="https://b.example/feed", source="other", tier=SourceTier.OTHER),
        ]
        result = sched._sort_work_items_by_economics(items, current_cycle=0)
        assert len(result) == 2

    def test_scheduler_calls_oracle_on_sort(self):
        """F200A-6: oracle.suggest_scores called during sort."""
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
            SourceWork,
            SourceTier,
        )
        sched = SprintScheduler(SprintSchedulerConfig())

        # Create oracle and inject
        oracle = PrefetchOracleIntegration()
        sched.inject_prefetch_oracle(oracle)

        items = [
            SourceWork(feed_url="https://a.example/feed", source="other", tier=SourceTier.OTHER),
            SourceWork(feed_url="https://b.example/feed", source="other", tier=SourceTier.OTHER),
        ]

        # Record outcome for one source
        oracle.record_outcome("https://a.example/feed", fetched=100, accepted=80, cycle=0)

        # Sort should use oracle scores
        sched._sort_work_items_by_economics(items, current_cycle=1)

        # Stats should show suggestions made
        stats = oracle.get_stats()
        assert stats["suggestions_made"] >= 1


class TestF200AOracleReset:
    """Test F200A-7: oracle.reset() clears state."""

    def test_reset_clears_all_state(self):
        """F200A-7: reset() clears signals, URLs, cache, stats."""
        oracle = PrefetchOracleIntegration()
        oracle.record_outcome("https://example.com/feed", fetched=10, accepted=5, cycle=0)
        oracle.record_url_seen("https://discovered.example/page")

        stats_before = oracle.get_stats()
        assert stats_before["sources_tracked"] == 1
        assert stats_before["urls_tracked"] == 1

        oracle.reset()

        stats_after = oracle.get_stats()
        assert stats_after["sources_tracked"] == 0
        assert stats_after["urls_tracked"] == 0
        assert stats_after["suggestions_made"] == 0
        assert stats_after["cache_hits"] == 0
        assert stats_after["cache_misses"] == 0


class TestF200AOracleStats:
    """Test F200A-8: get_stats returns diagnostics."""

    def test_get_stats_returns_dict(self):
        """F200A-8: get_stats returns dict with expected keys."""
        oracle = PrefetchOracleIntegration()
        stats = oracle.get_stats()
        assert isinstance(stats, dict)
        assert "suggestions_made" in stats
        assert "cache_hits" in stats
        assert "cache_misses" in stats
        assert "sources_tracked" in stats
        assert "urls_tracked" in stats
        assert "cache_size" in stats

    def test_cache_hit_increments(self):
        """Cache hits tracked in stats."""
        oracle = PrefetchOracleIntegration()
        items = [FakeWorkItem("https://example.com/feed")]
        oracle.suggest_scores(items, current_cycle=0)
        oracle.suggest_scores(items, current_cycle=0)  # same cycle — cache hit
        stats = oracle.get_stats()
        assert stats["cache_hits"] >= 1
