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


import asyncio
import logging
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

# P0-2: Lazy Rust import — batch_compute_scores via ARM NEON
_rust_batch_scores: Any = None
def _get_rust_batch_scores():
    global _rust_batch_scores
    if _rust_batch_scores is None:
        try:
            from hledac_rust_extensions import batch_compute_scores
            _rust_batch_scores = batch_compute_scores
        except Exception:
            _rust_batch_scores = None
    return _rust_batch_scores

# F271: Lazy Rust import — batch_aggregate_signals via ARM NEON
#
# batch_aggregate_signals(signals, weights, normalize) aggregates per-source
# signal vectors into a single weighted-average vector using ARM NEON SIMD.
# Currently unused — reserved for future multi-source vector scoring:
#   signals: list of [f32, ...] per source (e.g., multi-dimensional yield vectors)
#   weights: per-source importance weights
#   normalize: weighted average vs weighted sum
# Will be wired in a future sprint when multi-dimensional source signals
# (beyond scalar yield ratio) are tracked.
_rust_aggregate_signals: Any = None
def _get_rust_aggregate_signals():
    global _rust_aggregate_signals
    if _rust_aggregate_signals is None:
        try:
            from hledac_rust_extensions import batch_aggregate_signals
            _rust_aggregate_signals = batch_aggregate_signals
        except Exception:
            _rust_aggregate_signals = None
    return _rust_aggregate_signals

# F200A: Bounded constants
MAX_CANDIDATES = 100
MAX_SOURCE_HISTORY = 200
MAX_URL_SEEN = 50_000

