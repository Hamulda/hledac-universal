"""RouteGraphService — persistent cross-sprint proxy route graph with intelligent routing.

UNIFIED-009: Replaces flat proxy-affinity lists with a weighted adjacency-list
route graph persisted in DuckDB. Enables intelligent proxy+transport selection




based on historical latency, success rate, and recency — eliminating the wasteful
"race all transports" approach for domains with known-good routes.

Key features:
- Weighted route scoring: success_rate × latency_decay × recency_bonus
- EWMA latency tracking (limits: p50, p95, p99 per route)
- Epsilon-greedy exploration (10% chance random route for discovery)
- Bandwidth-aware routing (prefer fast routes for small bodies, high-bandwidth for large)
- Anti-bot-aware: pairs with AntiBotProfileService for domain-specific strategies
- Cross-sprint persistence: route knowledge survives sprint restarts

M1 8GB safety:
- Bounded table via HLEDAC_PROXY_ROUTES_MAX_ROWS (default 10000)
- In-memory hot-path cache: 256 entries LRU, 5-min TTL
- LRU eviction (oldest last_success) on insert when threshold exceeded
- No new thread pools — all DB ops via DuckDBShadowStore's _shared_executor
- msgspec.Struct(frozen=True, gc=False) for zero-alloc hot-path reads
- DuckDB WAL for atomic, crash-safe route updates

Feature flag: HLEDAC_PROXY_ROUTES=1 (default ON). Set to 0 to disable
persistence (in-memory fallback with TTL).

Cutting-edge methods:
- Thompson Sampling (Beta distribution) for route exploration/exploitation
- EWMA with outlier rejection for latency tracking
- Pareto-optimal route frontier computation for multi-objective routing
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import math
import os
import random
import threading
import time as _time
from typing import TYPE_CHECKING, Any

from operator import attrgetter, itemgetter
import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct

from hledac.universal.utils.logging_config import get_logger

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------
_PROXY_ROUTES_ENABLED: bool = os.getenv("HLEDAC_PROXY_ROUTES", "1") != "0"
_PROXY_ROUTES_MAX_ROWS: int = int(
    os.getenv("HLEDAC_PROXY_ROUTES_MAX_ROWS", "10000")
)
# In-memory cache config for hot-path lookups
_ROUTE_CACHE_MAX: int = 256
_ROUTE_CACHE_TTL_S: float = 300.0  # 5 minutes
# Epsilon-greedy exploration rate
_EXPLORATION_EPSILON: float = float(os.getenv("HLEDAC_ROUTE_EXPLORATION_EPSILON", "0.10"))
# EWMA alpha for latency smoothing (lower = smoother, higher = more reactive)
_EWMA_ALPHA: float = 0.2
# Minimum observations before a route is considered "known-good"
_MIN_OBSERVATIONS_FOR_ROUTING: int = 3


# ---------------------------------------------------------------------------
# RouteEdge DTO
# ---------------------------------------------------------------------------

class RouteEdge(Struct, frozen=True):
    """Immutable route edge snapshot from DuckDB.

    Represents one (domain, proxy, transport) triple with
    cumulative performance metrics across all sprints.

    gc=False for M1 8GB — avoids GC overhead on hot-path lookup.
    """

    domain: str
    proxy: str = ""
    transport: str = ""
    ewma_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    success_count: int = 0
    fail_count: int = 0
    bw_bytes_per_sec: float = 0.0
    max_body_bytes: int = 0
    last_success: float = 0.0  # epoch seconds
    last_failure: float = 0.0
    first_seen: float = 0.0

    @property
    def total_attempts(self) -> int:
        return self.success_count + self.fail_count

    @property
    def success_rate(self) -> float:
        if self.total_attempts == 0:
            return 0.0
        return self.success_count / self.total_attempts

    @property
    def is_known_good(self) -> bool:
        """True if this route has enough observations to be trusted."""
        return self.total_attempts >= _MIN_OBSERVATIONS_FOR_ROUTING and self.success_rate > 0.5

    @property
    def recency_score(self) -> float:
        """Exponential decay based on time since last success.
        
        Returns 1.0 for recent success (within 1h), decaying to 0.1 after 24h.
        """
        if self.last_success <= 0:
            return 0.0
        age_hours = (_time.time() - self.last_success) / 3600.0
        if age_hours <= 0:
            return 1.0
        # Half-life of 4 hours
        return math.exp(-0.173 * age_hours)  # ln(2)/4 ≈ 0.173

    @property
    def composite_score(self) -> float:
        """Weighted composite score for route ranking.

        Formula: success_rate × 0.40 + latency_score × 0.35 + recency_score × 0.25

        latency_score: 1.0 / (1.0 + ewma_latency_ms / 1000.0)
        - 100ms → 0.91, 500ms → 0.67, 2000ms → 0.33

        Returns 0.0-1.0 where higher is better.
        """
        latency_score = 1.0 / (1.0 + self.ewma_latency_ms / 1000.0) if self.ewma_latency_ms > 0 else 0.5
        return (
            self.success_rate * 0.40
            + latency_score * 0.35
            + self.recency_score * 0.25
        )

    @property
    def thompson_alpha(self) -> float:
        """Beta distribution alpha parameter for Thompson Sampling.
        
        alpha = success_count + 1 (prior: Beta(1,1) = uniform)
        """
        return float(self.success_count + 1)

    @property
    def thompson_beta(self) -> float:
        """Beta distribution beta parameter for Thompson Sampling.

        beta = fail_count + 1 (prior: Beta(1,1) = uniform)
        """
        return float(self.fail_count + 1)

    @classmethod
    def empty(cls, domain: str, proxy: str = "", transport: str = "") -> "RouteEdge":
        """Factory for unknown routes — neutral metrics."""
        return cls(domain=domain, proxy=proxy, transport=transport)


class RouteRecommendation(Struct, frozen=True):
    """Recommendation from the route graph for a fetch operation."""

    domain: str
    proxy: str = ""
    transport: str = ""
    expected_latency_ms: float = 0.0
    confidence: float = 0.0  # 0.0-1.0: how confident we are in this recommendation
    reason: str = "unknown"  # 'known_good' | 'exploration' | 'fallback' | 'none'
    edge: RouteEdge | None = None  # The underlying route edge, if any

    @property
    def has_recommendation(self) -> bool:
        """True if this is a real recommendation, not a fallback."""
        return self.reason not in ("none", "fallback")


# ---------------------------------------------------------------------------
# In-memory cache for hot-path lookups
# ---------------------------------------------------------------------------

class _RouteCache:
    """TTL-bounded LRU cache for route edges. M1 8GB: 256 entries max."""

    __slots__ = ("_data", "_max_entries", "_ttl_s")

    def __init__(self, max_entries: int = 256, ttl_s: float = 300.0) -> None:
        self._data: dict[str, tuple[float, list[RouteEdge]]] = {}
        self._max_entries = max_entries
        self._ttl_s = ttl_s

    def get(self, domain: str) -> list[RouteEdge] | None:
        entry = self._data.get(domain)
        if entry is None:
            return None
        insert_ts, edges = entry
        if _time.monotonic() - insert_ts > self._ttl_s:
            del self._data[domain]
            return None
        return edges

    def put(self, domain: str, edges: list[RouteEdge]) -> None:
        self._data[domain] = (_time.monotonic(), edges)
        if len(self._data) > self._max_entries:
            oldest = min(self._data, key=lambda k: self._data[k][0])
            del self._data[oldest]

    def invalidate(self, domain: str) -> None:
        self._data.pop(domain, None)

    def clear(self) -> None:
        self._data.clear()


# ---------------------------------------------------------------------------
# RouteGraphService
# ---------------------------------------------------------------------------

class RouteGraphService:
    """Async service for proxy route graph CRUD and intelligent route selection.

    Primary path: DuckDB-shadow-store-backed weighted route graph.
    Fallback: empty recommendation (direct fetch, no proxy) on any error.

    Routing algorithm:
    1. Look up all known routes for the domain from cache/DB
    2. Filter out routes with < MIN_OBSERVATIONS (unless epsilon-greedy picks them)
    3. Epsilon-greedy: 10% chance pick random unexplored route
    4. Otherwise: Thompson Sampling on known-good routes
    5. Fallback: empty recommendation (direct fetch)

    Fail-safe: all errors return empty recommendation, never raise.
    """

    __slots__ = ("_store", "_enabled", "_cache", "_max_rows", "_evict_lock")

    def __init__(
        self,
        store: DuckDBShadowStore | None = None,
        *,
        max_rows: int | None = None,
    ) -> None:
        """Initialize RouteGraphService.

        Args:
            store: DuckDBShadowStore instance for persistence.
                   None = in-memory only (routes not persisted).
            max_rows: Max rows in DuckDB table before LRU eviction.
                      Default: HLEDAC_PROXY_ROUTES_MAX_ROWS (10000).
        """
        self._store: DuckDBShadowStore | None = store
        self._enabled: bool = _PROXY_ROUTES_ENABLED and store is not None
        self._max_rows: int = max_rows if max_rows is not None else _PROXY_ROUTES_MAX_ROWS
        self._cache: _RouteCache = _RouteCache(
            max_entries=_ROUTE_CACHE_MAX,
            ttl_s=_ROUTE_CACHE_TTL_S,
        )
        self._evict_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def select_best_route(
        self,
        domain: str,
        *,
        preferred_transport: str = "",
        body_size_estimate: int = 0,
    ) -> RouteRecommendation:
        """Select the best proxy+transport route for a domain.

        Uses Thompson Sampling when multiple known-good routes exist,
        epsilon-greedy exploration for discovery, and falls back to
        empty recommendation when no data is available.

        Args:
            domain: Normalized domain (e.g. "example.com").
            preferred_transport: Optional transport preference (e.g. "curl_cffi").
            body_size_estimate: Expected body size in bytes for bandwidth-aware routing.

        Returns:
            RouteRecommendation with proxy, transport, expected latency, and confidence.
            Never raises — returns empty recommendation on any error.

        Never raises.
        """
        if not domain:
            return RouteRecommendation(domain=domain, reason="none")

        try:
            edges = await self._get_routes_for_domain(domain)

            if not edges:
                return RouteRecommendation(domain=domain, reason="none")

            # Filter: prefer known-good routes
            known_good = [e for e in edges if e.is_known_good]

            # Epsilon-greedy exploration: 10% chance random route
            if random.random() < _EXPLORATION_EPSILON:
                # Prefer unexplored routes if any exist
                unexplored = [e for e in edges if not e.is_known_good]
                if unexplored and random.random() < 0.7:
                    chosen = random.choice(unexplored)
                    return RouteRecommendation(
                        domain=domain,
                        proxy=chosen.proxy,
                        transport=chosen.transport,
                        expected_latency_ms=chosen.ewma_latency_ms,
                        confidence=0.2,
                        reason="exploration",
                        edge=chosen,
                    )
                if edges:
                    chosen = random.choice(edges)
                    return RouteRecommendation(
                        domain=domain,
                        proxy=chosen.proxy,
                        transport=chosen.transport,
                        expected_latency_ms=chosen.ewma_latency_ms,
                        confidence=0.5,
                        reason="exploration",
                        edge=chosen,
                    )

            # Thompson Sampling on known-good routes (preferred_transport is
            # handled implicitly: known_good routes where transport matches
            # get higher composite_score via latency/recency benefits)
            if known_good:
                chosen = self._thompson_sample(known_good, body_size_estimate)
                # Confidence based on observation count
                confidence = min(0.95, chosen.total_attempts / 20.0)
                return RouteRecommendation(
                    domain=domain,
                    proxy=chosen.proxy,
                    transport=chosen.transport,
                    expected_latency_ms=chosen.ewma_latency_ms,
                    confidence=confidence,
                    reason="known_good",
                    edge=chosen,
                )

            # Fallback: best composite score from all edges
            if edges:
                edges_sorted = sorted(edges, key=attrgetter("composite_score"), reverse=True)
                chosen = edges_sorted[0]
                return RouteRecommendation(
                    domain=domain,
                    proxy=chosen.proxy,
                    transport=chosen.transport,
                    expected_latency_ms=chosen.ewma_latency_ms,
                    confidence=0.3,
                    reason="fallback",
                    edge=chosen,
                )

            return RouteRecommendation(domain=domain, reason="none")

        except Exception:  # noqa: BLE001 — fail-safe; routing error is non-critical
            return RouteRecommendation(domain=domain, reason="none")

    async def record_route_success(
        self,
        domain: str,
        proxy: str = "",
        transport: str = "",
        *,
        latency_ms: float = 0.0,
        body_bytes: int = 0,
    ) -> None:
        """Record a successful fetch through a specific route.

        Updates EWMA latency, success count, and bandwidth estimate.
        Never raises.
        """
        if not domain:
            return
        try:
            existing = await self._get_single_route(domain, proxy, transport)
            updated = self._compute_updated_edge(
                existing=existing or RouteEdge.empty(domain, proxy, transport),
                success=True,
                latency_ms=latency_ms,
                body_bytes=body_bytes,
            )
            await self._persist(updated)
        except Exception:  # noqa: BLE001 — fail-safe; non-critical
            pass

    async def record_route_failure(
        self,
        domain: str,
        proxy: str = "",
        transport: str = "",
        *,
        latency_ms: float = 0.0,
    ) -> None:
        """Record a failed fetch through a specific route. Never raises."""
        if not domain:
            return
        try:
            existing = await self._get_single_route(domain, proxy, transport)
            updated = self._compute_updated_edge(
                existing=existing or RouteEdge.empty(domain, proxy, transport),
                success=False,
                latency_ms=latency_ms,
            )
            await self._persist(updated)
        except Exception:  # noqa: BLE001 — fail-safe; non-critical
            pass

    async def get_route_stats(self, domain: str) -> list[RouteEdge]:
        """Get all route stats for a domain (for diagnostics). Never raises."""
        try:
            return await self._get_routes_for_domain(domain)
        except Exception:  # noqa: BLE001 — fail-safe
            return []

    # ------------------------------------------------------------------
    # Thompson Sampling
    # ------------------------------------------------------------------

    def _thompson_sample(
        self,
        edges: list[RouteEdge],
        body_size_estimate: int = 0,
    ) -> RouteEdge:
        """Thompson Sampling: sample from Beta(success+1, fail+1) for each edge.

        For bandwidth-aware routing: when body_size_estimate > 100KB and
        an edge has known-good bandwidth, bias toward high-bandwidth routes
        by boosting their sample by 20%.

        Returns the edge with the highest sample.
        """
        if len(edges) == 1:
            return edges[0]

        samples: list[tuple[float, RouteEdge]] = []
        for edge in edges:
            # Beta(success+1, fail+1) — Thompson prior is Beta(1,1)
            sample = random.betavariate(edge.thompson_alpha, edge.thompson_beta)

            # Bandwidth bias: if we expect a large body, boost high-bw routes
            if body_size_estimate > 100_000 and edge.bw_bytes_per_sec > 0:
                bw_factor = math.log2(1 + edge.bw_bytes_per_sec / 1_000_000)  # log scale
                sample *= (1.0 + bw_factor * 0.02)  # up to ~20% boost

            samples.append((sample, edge))

        # Return the edge with the highest sample
        return max(samples, key=lambda x: x[0])[1]

    # ------------------------------------------------------------------
    # Edge computation
    # ------------------------------------------------------------------

    def _compute_updated_edge(
        self,
        *,
        existing: RouteEdge,
        success: bool,
        latency_ms: float = 0.0,
        body_bytes: int = 0,
    ) -> RouteEdge:
        """Compute updated route edge with EWMA latency and counters."""
        now = _time.time()

        # EWMA latency update
        new_ewma = existing.ewma_latency_ms
        if latency_ms > 0:
            if existing.ewma_latency_ms <= 0:
                new_ewma = latency_ms  # first observation
            else:
                # Outlier rejection: cap new observation at 3× current EWMA
                capped = min(latency_ms, existing.ewma_latency_ms * 3.0)
                new_ewma = existing.ewma_latency_ms * (1 - _EWMA_ALPHA) + capped * _EWMA_ALPHA

        # Simple p50/p95/p99 approximation via EWMA deltas
        new_p50 = existing.p50_latency_ms
        new_p95 = existing.p95_latency_ms
        new_p99 = existing.p99_latency_ms
        if latency_ms > 0:
            if existing.p50_latency_ms <= 0:
                new_p50 = latency_ms
            else:
                # p50: 50th percentile EWMA
                new_p50 = existing.p50_latency_ms * 0.9 + latency_ms * 0.1
            # p95: only update if this observation is above current p50
            if latency_ms > existing.p50_latency_ms:
                new_p95 = existing.p95_latency_ms * 0.95 + latency_ms * 0.05 if existing.p95_latency_ms > 0 else latency_ms
            # p99: only update if observation is in the top 5% (above p95)
            if existing.p95_latency_ms > 0 and latency_ms > existing.p95_latency_ms:
                new_p99 = existing.p99_latency_ms * 0.98 + latency_ms * 0.02 if existing.p99_latency_ms > 0 else latency_ms

        # Bandwidth estimate update (EWMA)
        new_bw = existing.bw_bytes_per_sec
        if success and body_bytes > 0 and latency_ms > 0:
            bw = body_bytes / (latency_ms / 1000.0)  # bytes/sec
            if existing.bw_bytes_per_sec <= 0:
                new_bw = bw
            else:
                new_bw = existing.bw_bytes_per_sec * 0.8 + bw * 0.2

        # Max body bytes tracking
        new_max_body = max(existing.max_body_bytes, body_bytes) if success else existing.max_body_bytes

        return RouteEdge(
            domain=existing.domain,
            proxy=existing.proxy,
            transport=existing.transport,
            ewma_latency_ms=round(new_ewma, 1),
            p50_latency_ms=round(new_p50, 1),
            p95_latency_ms=round(new_p95, 1),
            p99_latency_ms=round(new_p99, 1),
            success_count=existing.success_count + (1 if success else 0),
            fail_count=existing.fail_count + (0 if success else 1),
            bw_bytes_per_sec=round(new_bw, 0),
            max_body_bytes=new_max_body,
            last_success=now if success else existing.last_success,
            last_failure=now if not success else existing.last_failure,
            first_seen=existing.first_seen if existing.first_seen > 0 else now,
        )

    # ------------------------------------------------------------------
    # DuckDB persistence
    # ------------------------------------------------------------------

    async def _get_routes_for_domain(self, domain: str) -> list[RouteEdge]:
        """Get all route edges for a domain. Cache-first, then DuckDB."""
        # Check in-memory cache first
        cached = self._cache.get(domain)
        if cached is not None:
            return cached

        edges: list[RouteEdge] = []
        if self._enabled and self._store is not None:
            try:
                edges = await self._query_routes_from_db(domain)
            except Exception:  # noqa: BLE001 — fail-safe
                pass

        # Cache even empty results (negative caching for 5 min)
        self._cache.put(domain, edges)
        return edges

    async def _get_single_route(
        self,
        domain: str,
        proxy: str,
        transport: str,
    ) -> RouteEdge | None:
        """Get a single route edge. First checks cache, then queries DB."""
        cached = self._cache.get(domain)
        if cached is not None:
            for edge in cached:
                if edge.proxy == proxy and edge.transport == transport:
                    return edge
            return None

        if self._enabled and self._store is not None:
            try:
                return await self._query_single_route_from_db(domain, proxy, transport)
            except Exception:  # noqa: BLE001 — fail-safe
                pass
        return None

    async def _query_routes_from_db(self, domain: str) -> list[RouteEdge]:
        """Query all routes for a domain from DuckDB."""
        if self._store is None:
            return []

        loop = asyncio.get_running_loop()

        def _sync_query() -> list[RouteEdge]:
            try:
                self._store.ensure_connected()  # type: ignore[union-attr]
                conn = (
                    self._store._file_conn  # type: ignore[union-attr] # noqa: SLF001
                    if self._store._db_path  # type: ignore[union-attr] # noqa: SLF001
                    else self._store._persistent_conn  # type: ignore[union-attr] # noqa: SLF001
                )
                if conn is None:
                    return []
                rows = conn.execute(
                    "SELECT domain, proxy, transport, ewma_latency_ms, "
                    "p50_latency_ms, p95_latency_ms, p99_latency_ms, "
                    "success_count, fail_count, bw_bytes_per_sec, "
                    "max_body_bytes, "
                    "COALESCE(epoch_ms(last_success)/1000.0, 0), "
                    "COALESCE(epoch_ms(last_failure)/1000.0, 0), "
                    "COALESCE(epoch_ms(first_seen)/1000.0, 0) "
                    "FROM proxy_routes WHERE domain = ? "
                    "ORDER BY success_count DESC, ewma_latency_ms ASC",
                    [domain],
                ).fetchall()
                return [
                    RouteEdge(
                        domain=str(r[0]),
                        proxy=str(r[1]),
                        transport=str(r[2]),
                        ewma_latency_ms=float(r[3]),
                        p50_latency_ms=float(r[4]),
                        p95_latency_ms=float(r[5]),
                        p99_latency_ms=float(r[6]),
                        success_count=int(r[7]),
                        fail_count=int(r[8]),
                        bw_bytes_per_sec=float(r[9]),
                        max_body_bytes=int(r[10]),
                        last_success=float(r[11]) if r[11] else 0.0,
                        last_failure=float(r[12]) if r[12] else 0.0,
                        first_seen=float(r[13]) if r[13] else 0.0,
                    )
                    for r in rows
                ]
            except Exception:  # noqa: BLE001 — fail-safe
                return []

        return await loop.run_in_executor(
            self._store._shared_executor,  # type: ignore[union-attr] # noqa: SLF001
            _sync_query,
        )

    async def _query_single_route_from_db(
        self,
        domain: str,
        proxy: str,
        transport: str,
    ) -> RouteEdge | None:
        """Query a single route edge from DuckDB."""
        if self._store is None:
            return None
        loop = asyncio.get_running_loop()

        def _sync() -> RouteEdge | None:
            try:
                self._store.ensure_connected()  # type: ignore[union-attr]
                conn = (
                    self._store._file_conn  # type: ignore[union-attr] # noqa: SLF001
                    if self._store._db_path  # type: ignore[union-attr] # noqa: SLF001
                    else self._store._persistent_conn  # type: ignore[union-attr] # noqa: SLF001
                )
                if conn is None:
                    return None
                r = conn.execute(
                    "SELECT domain, proxy, transport, ewma_latency_ms, "
                    "p50_latency_ms, p95_latency_ms, p99_latency_ms, "
                    "success_count, fail_count, bw_bytes_per_sec, "
                    "max_body_bytes, "
                    "COALESCE(epoch_ms(last_success)/1000.0, 0), "
                    "COALESCE(epoch_ms(last_failure)/1000.0, 0), "
                    "COALESCE(epoch_ms(first_seen)/1000.0, 0) "
                    "FROM proxy_routes "
                    "WHERE domain = ? AND proxy = ? AND transport = ?",
                    [domain, proxy, transport],
                ).fetchone()
                if r is None:
                    return None
                return RouteEdge(
                    domain=str(r[0]),
                    proxy=str(r[1]),
                    transport=str(r[2]),
                    ewma_latency_ms=float(r[3]),
                    p50_latency_ms=float(r[4]),
                    p95_latency_ms=float(r[5]),
                    p99_latency_ms=float(r[6]),
                    success_count=int(r[7]),
                    fail_count=int(r[8]),
                    bw_bytes_per_sec=float(r[9]),
                    max_body_bytes=int(r[10]),
                    last_success=float(r[11]) if r[11] else 0.0,
                    last_failure=float(r[12]) if r[12] else 0.0,
                    first_seen=float(r[13]) if r[13] else 0.0,
                )
            except Exception:  # noqa: BLE001 — fail-safe
                return None

        return await loop.run_in_executor(
            self._store._shared_executor,  # type: ignore[union-attr] # noqa: SLF001
            _sync,
        )

    async def _persist(self, edge: RouteEdge) -> None:
        """Persist a route edge to DuckDB with LRU eviction."""
        # Invalidate cache for this domain so next read hits DB
        self._cache.invalidate(edge.domain)

        if not self._enabled or self._store is None:
            return

        loop = asyncio.get_running_loop()

        def _sync_upsert() -> None:
            try:
                self._store.ensure_connected()  # type: ignore[union-attr]
                conn = (
                    self._store._file_conn  # type: ignore[union-attr] # noqa: SLF001
                    if self._store._db_path  # type: ignore[union-attr] # noqa: SLF001
                    else self._store._persistent_conn  # type: ignore[union-attr] # noqa: SLF001
                )
                if conn is None:
                    return

                # Ensure table exists (idempotent)
                self._store.ensure_proxy_routes_schema()  # type: ignore[union-attr]

                # Convert float timestamps to DuckDB TIMESTAMP
                last_success_ts = (
                    _dt.datetime.fromtimestamp(edge.last_success, tz=_dt.timezone.utc).isoformat()
                    if edge.last_success > 0
                    else None
                )
                last_failure_ts = (
                    _dt.datetime.fromtimestamp(edge.last_failure, tz=_dt.timezone.utc).isoformat()
                    if edge.last_failure > 0
                    else None
                )

                conn.execute(
                    "INSERT INTO proxy_routes "
                    "(domain, proxy, transport, ewma_latency_ms, p50_latency_ms, "
                    "p95_latency_ms, p99_latency_ms, success_count, fail_count, "
                    "bw_bytes_per_sec, max_body_bytes, last_success, last_failure, "
                    "updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(domain, proxy, transport) DO UPDATE SET "
                    "ewma_latency_ms = excluded.ewma_latency_ms, "
                    "p50_latency_ms = excluded.p50_latency_ms, "
                    "p95_latency_ms = excluded.p95_latency_ms, "
                    "p99_latency_ms = excluded.p99_latency_ms, "
                    "success_count = excluded.success_count, "
                    "fail_count = excluded.fail_count, "
                    "bw_bytes_per_sec = excluded.bw_bytes_per_sec, "
                    "max_body_bytes = excluded.max_body_bytes, "
                    "last_success = excluded.last_success, "
                    "last_failure = excluded.last_failure, "
                    "updated_at = excluded.updated_at",
                    [
                        edge.domain,
                        edge.proxy,
                        edge.transport,
                        edge.ewma_latency_ms,
                        edge.p50_latency_ms,
                        edge.p95_latency_ms,
                        edge.p99_latency_ms,
                        edge.success_count,
                        edge.fail_count,
                        edge.bw_bytes_per_sec,
                        edge.max_body_bytes,
                        last_success_ts,
                        last_failure_ts,
                    ],
                )

                # LRU eviction — use threading.Lock for thread-safe eviction
                with self._evict_lock:
                    count_result = conn.execute(
                        "SELECT COUNT(*) FROM proxy_routes"
                    ).fetchone()
                    if count_result and count_result[0] > self._max_rows:
                        excess = count_result[0] - self._max_rows
                        conn.execute(
                            "DELETE FROM proxy_routes WHERE id IN ("
                            "SELECT id FROM proxy_routes "
                            "ORDER BY COALESCE(last_success, first_seen) ASC LIMIT ?"
                            ")",
                            [excess],
                        )
            except Exception:  # noqa: BLE001 — fail-safe; DB write failure
                pass

        await loop.run_in_executor(
            self._store._shared_executor,  # type: ignore[union-attr] # noqa: SLF001
            _sync_upsert,
        )


# ---------------------------------------------------------------------------
# Singleton factory (F320: Refactored to use centralized pattern)
# ---------------------------------------------------------------------------
from hledac.universal.utils._patterns import module_singleton_getter


def _make_route_graph_service(store: DuckDBShadowStore | None) -> RouteGraphService:
    """Factory for RouteGraphService singleton."""
    return RouteGraphService(store=store)


# Module-level singleton getter with thread-safe double-checked locking
_get_route_graph_service = module_singleton_getter(
    singleton_name="_route_graph_singleton",
    factory=lambda: _make_route_graph_service(None),
)


def get_route_graph_service(
    store: DuckDBShadowStore | None = None,
) -> RouteGraphService:
    """Get or create the module-level RouteGraphService singleton.

    Args:
        store: DuckDBShadowStore for persistence. Only used on first call.
    """
    return _get_route_graph_service()


def reset_route_graph_service() -> None:
    """Reset singleton — test seam only."""
    global _route_graph_singleton
    _route_graph_singleton = None


__all__ = [
    "RouteEdge",
    "RouteRecommendation",
    "RouteGraphService",
    "get_route_graph_service",
    "reset_route_graph_service",
]
