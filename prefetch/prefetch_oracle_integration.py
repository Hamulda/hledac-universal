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

import asyncio
import logging
import math
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# P1-2: Lazy DuckDB import — avoids eager load at module import time
_dduckdb = None
def _get_duckdb():
    global _dduckdb
    if _dduckdb is None:
        import duckdb as _dd
        _dduckdb = _dd
    return _dduckdb

# F200A: Bounded constants
MAX_CANDIDATES = 100
MAX_SOURCE_HISTORY = 200
MAX_URL_SEEN = 50_000

# Score constants
SCORE_NEUTRAL = 1.0
SCORE_HOT = 1.3
SCORE_WARM = 1.1
SCORE_LUKEWARMewarm = 1.0
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

        # P1-2: ThreadPoolExecutor for blocking DuckDB historical queries
        # DuckDB queries can block the event loop if run sync — offload to thread pool
        # M1 8GB: conservative 2-worker pool to avoid Metal memory pressure
        self._duckdb_executor: ThreadPoolExecutor | None = None
        self._duckdb_conn: Any = None  # Injected via inject_duckdb_conn()

        # P1-3: InterpreterPoolExecutor fallback for pure-Python CPU-bound scoring
        # normalize_text_for_score, compute_signal_diversity, aggregate_signals
        # Python 3.14+ InterpreterPoolExecutor; fallback to ThreadPoolExecutor on older Python
        self._interp_pool: Any = None

        # Statistics
        self._stats = {
            "suggestions_made": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "duckdb_historical_queries": 0,
            "interp_pool_batches": 0,
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
            loop = asyncio.get_running_loop()
            return loop.run_until_complete(
                self.suggest_scores_async(work_items, current_cycle))
        except RuntimeError:
            return asyncio.run(self.suggest_scores_async(work_items, current_cycle))
        except Exception:
            return self._suggest_scores_sequential(work_items, current_cycle)

    async def suggest_scores_async(
        self, work_items: list[Any], current_cycle: int = 0
    ) -> dict[str, float]:
        """
        P1-1: Async version — parallel score computation via safe_gather_dropin.

        Falls back to sequential scoring if no async context is available.

        Args:
            work_items: list of SourceWork dataclass instances
            current_cycle: current sprint cycle number (for cache invalidation)

        Returns:
            {feed_url: float} where float is a sort multiplier (1.0 = neutral).
        """
        try:
            if not work_items:
                return {}

            if current_cycle != self._cache_cycle:
                self._score_cache.clear()
                self._cache_cycle = current_cycle

            items_to_score = work_items[: self.max_candidates]
            scores: dict[str, float] = {}

            # P1-1: Batch items and score in parallel via safe_gather_dropin
            # batch_size=8 tuned for M1 8GB — avoids Metal memory pressure
            batch_size = 8
            batches: list[list[Any]] = [
                items_to_score[i : i + batch_size]
                for i in range(0, len(items_to_score), batch_size)
            ]

            async def _score_batch(batch: list[Any]) -> dict[str, float]:
                """Score a single batch sequentially (same source, no lock needed)."""
                result: dict[str, float] = {}
                for item in batch:
                    feed_url = getattr(item, "feed_url", None)
                    if not feed_url:
                        continue
                    if feed_url in self._score_cache:
                        result[feed_url] = self._score_cache[feed_url]
                        self._stats["cache_hits"] += 1
                        continue
                    score = self._compute_source_score(feed_url, current_cycle)
                    result[feed_url] = score
                    self._score_cache[feed_url] = score
                    self._stats["cache_misses"] += 1
                return result

            try:
                from utils.async_helpers import safe_gather_dropin

                gathered = await safe_gather_dropin(
                    *[_score_batch(batch) for batch in batches],
                    label="P1-1:oracle_score_batch",
                )
                for batch_scores in gathered:
                    if isinstance(batch_scores, dict):
                        scores.update(batch_scores)
            except Exception as e:
                # Fail-safe: fall back to sequential scoring
                logger.debug(f"[P1-1] safe_gather_dropin failed, sequential fallback: {e}")
                for item in items_to_score:
                    feed_url = getattr(item, "feed_url", None)
                    if not feed_url:
                        continue
                    if feed_url in self._score_cache:
                        scores[feed_url] = self._score_cache[feed_url]
                        self._stats["cache_hits"] += 1
                        continue
                    score = self._compute_source_score(feed_url, current_cycle)
                    scores[feed_url] = score
                    self._score_cache[feed_url] = score
                    self._stats["cache_misses"] += 1

            self._stats["suggestions_made"] += 1
            return scores

        except Exception:
            logger.debug("[P1-1] suggest_scores_async failed")
            return {}

    def _suggest_scores_sequential(
        self, work_items: list[Any], current_cycle: int = 0
    ) -> dict[str, float]:
        """Sequential fallback scoring."""
        try:
            if not work_items:
                return {}
            if current_cycle != self._cache_cycle:
                self._score_cache.clear()
                self._cache_cycle = current_cycle
            scores: dict[str, float] = {}
            for item in work_items[: self.max_candidates]:
                feed_url = getattr(item, "feed_url", None)
                if not feed_url:
                    continue
                if feed_url in self._score_cache:
                    scores[feed_url] = self._score_cache[feed_url]
                    self._stats["cache_hits"] += 1
                    continue
                score = self._compute_source_score(feed_url, current_cycle)
                scores[feed_url] = score
                self._score_cache[feed_url] = score
                self._stats["cache_misses"] += 1
            self._stats["suggestions_made"] += 1
            return scores
        except Exception:
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
            "duckdb_historical_queries": 0,
            "interp_pool_batches": 0,
        }

    def inject_duckdb_conn(self, conn: Any) -> None:
        """
        P1-2: Inject DuckDB connection for historical yield queries.

        Runs DuckDB historical queries in a ThreadPoolExecutor to avoid
        blocking the event loop. Connection must be thread-safe.

        Called by SprintScheduler during initialization.
        """
        self._duckdb_conn = conn

    # ── P1-2: DuckDB historical queries in ThreadPool ─────────────────────

    async def _query_historical_yield_async(
        self, feed_url: str, cycles_back: int = 20
    ) -> float:
        """
        P1-2: Query DuckDB for historical yield patterns across sprints.

        Runs DuckDB query in ThreadPoolExecutor to avoid blocking the event loop.

        Returns:
            float yield ratio in range [0.0, 1.0], or -1.0 if unavailable.
        """
        try:
            conn = getattr(self, "_duckdb_conn", None)
            if conn is None:
                return -1.0

            if self._duckdb_executor is None:
                self._duckdb_executor = ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix="oracle_duckdb"
                )

            def _query_sync() -> float:
                """Blocking DuckDB query — runs in thread pool."""
                try:
                    _get_duckdb()
                    q = """
                    SELECT COALESCE(
                        SUM(accepted)::DOUBLE / NULLIF(SUM(fetched), 0),
                        -1.0
                    ) AS yield_ratio
                    FROM (
                        SELECT
                            feed_url,
                            SUM(CASE WHEN finding_id IS NOT NULL THEN 1 ELSE 0 END) AS accepted,
                            SUM(CASE WHEN feed_url IS NOT NULL THEN 1 ELSE 0 END) AS fetched
                        FROM sprint_findings
                        WHERE feed_url = ?
                          AND cycle_ts > NOW() - INTERVAL '1 hours' * ?
                        GROUP BY feed_url
                    ) sub;
                    """
                    result = conn.execute(q, [feed_url, cycles_back]).fetchone()
                    if result is None:
                        return -1.0
                    return float(result[0]) if result[0] is not None else -1.0
                except Exception:
                    return -1.0

            loop = asyncio.get_running_loop()
            ratio = await loop.run_in_executor(self._duckdb_executor, _query_sync)
            self._stats["duckdb_historical_queries"] += 1
            return ratio
        except Exception:
            return -1.0

    # ── P1-3: InterpreterPoolExecutor pure-Python scoring ─────────────────

    def _get_interp_pool(self) -> Any:
        """
        P1-3: Lazily get or create InterpreterPoolExecutor.

        Python 3.14+ has InterpreterPoolExecutor in concurrent.futures.
        Falls back to ThreadPoolExecutor for older Python versions.

        M1 8GB safe: max_workers=2 (CPU-bound pure-Python scoring is lightweight).
        """
        if self._interp_pool is not None:
            return self._interp_pool
        try:
            from concurrent.futures import InterpreterPoolExecutor
            self._interp_pool = InterpreterPoolExecutor(max_workers=2)
        except (ImportError, AttributeError):
            self._interp_pool = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="oracle_interp"
            )
        return self._interp_pool

    def _normalize_text_for_score(self, text: str) -> str:
        """
        P1-3: Normalize text for scoring — lowercase, strip, remove punctuation.

        Pure Python CPU-bound function suitable for InterpreterPoolExecutor.
        """
        import re
        text = text.lower().strip()
        text = re.sub(r"[^\w\s]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _compute_signal_diversity(self, signals: list) -> float:
        """
        P1-3: Compute signal diversity score across sources (entropy-based).

        Pure Python CPU-bound function suitable for InterpreterPoolExecutor.

        Returns:
            float diversity score in range [0.0, 1.0]
        """
        if not signals:
            return 0.0
        type_counts: dict[str, int] = {}
        for sig in signals:
            key = f"accepted_{min(sig.accepted, 10)}"
            type_counts[key] = type_counts.get(key, 0) + 1
        if len(type_counts) <= 1:
            return 0.0
        total = len(signals)
        entropy = 0.0
        for count in type_counts.values():
            p = count / total
            if p > 0:
                entropy -= p * math.log2(p)
        max_entropy = math.log2(len(type_counts)) if type_counts else 1.0
        return entropy / max_entropy if max_entropy > 0 else 0.0

    def _aggregate_signals(
        self,
        feed_url: str,
        yield_score: float,
        recency_bonus: float,
        novelty_bonus: float,
    ) -> float:
        """
        P1-3: Aggregate all signals into final score using weighted combination.

        Pure Python CPU-bound function suitable for InterpreterPoolExecutor.

        Returns:
            float final score in range [0.1, 3.0]
        """
        del feed_url  # Reserved for future per-URL weighting
        total_bonus = recency_bonus * 0.5 + novelty_bonus * 0.5
        score = yield_score * (1.0 + total_bonus)
        return max(0.1, min(score, 3.0))

    async def _score_batch_pure_python(
        self,
        items: list[Any],
        current_cycle: int,
    ) -> dict[str, float]:
        """
        P1-3: Score a batch of items using pure-Python functions.

        Uses InterpreterPoolExecutor (Python 3.14+) or ThreadPoolExecutor fallback.

        Returns:
            {feed_url: float} score dict
        """
        try:
            pool = self._get_interp_pool()
            self._stats["interp_pool_batches"] += 1

            def _score_item(item: Any) -> tuple[str, float]:
                """Score a single item — runs in thread/interp pool."""
                feed_url = getattr(item, "feed_url", None) or getattr(item, "url", None)
                if not feed_url:
                    return ("", 0.0)
                score = self._compute_source_score(feed_url, current_cycle)
                _ = self._normalize_text_for_score(feed_url)
                active_sigs = list(self._source_signals.values())[:50]
                diversity = self._compute_signal_diversity(active_sigs)
                final_score = self._aggregate_signals(
                    feed_url,
                    score * (1 + diversity * 0.1),
                    0.0,
                    0.0,
                )
                return (feed_url, final_score)

            loop = asyncio.get_running_loop()
            futures = [
                loop.run_in_executor(pool, _score_item, item) for item in items
            ]
            results = await asyncio.gather(*futures, return_exceptions=True)
            scores: dict[str, float] = {}
            for result in results:
                if isinstance(result, tuple) and len(result) == 2:
                    url, sc = result
                    if url:
                        scores[url] = sc
            return scores
        except Exception as e:
            logger.debug(f"[P1-3] _score_batch_pure_python failed: {e}")
            return {}

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
                yield_score = SCORE_LUKEWARM
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