# Score constants
SCORE_NEUTRAL = 1.0
SCORE_HOT = 1.3
SCORE_WARM = 1.1
SCORE_LUKEWARM = 1.0
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

        # P3-1: IOC graph and DuckDB store for speculative prefetch
        # Injected via inject_ioc_graph() / inject_duckdb_store()
        self._ioc_graph: Any = None
        self._duckdb_store: Any = None

        # P3-1: Cache of recently-fetched IOC values to avoid re-prefetching
        self._prefetched_iocs: OrderedDict[str, float] = OrderedDict()  # ioc_value -> last_prefetch_ts
        self._max_prefetched_iocs: int = 5000
        self._prefetch_ttl_s: float = 300.0  # 5 min TTL before IOC is eligible again

        # Statistics
        self._stats = {
            "suggestions_made": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "duckdb_historical_queries": 0,
            "predict_next_iocs_calls": 0,
            "predict_next_iocs_items": 0,
            "graph_traversals": 0,
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
        # F271B-FIX: Python 3.14 asyncio.get_running_loop() raises RuntimeError
        # inside running loop BEFORE returning the loop object — the
        # "loop.run_until_complete() from running loop" path is UNREACHABLE.
        # Fix: use same pattern as academic_discovery._run_sync — check
        # FIRST, then decide sync vs async, never try both on same coroutine.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — safe to instantiate coroutine
            return asyncio.run(self.suggest_scores_async(work_items, current_cycle))
        except Exception:
            return self._suggest_scores_sequential(work_items, current_cycle)
        # Running loop detected — await directly (caller must be async)
        raise RuntimeError(
            "suggest_scores() called from running event loop — "
            "use suggest_scores_async() directly instead"
        )

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

            # P0-2: Extract feed_urls, split cached vs uncached
            uncached_urls: list[str] = []
            for item in items_to_score:
                feed_url = getattr(item, "feed_url", None)
                if not feed_url:
                    continue
                if feed_url in self._score_cache:
                    scores[feed_url] = self._score_cache[feed_url]
                    self._stats["cache_hits"] += 1
                else:
                    uncached_urls.append(feed_url)

            # P0-2: Batch scoring — single call for all uncached URLs
            # Rust NEON path when available, pure-Python fallback otherwise
            if uncached_urls:
                try:
                    batch_scores = self._compute_source_score_batch(uncached_urls, current_cycle)
                    for feed_url, score in batch_scores.items():
                        scores[feed_url] = score
                        self._score_cache[feed_url] = score
                        self._stats["cache_misses"] += 1
                except Exception as e:
                    # Fail-safe: fall back to per-source scoring
                    logger.debug(f"[P0-2] batch scoring failed, sequential fallback: {e}")
                    for feed_url in uncached_urls:
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
        self._prefetched_iocs.clear()
        self._stats = {
            "suggestions_made": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "duckdb_historical_queries": 0,
            "predict_next_iocs_calls": 0,
            "predict_next_iocs_items": 0,
            "graph_traversals": 0,
        }

    def inject_duckdb_conn(self, conn: Any) -> None:
        """
        P1-2: Inject DuckDB connection for historical yield queries.

        Runs DuckDB historical queries in a ThreadPoolExecutor to avoid
        blocking the event loop. Connection must be thread-safe.

        Called by SprintScheduler during initialization.
        """
        self._duckdb_conn = conn

    def inject_ioc_graph(self, ioc_graph: Any) -> None:
        """
        P3-1: Inject IOC graph (DuckPGQGraph) for speculative prefetch.

        Graph provides find_connected(value, max_hops) for predicting
        next likely IOCs based on entity relationships.

        Called by SprintScheduler during initialization.
        """
        self._ioc_graph = ioc_graph

    def inject_duckdb_store(self, store: Any) -> None:
        """
        P3-1: Inject DuckDB store for recent findings lookup.

        Store provides async_get_recent_findings(limit) for retrieving
        the latest accepted IOCs to traverse from.

        Called by SprintScheduler during initialization.
        """
        self._duckdb_store = store

    # ── P3-1: IOC Graph speculative prefetch ──────────────────────────────

    async def predict_next_iocs(self, top_k: int = 10) -> list[dict]:
        """
        P3-1: Predict next likely IOCs using graph traversal.

        Strategy:
        1. Get recent accepted findings from DuckDB (last 1000)
        2. Extract unique IOC values and their types
        3. For each recent IOC, traverse graph to find connected entities
        4. Score candidates by: confidence × recency × degree
        5. Filter out recently prefetched IOCs (TTL 5 min)
        6. Return top_k candidates as dicts

        Args:
            top_k: Maximum number of predictions to return.

        Returns:
            List of dicts with keys: ioc_value, ioc_type, confidence,
            source_node, prediction_method.
            Empty list on any error (fail-soft).
        """
        try:
            self._stats["predict_next_iocs_calls"] += 1

            # Step 1: Get recent findings from DuckDB
            recent_findings: list[Any] = []
            try:
                store = getattr(self, "_duckdb_store", None)
                if store is not None and hasattr(store, "async_get_recent_findings"):
                    recent_findings = await store.async_get_recent_findings(limit=1000)
                elif store is not None and hasattr(store, "get_recent_findings"):
                    # Sync fallback via thread
                    recent_findings = await asyncio.to_thread(
                        store.get_recent_findings, limit=1000
                    )
            except Exception as e:
                logger.debug(f"[P3-1] Failed to get recent findings: {e}")
                return []

            if not recent_findings:
                return []

            # Step 2: Extract IOC candidates from findings
            candidates: dict[str, dict] = {}
            now = time.time()

            for finding in recent_findings:
                # CanonicalFinding has: finding_id, query, source_type, confidence, ts, provenance
                ioc_value: str | None = None
                ioc_type: str = "unknown"

                # Try common IOC field names (frozen Struct — use getattr)
                for attr in ("value", "ioc_value", "domain", "url", "ip", "hash", "finding_id"):
                    val = getattr(finding, attr, None)
                    if isinstance(val, str) and val:
                        ioc_value = val
                        break

                if not ioc_value:
                    continue

                # Determine IOC type from source_type or value pattern
                source_type = getattr(finding, "source_type", "")
                if "domain" in source_type or (ioc_type == "unknown" and "." in ioc_value and not ioc_value.startswith("http")):
                    ioc_type = "domain"
                elif "url" in source_type or ioc_value.startswith(("http://", "https://")):
                    ioc_type = "url"
                elif "ip" in source_type:
                    ioc_type = "ip"
                elif "hash" in source_type:
                    ioc_type = "hash"

                # Skip already prefetched recently (TTL check)
                if ioc_value in self._prefetched_iocs:
                    last_ts = self._prefetched_iocs[ioc_value]
                    if now - last_ts < self._prefetch_ttl_s:
                        continue

                confidence = float(getattr(finding, "confidence", 0.5))
                found_ts = float(getattr(finding, "ts", now))

                if ioc_value not in candidates or candidates[ioc_value]["confidence"] < confidence:
                    candidates[ioc_value] = {
                        "ioc_value": ioc_value,
                        "ioc_type": ioc_type,
                        "confidence": confidence,
                        "source_node": "duckdb_recent",
                        "prediction_method": "duckdb_recent",
                        "found_ts": found_ts,
                    }

            # Step 3: Graph traversal for connected IOCs (async, non-blocking)
            graph_candidates: dict[str, dict] = {}
            try:
                graph = getattr(self, "_ioc_graph", None)
                if graph is not None and hasattr(graph, "find_connected"):
                    self._stats["graph_traversals"] += 1

                    # Traverse from top-confidence candidates (limit graph queries to avoid OOM)
                    top_sources = sorted(
                        candidates.values(),
                        key=lambda x: x["confidence"],
                        reverse=True
                    )[:20]  # Max 20 graph traversals

                    # P3-1: Use asyncio.to_thread for non-blocking graph traversal
                    # Check for Rust batch path first (fastest via rayon parallelization)
                    db_path = getattr(graph, "db_path", None)
                    if db_path and hasattr(graph, "find_connected_batch"):
                        # Rust batch path: parallel via rayon, non-blocking via to_thread
                        source_values = [src["ioc_value"] for src in top_sources]

                        def _batch_traverse_sync() -> dict[str, list[dict]]:
                            """Sync batch traversal — runs in thread pool via asyncio.to_thread."""
                            try:
                                # Try Rust batch_graph_traverse first (fastest)
                                from hledac_rust_extensions import batch_graph_traverse
                                raw = batch_graph_traverse(db_path, source_values, 2)
                                if raw is not None:
                                    return dict(raw)
                            except Exception:  # noqa: BLE001
                                pass
                            # Fallback: Python batch (same-db connection)
                            return graph.find_connected_batch(source_values, max_hops=2)

                        batch_results: dict[str, list[dict]] = await asyncio.to_thread(
                            _batch_traverse_sync
                        )

                        for src in top_sources:
                            src_value = src["ioc_value"]
                            connected = batch_results.get(src_value, [])
                            for node in connected[:5]:  # Max 5 per source
                                node_value = node.get("value") or node.get("ioc_value")
                                node_type = node.get("type", src["ioc_type"])
                                node_conf = float(node.get("confidence", 0.5))

                                if not node_value or node_value in self._prefetched_iocs:
                                    continue
                                if now - self._prefetched_iocs.get(node_value, 0) < self._prefetch_ttl_s:
                                    continue

                                # Score: source confidence × node confidence × recency bonus
                                recency_bonus = 1.0 + max(0, (now - src["found_ts"]) / 86400) * 0.1
                                score = src["confidence"] * node_conf * recency_bonus

                                if (node_value not in graph_candidates or
                                        graph_candidates[node_value]["confidence"] < score):
                                    graph_candidates[node_value] = {
                                        "ioc_value": node_value,
                                        "ioc_type": node_type,
                                        "confidence": min(score, 1.0),
                                        "source_node": src_value,
                                        "prediction_method": "graph_traversal",
                                        "found_ts": now,
                                    }
                    else:
                        # Legacy Python batch path — find_connected_batch avoids N sequential calls
                        legacy_source_values = [src["ioc_value"] for src in top_sources]

                        def _legacy_batch_sync() -> dict[str, list[dict]]:
                            """Python batch traversal — no Rust dependency."""
                            try:
                                return graph.find_connected_batch(legacy_source_values, max_hops=2)
                            except Exception:
                                return {}

                        batch_results: dict[str, list[dict]] = await asyncio.to_thread(
                            _legacy_batch_sync
                        )

                        for src in top_sources:
                            src_value = src["ioc_value"]
                            connected = batch_results.get(src_value, [])
                            for node in connected[:5]:  # Max 5 per source
                                node_value = node.get("value") or node.get("ioc_value")
                                node_type = node.get("type", src["ioc_type"])
                                node_conf = float(node.get("confidence", 0.5))

                                if not node_value or node_value in self._prefetched_iocs:
                                    continue
                                if now - self._prefetched_iocs.get(node_value, 0) < self._prefetch_ttl_s:
                                    continue

                                # Score: source confidence × node confidence × recency bonus
                                recency_bonus = 1.0 + max(0, (now - src["found_ts"]) / 86400) * 0.1
                                score = src["confidence"] * node_conf * recency_bonus

                                if (node_value not in graph_candidates or
                                        graph_candidates[node_value]["confidence"] < score):
                                    graph_candidates[node_value] = {
                                        "ioc_value": node_value,
                                        "ioc_type": node_type,
                                        "confidence": min(score, 1.0),
                                        "source_node": src_value,
                                        "prediction_method": "graph_traversal",
                                        "found_ts": now,
                                    }
            except Exception as e:
                logger.debug(f"[P3-1] Graph access failed: {e}")

            # Step 4: Merge candidates (graph candidates override duckdb candidates for same key)
            all_candidates = {**candidates, **graph_candidates}

            # Step 5: Sort by confidence and return top_k
            sorted_candidates = sorted(
                all_candidates.values(),
                key=lambda x: x["confidence"],
                reverse=True
            )[:top_k]

            # Step 6: Mark as recently seen (LRU eviction)
            for cand in sorted_candidates:
                self._prefetched_iocs[cand["ioc_value"]] = now
                if len(self._prefetched_iocs) > self._max_prefetched_iocs:
                    self._prefetched_iocs.popitem(last=False)

            self._stats["predict_next_iocs_items"] += len(sorted_candidates)
            return sorted_candidates

        except Exception as e:
            logger.debug(f"[P3-1] predict_next_iocs failed: {e}")
            return []

    def record_prefetch_outcome(
        self,
        ioc_value: str,
        _success: bool = True,
        _bytes_downloaded: int = 0,
    ) -> None:
        """
        P3-1: Record the outcome of a prefetch attempt.

        Called by ContinuousPrefetchPipeline._prefetch_item() after fetch.

        Args:
            ioc_value: The IOC that was prefetched.
            success: True if fetch succeeded and data was cached.
            bytes_downloaded: Bytes downloaded (for bandwidth accounting).
        """
        try:
            # Always update timestamp to avoid hammering failing IOCs
            self._prefetched_iocs[ioc_value] = time.time()
            if len(self._prefetched_iocs) > self._max_prefetched_iocs:
                self._prefetched_iocs.popitem(last=False)
        except Exception:  # noqa: BLE001
            pass

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

    async def query_historical_yield_batch_async(
        self, feed_urls: list[str], cycles_back: int = 20
    ) -> dict[str, float]:
        """
        P1-2: Bulk DuckDB query for historical yield across multiple sources.

        Single round-trip to DuckDB for N sources (vs N round-trips in N+1 pattern).
        Uses DuckDB IN-clause with placeholders for bulk lookup.

        Returns:
            {feed_url: yield_ratio} for sources with data; -1.0 if unavailable.
        """
        if not feed_urls:
            return {}
        try:
            conn = getattr(self, "_duckdb_conn", None)
            if conn is None:
                return {}

            if self._duckdb_executor is None:
                self._duckdb_executor = ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix="oracle_duckdb"
                )

            def _query_sync() -> dict[str, float]:
                """Single blocking DuckDB query for all sources — runs in thread pool."""
                try:
                    _get_duckdb()
                    placeholders = ",".join(["?"] * len(feed_urls))
                    q = f"""
                    SELECT
                        feed_url,
                        COALESCE(
                            SUM(CASE WHEN finding_id IS NOT NULL THEN 1 ELSE 0 END)::DOUBLE
                                / NULLIF(SUM(CASE WHEN feed_url IS NOT NULL THEN 1 ELSE 0 END), 0),
                            -1.0
                        ) AS yield_ratio
                    FROM sprint_findings
                    WHERE feed_url IN ({placeholders})
                      AND cycle_ts > NOW() - INTERVAL '1 hours' * ?
                    GROUP BY feed_url;
                    """
                    rows = conn.execute(q, [*feed_urls, cycles_back]).fetchall()
                    return {
                        row[0]: float(row[1]) if row[1] is not None else -1.0
                        for row in rows
                    }
                except Exception:
                    return {}

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(self._duckdb_executor, _query_sync)
            self._stats["duckdb_historical_queries"] += 1
            return result
        except Exception:
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

    def _compute_source_score_batch(
        self, feed_urls: list[str], current_cycle: int
    ) -> dict[str, float]:
        """
        P0-2: Batch source scoring — all signals in one pass.

        Builds stats list for Rust batch_compute_scores (F199A NEON path) when
        Rust is available; falls back to pure-Python loop when not.
        Single ThreadPoolExecutor call replaces N sequential _compute_source_score calls.

        Args:
            feed_urls: list of feed URLs to score
            current_cycle: current sprint cycle (for recency bonus)

        Returns:
            {feed_url: score} for all feed_urls with known signals;
            unknown URLs use SCORE_UNKNOWN.
        """
        rust_fn = _get_rust_batch_scores()
        if rust_fn is not None:
            # Build stats list for Rust NEON batch path (F199A formula)
            # Maps feed_url -> index in stats list
            url_order: list[str] = []
            stats_list: list[dict[str, object]] = []
            for feed_url in feed_urls:
                signal = self._source_signals.get(feed_url)
                if signal is None:
                    continue
                url_order.append(feed_url)
                # batch_compute_scores dict format: fetched, accepted, current_weight, novelty
                # NOTE: must use int for fetched/accepted — Rust extracts as u32, float→u32 fails
                stats_list.append({
                    "fetched": int(signal.fetched),
                    "accepted": int(signal.accepted),
                    "current_weight": 1.0,  # unused by oracle formula but required by Rust API
                    "novelty": signal.seen_urls > 0 and signal.cycles_active > 0
                    and (signal.seen_urls / max(1, signal.cycles_active)) > 5,
                })

            if not stats_list:
                return dict.fromkeys(feed_urls, SCORE_UNKNOWN)

            try:
                # Rust NEON path: returns F199A weights [0.3, 2.5]
                # Convert F199A weight back to oracle score space
                raw_weights: list[float] = rust_fn(stats_list)
                result: dict[str, float] = {}
                for i, feed_url in enumerate(url_order):
                    # Map F199A [0.3, 2.5] → oracle [0.1, 3.0] approximately
                    # F199A delta multiplier × oracle base → oracle score
                    delta = raw_weights[i]  # already clamped [0.3, 2.5]
                    base = SCORE_NEUTRAL  # use neutral as base multiplier
                    result[feed_url] = max(0.1, min(delta * base, 3.0))
                # Unknown URLs
                for feed_url in feed_urls:
                    if feed_url not in result:
                        result[feed_url] = SCORE_UNKNOWN
                return result
            except Exception:  # noqa: BLE001
                pass  # noqa: BLE001  # Fall through to pure-Python

        # Pure-Python batch: single pass over all feed_urls
        result: dict[str, float] = {}
        for feed_url in feed_urls:
            result[feed_url] = self._compute_source_score(feed_url, current_cycle)
        return result
