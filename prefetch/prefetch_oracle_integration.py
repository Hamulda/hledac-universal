"""
PrefetchOracleIntegration – lightweight bounded oracle for scheduler advisory ordering.

F200A: Sprint F200A prefetch oracle integration.

Role: ADVISORY ONLY — oracle SUGGESTS ordering; scheduler RETAINS authority.
Oracle never blocks, never raises, never takes over scheduler decisions.

Bounded design:
- MAX_CANDIDATES = 100 (hard cap on candidate list)
- MAX_SOURCE_HISTORY = 200 (per-source signals tracked)
- Scores returned as float multipliers for economics sort key
- All methods fail-soft: exception → default neutral score

Oracle signal sources (advisory only):
1. Historical yield: sources with higher accepted/fetched ratio → higher score
2. Recency: sources active in recent cycles → recency bonus
3. Novelty: sources with new URLs not yet seen → novelty bonus
4. Tier baseline: SURFACE > STRUCTURED_TI > DEEP > ARCHIVE > OTHER

Integration seam:
    scheduler.inject_prefetch_oracle(oracle)
    # During sort, oracle.suggest_scores(work_items) returns {feed_url: float}
    # Scheduler multiplies economics sort key by oracle score
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# F200A: Bounded constants
MAX_CANDIDATES = 100
MAX_SOURCE_HISTORY = 200
MAX_URL_SEEN = 50_000

# Score constants
SCORE_NEUTRAL = 1.0
SCORE_HOT = 1.3
SCORE_WARM = 1.1
SCORE_LUKewarm = 1.0
SCORE_MARGINAL = 0.8
SCORE_COLD = 0.6
SCORE_UNKNOWN = 1.0

# Recency bonus (per cycle since last activity)
RECENCY_BONUS_PER_CYCLE = 0.05
RECENCY_BONUS_MAX = 0.3

# Novelty bonus
NOVELTY_BONUS = 0.15


@dataclass
class _SourceSignal:
    """Per-source signal tracking (bounded)."""
    feed_url: str
    fetched: int = 0
    accepted: int = 0
    cycles_active: int = 0
    last_cycle: int = -1
    seen_urls: int = 0  # count of unique URLs discovered


class PrefetchOracleIntegration:
    """
    Lightweight bounded oracle for scheduler advisory ordering.

    F200A invariants:
    - Advisory only: oracle SUGGESTS, scheduler DECIDES
    - Fail-soft: all methods return neutral defaults on error
    - Bounded: MAX_SOURCE_HISTORY tracked, LRU eviction
    - No network I/O: purely advisory signal from in-memory state
    - No MLX/Metal: pure Python, M1-safe

    Integration:
        oracle = PrefetchOracleIntegration()
        scheduler.inject_prefetch_oracle(oracle)
        scores = oracle.suggest_scores(work_items)  # {feed_url: float}
    """

    def __init__(
        self,
        max_candidates: int = MAX_CANDIDATES,
        max_source_history: int = MAX_SOURCE_HISTORY,
        novelty_bonus: float = NOVELTY_BONUS,
        recency_bonus_per_cycle: float = RECENCY_BONUS_PER_CYCLE,
        recency_bonus_max: float = RECENCY_BONUS_MAX,
    ):
        self.max_candidates = max_candidates
        self.max_source_history = max_source_history
        self.novety_bonus = novelty_bonus
        self.recency_bonus_per_cycle = recency_bonus_per_cycle
        self.recency_bonus_max = recency_bonus_max

        # Bounded source signals: feed_url -> _SourceSignal
        # LRU eviction when max_source_history exceeded
        self._source_signals: OrderedDict[str, _SourceSignal] = OrderedDict()

        # Global URL discovery tracker (for novelty signal)
        self._seen_urls: OrderedDict[str, float] = OrderedDict()  # url -> first_seen_ts
        self._max_seen_urls = MAX_URL_SEEN

        # Score cache (avoid recompute every sort)
        self._score_cache: dict[str, float] = {}
        self._cache_cycle: int = -1

        # Statistics
        self._stats = {
            "suggestions_made": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }

    # ── Public API ─────────────────────────────────────────────────────────

    def suggest_scores(self, work_items: list[Any], current_cycle: int = 0) -> dict[str, float]:
        """
        Return advisory scores for work items.

        F200A-1: Advisory only — returns {feed_url: score} or empty dict on error.
        F200A-2: Bounded — max MAX_CANDIDATES items processed.
        F200A-3: Cache invalidation — cache cleared when current_cycle changes.

        Args:
            work_items: list of SourceWork dataclass instances
            current_cycle: current sprint cycle number (for cache invalidation)

        Returns:
            {feed_url: float} where float is a sort multiplier (1.0 = neutral).
            Empty dict on any error (scheduler falls back to default ordering).
        """
        try:
            if not work_items:
                return {}

            # Invalidate cache on new cycle
            if current_cycle != self._cache_cycle:
                self._score_cache.clear()
                self._cache_cycle = current_cycle

            scores: dict[str, float] = {}
            items_to_score = work_items[: self.max_candidates]

            for item in items_to_score:
                feed_url = getattr(item, "feed_url", None)
                if not feed_url:
                    continue

                # Cache hit
                if feed_url in self._score_cache:
                    scores[feed_url] = self._score_cache[feed_url]
                    self._stats["cache_hits"] += 1
                    continue

                # Compute score
                score = self._compute_source_score(feed_url, current_cycle)
                scores[feed_url] = score
                self._score_cache[feed_url] = score
                self._stats["cache_misses"] += 1

            self._stats["suggestions_made"] += 1
            return scores

        except Exception:
            # F200A-1: fail-soft — return empty dict, scheduler uses default ordering
            logger.debug("[F200A] oracle suggest_scores failed, using default ordering")
            return {}

    def record_outcome(
        self,
        feed_url: str,
        fetched: int,
        accepted: int,
        cycle: int,
        seen_new_urls: int = 0,
    ) -> None:
        """
        Record fetch outcome for future scoring.

        F200A-4: Bounded — max MAX_SOURCE_HISTORY sources tracked.
        F200A-5: LRU eviction — least-recently-used source removed on overflow.

        Args:
            feed_url: source URL
            fetched: number of entries fetched
            accepted: number of findings accepted (from quality gate)
            cycle: current sprint cycle number
            seen_new_urls: count of newly discovered unique URLs from this source
        """
        try:
            if feed_url in self._source_signals:
                sig = self._source_signals[feed_url]
                sig.fetched += fetched
                sig.accepted += accepted
                sig.cycles_active += 1
                sig.last_cycle = cycle
                sig.seen_urls += seen_new_urls
                # Move to end (most recently used)
                self._source_signals.move_to_end(feed_url)
            else:
                # New source
                if len(self._source_signals) >= self.max_source_history:
                    # LRU eviction
                    evicted_url, _ = self._source_signals.popitem(last=False)
                    logger.debug(f"[F200A] LRU evicting source: {evicted_url}")
                    # Clear from cache too
                    self._score_cache.pop(evicted_url, None)

                self._source_signals[feed_url] = _SourceSignal(
                    feed_url=feed_url,
                    fetched=fetched,
                    accepted=accepted,
                    cycles_active=1,
                    last_cycle=cycle,
                    seen_urls=seen_new_urls,
                )
        except Exception:
            # Fail-soft
            logger.debug(f"[F200A] record_outcome failed for {feed_url}")

    def record_url_seen(self, url: str) -> None:
        """
        Record that a URL was discovered (for novelty tracking).

        F200A-6: Bounded — max MAX_URL_SEEN tracked (LRU eviction).
        """
        try:
            if url in self._seen_urls:
                self._seen_urls.move_to_end(url)
                return

            if len(self._seen_urls) >= self._max_seen_urls:
                self._seen_urls.popitem(last=False)

            self._seen_urls[url] = time.time()
        except Exception:
            logger.debug(f"[F200A] record_url_seen failed for {url}")

    def get_stats(self) -> dict[str, Any]:
        """Return oracle statistics (for diagnostics)."""
        return {
            **self._stats,
            "sources_tracked": len(self._source_signals),
            "urls_tracked": len(self._seen_urls),
            "cache_size": len(self._score_cache),
        }

    def reset(self) -> None:
        """Reset all state (called at sprint teardown)."""
        self._source_signals.clear()
        self._seen_urls.clear()
        self._score_cache.clear()
        self._cache_cycle = -1
        self._stats = {
            "suggestions_made": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }

    # ── Internal scoring ───────────────────────────────────────────────────

    def _compute_source_score(self, feed_url: str, current_cycle: int) -> float:
        """
        Compute advisory score for a source.

        Score composition:
        1. Historical yield: accepted/fetched ratio → base score
        2. Recency bonus: cycles since last activity
        3. Novelty bonus: sources that discover new URLs

        Returns float multiplier for economics sort key.
        """
        signal = self._source_signals.get(feed_url)

        if signal is None:
            return SCORE_UNKNOWN

        # 1. Historical yield score
        if signal.fetched > 0:
            ratio = signal.accepted / signal.fetched
            if ratio >= 0.7:
                yield_score = SCORE_HOT
            elif ratio >= 0.4:
                yield_score = SCORE_WARM
            elif ratio >= 0.15:
                yield_score = SCORE_LUKewarm
            elif ratio >= 0.05:
                yield_score = SCORE_MARGINAL
            else:
                yield_score = SCORE_COLD
        else:
            yield_score = SCORE_UNKNOWN

        # 2. Recency bonus
        recency_bonus = 0.0
        if signal.last_cycle >= 0 and current_cycle > signal.last_cycle:
            cycles_since = current_cycle - signal.last_cycle
            recency_bonus = min(
                cycles_since * self.recency_bonus_per_cycle,
                self.recency_bonus_max,
            )

        # 3. Novelty bonus (sources that discover new URLs get boost)
        novelty_bonus = 0.0
        if signal.seen_urls > 0 and signal.cycles_active > 0:
            avg_urls_per_cycle = signal.seen_urls / signal.cycles_active
            if avg_urls_per_cycle > 5:  # Active discovery
                novelty_bonus = self.novety_bonus

        # Combine: yield_score is the base multiplier, bonuses are additive
        score = yield_score + recency_bonus + novelty_bonus
        return max(0.1, min(score, 3.0))  # Clamp to [0.1, 3.0]
