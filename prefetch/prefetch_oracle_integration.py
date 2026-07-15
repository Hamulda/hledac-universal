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
from dataclasses import dataclass
import msgspec
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    pass
logger = logging.getLogger(__name__)
_dduckdb = None

def _get_duckdb():
    global _dduckdb
    if _dduckdb is None:
        import duckdb as _dd
        _dduckdb = _dd
    return _dduckdb
# ISSUE-025: Use core.rust_backend for consistency (fail-soft, single import point)
# Previously: direct hledac_rust_extensions imports with individual try/except blocks.
_rust_signal_domain: Any = None

def _get_rust_signal_domain():
    """Lazy access to Rust signal domain — batch_compute_scores, batch_aggregate_signals."""
    global _rust_signal_domain
    if _rust_signal_domain is None:
        try:
            from core.rust_backend import rust
            _rust_signal_domain = rust.signal if rust.is_available else None
        except Exception:
            _rust_signal_domain = None
    return _rust_signal_domain

_rust_federated_domain: Any = None

def _get_rust_federated_domain():
    """Lazy access to Rust federated domain — RustFederatedQTable."""
    global _rust_federated_domain
    if _rust_federated_domain is None:
        try:
            from core.rust_backend import rust
            _rust_federated_domain = rust.federated if rust.is_available else None
        except Exception:
            _rust_federated_domain = None
    return _rust_federated_domain
# NOTE: batch_graph_traverse (standalone) is imported directly from hledac_rust_extensions
# at line ~405 because it has different signature than rust.graph.batch_graph_traverse domain method.
# The standalone function: (db_path, values, max_hops) → dict[str, list[dict]]
# The domain method: (root_ids, graph_path, max_depth, direction) → list[dict]
# These are NOT interchangeable - they have completely different APIs.

MAX_CANDIDATES = 100
MAX_SOURCE_HISTORY = 200
MAX_URL_SEEN = 50000
SCORE_NEUTRAL = 1.0
SCORE_HOT = 1.3
SCORE_WARM = 1.1
SCORE_LUKEWARM = 1.0
SCORE_MARGINAL = 0.8
SCORE_COLD = 0.6
SCORE_UNKNOWN = 1.0
RECENCY_BONUS_PER_CYCLE = 0.05
RECENCY_BONUS_MAX = 0.3
NOVELTY_BONUS = 0.15

@dataclass(slots=True)
class _SourceSignal:
    """Per-source signal tracking (bounded)."""
    feed_url: str
    fetched: int = 0
    accepted: int = 0
    cycles_active: int = 0
    last_cycle: int = -1
    seen_urls: int = 0

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
    __slots__ = tuple(('_cache_cycle', '_duckdb_conn', '_duckdb_executor', '_duckdb_store', '_ioc_graph', '_max_prefetched_iocs', '_max_seen_urls', '_prefetch_ttl_s', '_prefetched_iocs', '_score_cache', '_seen_urls', '_source_signals', '_stats', 'max_candidates', 'max_source_history', 'novety_bonus', 'recency_bonus_max', 'recency_bonus_per_cycle'))

    def __init__(self, max_candidates: int=MAX_CANDIDATES, max_source_history: int=MAX_SOURCE_HISTORY, novelty_bonus: float=NOVELTY_BONUS, recency_bonus_per_cycle: float=RECENCY_BONUS_PER_CYCLE, recency_bonus_max: float=RECENCY_BONUS_MAX):
        self.max_candidates = max_candidates
        self.max_source_history = max_source_history
        self.novety_bonus = novelty_bonus
        self.recency_bonus_per_cycle = recency_bonus_per_cycle
        self.recency_bonus_max = recency_bonus_max
        self._source_signals: OrderedDict[str, _SourceSignal] = OrderedDict()
        self._seen_urls: OrderedDict[str, float] = OrderedDict()
        self._max_seen_urls = MAX_URL_SEEN
        self._score_cache: dict[str, float] = {}
        self._cache_cycle: int = -1
        self._duckdb_conn: Any = None
        self._ioc_graph: Any = None
        self._duckdb_store: Any = None
        self._prefetched_iocs: OrderedDict[str, float] = OrderedDict()
        self._max_prefetched_iocs: int = 5000
        self._prefetch_ttl_s: float = 300.0
        self._stats = {'suggestions_made': 0, 'cache_hits': 0, 'cache_misses': 0, 'duckdb_historical_queries': 0, 'predict_next_iocs_calls': 0, 'predict_next_iocs_items': 0, 'graph_traversals': 0}

    def suggest_scores(self, work_items: list[Any], current_cycle: int=0) -> dict[str, float]:
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
        except RuntimeError:
            # No running loop — bridge to async via run_sync_async (M1 Metal safe).
            from utils.sync_bridge import run_sync_async
            return run_sync_async(self.suggest_scores_async(work_items, current_cycle))
        except Exception:
            return self._suggest_scores_sequential(work_items, current_cycle)
        # Running loop detected — delegate to async version via run_coroutine_threadsafe.
        # This avoids M1 Metal crash that asyncio.run() would cause inside an existing loop.
        try:

            import asyncio as _asyncio
            future = _asyncio.run_coroutine_threadsafe(
                self.suggest_scores_async(work_items, current_cycle), loop
            )
            return future.result()
        except Exception:
            return self._suggest_scores_sequential(work_items, current_cycle)

    async def suggest_scores_async(self, work_items: list[Any], current_cycle: int=0) -> dict[str, float]:
        """
        P1-1: Async version — parallel score computation via safe_gather_ok.

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
            items_to_score = work_items[:self.max_candidates]
            scores: dict[str, float] = {}
            uncached_urls: list[str] = []
            for item in items_to_score:
                feed_url = getattr(item, 'feed_url', None)
                if not feed_url:
                    continue
                if feed_url in self._score_cache:
                    scores[feed_url] = self._score_cache[feed_url]
                    self._stats['cache_hits'] += 1
                else:
                    uncached_urls.append(feed_url)
            if uncached_urls:
                try:
                    batch_scores = self._compute_source_score_batch(uncached_urls, current_cycle)
                    for feed_url, score in batch_scores.items():
                        scores[feed_url] = score
                        self._score_cache[feed_url] = score
                        self._stats['cache_misses'] += 1
                except Exception as e:
                    logger.debug(f'[P0-2] batch scoring failed, sequential fallback: {e}')
                    for feed_url in uncached_urls:
                        score = self._compute_source_score(feed_url, current_cycle)
                        scores[feed_url] = score
                        self._score_cache[feed_url] = score
                        self._stats['cache_misses'] += 1
            self._stats['suggestions_made'] += 1
            return scores
        except Exception:
            logger.debug('[P1-1] suggest_scores_async failed')
            return {}

    def _suggest_scores_sequential(self, work_items: list[Any], current_cycle: int=0) -> dict[str, float]:
        """Sequential fallback scoring."""
        try:
            if not work_items:
                return {}
            if current_cycle != self._cache_cycle:
                self._score_cache.clear()
                self._cache_cycle = current_cycle
            scores: dict[str, float] = {}
            for item in work_items[:self.max_candidates]:
                feed_url = getattr(item, 'feed_url', None)
                if not feed_url:
                    continue
                if feed_url in self._score_cache:
                    scores[feed_url] = self._score_cache[feed_url]
                    self._stats['cache_hits'] += 1
                    continue
                score = self._compute_source_score(feed_url, current_cycle)
                scores[feed_url] = score
                self._score_cache[feed_url] = score
                self._stats['cache_misses'] += 1
            self._stats['suggestions_made'] += 1
            return scores
        except Exception:
            return {}

    def record_outcome(self, feed_url: str, fetched: int, accepted: int, cycle: int, seen_new_urls: int=0) -> None:
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
                self._source_signals.move_to_end(feed_url)
            else:
                if len(self._source_signals) >= self.max_source_history:
                    evicted_url, _ = self._source_signals.popitem(last=False)
                    logger.debug(f'[F200A] LRU evicting source: {evicted_url}')
                    self._score_cache.pop(evicted_url, None)
                self._source_signals[feed_url] = _SourceSignal(feed_url=feed_url, fetched=fetched, accepted=accepted, cycles_active=1, last_cycle=cycle, seen_urls=seen_new_urls)
        except Exception:
            logger.debug(f'[F200A] record_outcome failed for {feed_url}')

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
            logger.debug(f'[F200A] record_url_seen failed for {url}')

    def get_stats(self) -> dict[str, Any]:
        """Return oracle statistics (for diagnostics)."""
        return {**self._stats, 'sources_tracked': len(self._source_signals), 'urls_tracked': len(self._seen_urls), 'cache_size': len(self._score_cache)}

    def reset(self) -> None:
        """Reset all state (called at sprint teardown)."""
        self._source_signals.clear()
        self._seen_urls.clear()
        self._score_cache.clear()
        self._cache_cycle = -1
        self._prefetched_iocs.clear()
        self._stats = {'suggestions_made': 0, 'cache_hits': 0, 'cache_misses': 0, 'duckdb_historical_queries': 0, 'predict_next_iocs_calls': 0, 'predict_next_iocs_items': 0, 'graph_traversals': 0}

    def inject_duckdb_conn(self, conn: Any) -> None:
        """
        P1-2: Inject DuckDB connection for historical yield queries.

        Runs DuckDB historical queries via asyncio.to_thread to avoid
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

    async def predict_next_iocs(self, top_k: int=10) -> list[dict]:
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
            self._stats['predict_next_iocs_calls'] += 1
            recent_findings: list[Any] = []
            try:
                store = getattr(self, '_duckdb_store', None)
                if store is not None and hasattr(store, 'async_get_recent_findings'):
                    recent_findings = await store.async_get_recent_findings(limit=1000)
                elif store is not None and hasattr(store, 'get_recent_findings'):
                    recent_findings = await asyncio.to_thread(store.get_recent_findings, limit=1000)
            except Exception as e:
                logger.debug(f'[P3-1] Failed to get recent findings: {e}')
                return []
            if not recent_findings:
                return []
            candidates: dict[str, dict] = {}
            now = time.time()
            for finding in recent_findings:
                ioc_value: str | None = None
                ioc_type: str = 'unknown'
                for attr in ('value', 'ioc_value', 'domain', 'url', 'ip', 'hash', 'finding_id'):
                    val = getattr(finding, attr, None)
                    if isinstance(val, str) and val:
                        ioc_value = val
                        break
                if not ioc_value:
                    continue
                source_type = getattr(finding, 'source_type', '')
                if 'domain' in source_type or (ioc_type == 'unknown' and '.' in ioc_value and (not ioc_value.startswith('http'))):
                    ioc_type = 'domain'
                elif 'url' in source_type or ioc_value.startswith(('http://', 'https://')):
                    ioc_type = 'url'
                elif 'ip' in source_type:
                    ioc_type = 'ip'
                elif 'hash' in source_type:
                    ioc_type = 'hash'
                if ioc_value in self._prefetched_iocs:
                    last_ts = self._prefetched_iocs[ioc_value]
                    if now - last_ts < self._prefetch_ttl_s:
                        continue
                confidence = float(getattr(finding, 'confidence', 0.5))
                found_ts = float(getattr(finding, 'ts', now))
                if ioc_value not in candidates or candidates[ioc_value]['confidence'] < confidence:
                    candidates[ioc_value] = {'ioc_value': ioc_value, 'ioc_type': ioc_type, 'confidence': confidence, 'source_node': 'duckdb_recent', 'prediction_method': 'duckdb_recent', 'found_ts': found_ts}
            graph_candidates: dict[str, dict] = {}
            try:
                graph = getattr(self, '_ioc_graph', None)
                if graph is not None and hasattr(graph, 'find_connected'):
                    self._stats['graph_traversals'] += 1
                    top_sources = sorted(candidates.values(), key=lambda x: x['confidence'], reverse=True)[:20]
                    db_path = getattr(graph, 'db_path', None)
                    if db_path and hasattr(graph, 'find_connected_batch'):
                        source_values = [src['ioc_value'] for src in top_sources]

                        def _batch_traverse_sync() -> dict[str, list[dict]]:
                            """Sync batch traversal — runs in thread pool via asyncio.to_thread."""
                            try:
                                from hledac_rust_extensions import batch_graph_traverse
                                raw = batch_graph_traverse(db_path, source_values, 2)
                                if raw is not None:
                                    return dict(raw)
                            except Exception:
                                pass
                            return graph.find_connected_batch(source_values, max_hops=2)
                        batch_results: dict[str, list[dict]] = await asyncio.to_thread(_batch_traverse_sync)
                        for src in top_sources:
                            src_value = src['ioc_value']
                            connected = batch_results.get(src_value, [])
                            for node in connected[:5]:
                                node_value = node.get('value') or node.get('ioc_value')
                                node_type = node.get('type', src['ioc_type'])
                                node_conf = float(node.get('confidence', 0.5))
                                if not node_value or node_value in self._prefetched_iocs:
                                    continue
                                if now - self._prefetched_iocs.get(node_value, 0) < self._prefetch_ttl_s:
                                    continue
                                recency_bonus = 1.0 + max(0, (now - src['found_ts']) / 86400) * 0.1
                                score = src['confidence'] * node_conf * recency_bonus
                                if node_value not in graph_candidates or graph_candidates[node_value]['confidence'] < score:
                                    graph_candidates[node_value] = {'ioc_value': node_value, 'ioc_type': node_type, 'confidence': min(score, 1.0), 'source_node': src_value, 'prediction_method': 'graph_traversal', 'found_ts': now}
                    else:
                        legacy_source_values = [src['ioc_value'] for src in top_sources]

                        def _legacy_batch_sync() -> dict[str, list[dict]]:
                            """Python batch traversal — no Rust dependency."""
                            try:
                                return graph.find_connected_batch(legacy_source_values, max_hops=2)
                            except Exception:
                                return {}
                        batch_results: dict[str, list[dict]] = await asyncio.to_thread(_legacy_batch_sync)
                        for src in top_sources:
                            src_value = src['ioc_value']
                            connected = batch_results.get(src_value, [])
                            for node in connected[:5]:
                                node_value = node.get('value') or node.get('ioc_value')
                                node_type = node.get('type', src['ioc_type'])
                                node_conf = float(node.get('confidence', 0.5))
                                if not node_value or node_value in self._prefetched_iocs:
                                    continue
                                if now - self._prefetched_iocs.get(node_value, 0) < self._prefetch_ttl_s:
                                    continue
                                recency_bonus = 1.0 + max(0, (now - src['found_ts']) / 86400) * 0.1
                                score = src['confidence'] * node_conf * recency_bonus
                                if node_value not in graph_candidates or graph_candidates[node_value]['confidence'] < score:
                                    graph_candidates[node_value] = {'ioc_value': node_value, 'ioc_type': node_type, 'confidence': min(score, 1.0), 'source_node': src_value, 'prediction_method': 'graph_traversal', 'found_ts': now}
            except Exception as e:
                logger.debug(f'[P3-1] Graph access failed: {e}')
            all_candidates = {**candidates, **graph_candidates}
            sorted_candidates = sorted(all_candidates.values(), key=lambda x: x['confidence'], reverse=True)[:top_k]
            for cand in sorted_candidates:
                self._prefetched_iocs[cand['ioc_value']] = now
                if len(self._prefetched_iocs) > self._max_prefetched_iocs:
                    self._prefetched_iocs.popitem(last=False)
            self._stats['predict_next_iocs_items'] += len(sorted_candidates)
            return sorted_candidates
        except Exception as e:
            logger.debug(f'[P3-1] predict_next_iocs failed: {e}')
            return []

    def record_prefetch_outcome(self, ioc_value: str, success: bool=True, _bytes_downloaded: int=0, lane: str='surface', state_key: str='prelude', next_state_key: str='prelude') -> None:
        """
        P3-1: Record the outcome of a prefetch attempt.

        ISSUE #009: Also updates Rust FederatedQTable for Q-guided prefetch.
        Rust Q-table learn() from prefetch success/failure — improves future
        prefetch ordering via rayon's DashMap-backed parallel updates.

        Args:
            ioc_value: The IOC that was prefetched.
            success: True if fetch succeeded and data was cached.
            _bytes_downloaded: Bytes downloaded (reserved for future bandwidth accounting).
            lane: Q-table lane ('surface', 'dark', etc.)
            state_key: Current state key for Q-learning.
            next_state_key: Next state key for Q-learning.
        """
        try:
            self._prefetched_iocs[ioc_value] = time.time()
            if len(self._prefetched_iocs) > self._max_prefetched_iocs:
                self._prefetched_iocs.popitem(last=False)
        except Exception:
            pass
        # ISSUE #009: Rust Q-table update — reward = +1 on success, -0.1 on failure
        try:
            domain = _get_rust_federated_domain()
            if domain is not None:
                # ISSUE-025: Use rust.federated domain (singleton qtable cached in domain)
                qtable = domain.RustFederatedQTable(alpha=0.1, gamma=0.9, max_entries=1024)
                reward = 1.0 if success else -0.1
                action = f'prefetch:{ioc_value[:32]}'
                # update() is lock-free via DashMap — no global lock contention
                qtable.update(lane, state_key, action, reward, next_state_key)
        except Exception:
            pass

    def get_best_prefetch_actions(self, candidates: list[str], lane: str='surface', state_key: str='prelude', top_k: int=5) -> list[str]:
        """
        ISSUE #009 + ISSUE A fix: Return top-K prefetch actions guided by Rust FederatedQTable.

        Uses RustFederatedQTable.get_best_action() which is lock-free (DashMap)
        for reading Q-values and rayon-parallel for batch updates.

        Args:
            candidates: List of IOC values to rank.
            lane: Q-table lane.
            state_key: Current state key for Q-learning.
            top_k: Number of top actions to return.

        Returns:
            List of top-K IOC values sorted by Q-value descending.
            Returns candidates as-is if Rust Q-table unavailable (fail-soft).

        ISSUE A fix:
            - Deduplication uses exact match (==) instead of startswith.
              With 32-char hex strings of equal length, startswith was accidentally
              correct by coincidence — == is semantically correct.
            - Empty candidates returns state_key as single best (degrades gracefully).
        """
        try:
            domain = _get_rust_federated_domain()
            if domain is None:
                return candidates[:top_k]
            # Empty candidates: return state_key as placeholder best action
            if not candidates:
                return [state_key] if top_k >= 1 else []
            # Build action list: 'prefetch:{ioc_value}' for each candidate
            actions = [f'prefetch:{c}' for c in candidates[:20]]
            if not actions:
                return []
            # get_best_action is lock-free — DashMap per-shard read lock
            qtable = domain.RustFederatedQTable(alpha=0.1, gamma=0.9, max_entries=1024)
            best = qtable.get_best_action(lane, state_key, actions)
            # Extract IOC value from action name: 'prefetch:{ioc_value}'
            if best.startswith('prefetch:'):
                ioc_value = best[len('prefetch:'):]
                # Deduplication: exact match (IOC values are full strings, not prefixes)
                seen = {ioc_value}
                ranked = [ioc_value]
                for c in candidates[:top_k]:
                    if c not in seen:
                        seen.add(c)
                        ranked.append(c)
                return ranked[:top_k]
            return candidates[:top_k]
        except Exception:
            return candidates[:top_k]

    async def _query_historical_yield_async(self, feed_url: str, cycles_back: int=20) -> float:
        """
        P1-2: Query DuckDB for historical yield patterns across sprints.

        Runs DuckDB query via asyncio.to_thread to avoid blocking the event loop.

        Returns:
            float yield ratio in range [0.0, 1.0], or -1.0 if unavailable.
        """
        try:
            conn = getattr(self, '_duckdb_conn', None)
            if conn is None:
                return -1.0
            def _query_sync() -> float:
                """Blocking DuckDB query — runs in thread pool."""
                try:
                    _get_duckdb()
                    q = "\n                    SELECT COALESCE(\n                        SUM(accepted)::DOUBLE / NULLIF(SUM(fetched), 0),\n                        -1.0\n                    ) AS yield_ratio\n                    FROM (\n                        SELECT\n                            feed_url,\n                            SUM(CASE WHEN finding_id IS NOT NULL THEN 1 ELSE 0 END) AS accepted,\n                            SUM(CASE WHEN feed_url IS NOT NULL THEN 1 ELSE 0 END) AS fetched\n                        FROM sprint_findings\n                        WHERE feed_url = ?\n                          AND cycle_ts > NOW() - INTERVAL '1 hours' * ?\n                        GROUP BY feed_url\n                    ) sub;\n                    "
                    result = conn.execute(q, [feed_url, cycles_back]).fetchone()
                    if result is None:
                        return -1.0
                    return float(result[0]) if result[0] is not None else -1.0
                except Exception:
                    return -1.0

            ratio = await asyncio.to_thread(_query_sync)
            self._stats['duckdb_historical_queries'] += 1
            return ratio
        except Exception:
            return -1.0

    async def query_historical_yield_batch_async(self, feed_urls: list[str], cycles_back: int=20) -> dict[str, float]:
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
            conn = getattr(self, '_duckdb_conn', None)
            if conn is None:
                return {}
            if self._duckdb_executor is None:
                from utils.domain_executors import get_duckdb_executor
                self._duckdb_executor = get_duckdb_executor()

            def _query_sync() -> dict[str, float]:
                """Single blocking DuckDB query for all sources — runs in thread pool."""
                try:
                    _get_duckdb()
                    placeholders = ','.join(['?'] * len(feed_urls))
                    q = f"\n                    SELECT\n                        feed_url,\n                        COALESCE(\n                            SUM(CASE WHEN finding_id IS NOT NULL THEN 1 ELSE 0 END)::DOUBLE\n                                / NULLIF(SUM(CASE WHEN feed_url IS NOT NULL THEN 1 ELSE 0 END), 0),\n                            -1.0\n                        ) AS yield_ratio\n                    FROM sprint_findings\n                    WHERE feed_url IN ({placeholders})\n                      AND cycle_ts > NOW() - INTERVAL '1 hours' * ?\n                    GROUP BY feed_url;\n                    "
                    rows = conn.execute(q, [*feed_urls, cycles_back]).fetchall()
                    return {row[0]: float(row[1]) if row[1] is not None else -1.0 for row in rows}
                except Exception:
                    return {}

            result = await asyncio.to_thread(_query_sync)
            self._stats['duckdb_historical_queries'] += 1
            return result
        except Exception:
            return {}

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
        recency_bonus = 0.0
        if signal.last_cycle >= 0 and current_cycle > signal.last_cycle:
            cycles_since = current_cycle - signal.last_cycle
            recency_bonus = min(cycles_since * self.recency_bonus_per_cycle, self.recency_bonus_max)
        novelty_bonus = 0.0
        if signal.seen_urls > 0 and signal.cycles_active > 0:
            avg_urls_per_cycle = signal.seen_urls / signal.cycles_active
            if avg_urls_per_cycle > 5:
                novelty_bonus = self.novety_bonus
        score = yield_score + recency_bonus + novelty_bonus
        return max(0.1, min(score, 3.0))

    def _compute_source_score_batch(self, feed_urls: list[str], current_cycle: int) -> dict[str, float]:
        """
        P0-2: Batch source scoring — all signals in one pass.

        Builds stats list for Rust batch_compute_scores (F199A NEON path) when
        Rust is available; falls back to pure-Python loop when not.
        Uses asyncio.to_thread for non-blocking execution via DuckDB executor.

        Args:
            feed_urls: list of feed URLs to score
            current_cycle: current sprint cycle (for recency bonus)

        Returns:
            {feed_url: score} for all feed_urls with known signals;
            unknown URLs use SCORE_UNKNOWN.
        """
        domain = _get_rust_signal_domain()
        if domain is not None:
            url_order: list[str] = []
            stats_list: list[dict[str, object]] = []
            for feed_url in feed_urls:
                signal = self._source_signals.get(feed_url)
                if signal is None:
                    continue
                url_order.append(feed_url)
                stats_list.append({'fetched': int(signal.fetched), 'accepted': int(signal.accepted), 'current_weight': 1.0, 'novelty': signal.seen_urls > 0 and signal.cycles_active > 0 and (signal.seen_urls / max(1, signal.cycles_active) > 5)})
            if not stats_list:
                return dict.fromkeys(feed_urls, SCORE_UNKNOWN)
            try:
                raw_weights: list[float] = domain.batch_compute_scores(stats_list)
                result: dict[str, float] = {}
                for i, feed_url in enumerate(url_order):
                    delta = raw_weights[i]
                    base = SCORE_NEUTRAL
                    result[feed_url] = max(0.1, min(delta * base, 3.0))
                for feed_url in feed_urls:
                    if feed_url not in result:
                        result[feed_url] = SCORE_UNKNOWN
                return result
            except Exception:
                pass
        result: dict[str, float] = {}
        for feed_url in feed_urls:
            result[feed_url] = self._compute_source_score(feed_url, current_cycle)
        return result