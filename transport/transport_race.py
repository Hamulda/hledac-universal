"""
transport/transport_race.py — R9: Parallel Transport Racing

Replaces the sequential transport fallback chain in public_fetcher.py with


bounded parallel racing: for each URL, launch httpx, curl_cffi, and
nw_connection (Apple Network.framework) concurrently, take the first
success, and cancel the rest.

Key features:
- Per-transport circuit breakers (TransportCircuitBreaker) disable
  failing transports after consecutive failures
- Global semaphore caps total concurrent races (M1 8GB: 8 races max)
- NWConnection lane: hardware-accelerated TLS 1.3 via Network.framework
- Fail-soft: any transport error → dropped from race, others continue
- Timeout: bounded per-race timeout prevents slow transports from
  blocking the winner

M1 8GB bounds:
- MAX_CONCURRENT_RACES: 8 (8 URLs × up to 3 transports = 24 in-flight)
- Per-transport semaphores: 3/transport (prevents single-transport monopoly)
- NWConnection: skipped when RSS > 5.5 GiB

Env gates:
- HLEDAC_ENABLE_TRANSPORT_RACE=1 — enable racing (default ON)
- HLEDAC_TRANSPORT_RACE_TIMEOUT_S=8.0 — per-race timeout

Integration:
- public_fetcher.py:_fetch_core_racing() — drop-in replacement for _fetch_core()
- Uses existing TransportCircuitBreaker from circuit_breaker.py
- Uses existing fetch_via_unified() from unified_transport.py
- Uses existing fetch_nw_connection() from nw_connection_lane.py

Invariants:
- asyncio.gather always with return_exceptions=True
- asyncio.CancelledError always re-raised
- No bare except: — always except Exception:
- Circuit breaker state persisted in-memory (bounded, no disk)
- Any failure → race continues with remaining transports
- First 2xx/3xx response wins immediately
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING
import urllib.parse as _urlparse
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Final

from hledac.universal.transport.circuit_breaker import (
    CircuitBreaker,
    TransportCircuitBreaker,
    get_transport_breaker,
)
from hledac.universal.transport.utils import (
    safe_create_task,
)
from hledac.universal.utils.asyncx import safe_wait_for
from hledac.universal.utils.asyncx import safe_wait_for
from _core import aclose

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env gates
# ---------------------------------------------------------------------------

_RACE_ENABLED: bool = (
    os.environ.get("HLEDAC_ENABLE_TRANSPORT_RACE", "1").lower()
    in ("1", "true", "yes", "on")
)
_RACE_TIMEOUT_S: float = float(
    os.environ.get("HLEDAC_TRANSPORT_RACE_TIMEOUT_S", "8.0")
)

# [FINAL]-019: SIEM fingerprint defense — stagger transport launches.
# Fires 4 TLS handshakes within ~5ms by default (no-op fallback).
# Races 3 transports: httpx, curl_cffi, nw_connection (+ nw_quic opportunistically).
# Each transport gets a decorrelated stagger so they don't all fire simultaneously.
# This breaks the "exactly N TLS ClientHellos in N ms" SIEM fingerprint.
_RACE_STAGGER_MS: float = float(os.environ.get('HLEDAC_RACE_STAGGER_MS', '0'))

# ---------------------------------------------------------------------------
# ISSUE-022-04: Pre-race gate thresholds
# ---------------------------------------------------------------------------
# Use RouteGraphService as a pre-race gate to avoid wasted parallel fetches.
# If a transport has high confidence (>95% success rate, <500ms p50 latency,
# >=3 observations), skip the race and use that transport directly.
# Only race when no transport meets the high-confidence threshold.

_PRE_RACE_GATE_ENABLED: bool = (
    os.environ.get("HLEDAC_PRE_RACE_GATE", "1").lower()
    in ("1", "true", "yes", "on")
)
_PRE_RACE_MIN_SUCCESS_RATE: float = float(
    os.environ.get("HLEDAC_PRE_RACE_MIN_SUCCESS_RATE", "0.95")
)  # 95% success rate threshold
_PRE_RACE_MAX_P50_LATENCY_MS: float = float(
    os.environ.get("HLEDAC_PRE_RACE_MAX_P50_LATENCY_MS", "500.0")
)  # 500ms p50 latency cap
_PRE_RACE_MIN_OBSERVATIONS: int = int(
    os.environ.get("HLEDAC_PRE_RACE_MIN_OBS", "3")
)  # minimum observations before trusting
_PRE_RACE_CONFIDENCE_THRESHOLD: float = float(
    os.environ.get("HLEDAC_PRE_RACE_CONFIDENCE_THRESHOLD", "0.80")
)  # 80% confidence to skip race

# ---------------------------------------------------------------------------
# M1 8GB bounds
# ---------------------------------------------------------------------------

# Maximum concurrent races (one race = one URL × up to N transports).
# 8 races × 3 transports = 24 in-flight at absolute worst.
# Each transport ~15 MB (curl_cffi session) + ~5 MB (httpx session) +
# ~50 KB (nw_connection) ≈ 20 MB/in-flight → 480 MB total. Safe on 8 GB.
_MAX_CONCURRENT_RACES: Final[int] = 8

# Per-transport concurrency caps — prevent one transport from monopolizing
# the thread pool when others could be winning races.
_TRANSPORT_SEMAPHORE_LIMITS: Final[dict[str, int]] = {
    "httpx": 4,
    "curl_cffi": 4,
    "nw_connection": 3,
    "nw_quic": 3,  # SILICON-05: Network.framework QUIC/HTTP3 (shared pool with TCP)
    "playwright": 1,  # JS renderer is M1-heavy
    "curl_cffi_stealth": 3,  # ISSUE-15: stealth curl_cffi for racing
    "curl_cffi_tor": 2,  # ISSUE-15: Tor curl_cffi (higher latency per conn)
}

# ---------------------------------------------------------------------------
# NEXUS-018-011: Transport winner cache
# ---------------------------------------------------------------------------
# Per-host caching of the last winning transport. Eliminates redundant
# racing for repeat requests to the same host — saving 50-100 ms per URL
# (3× asyncio.create_task + semaphore + gather overhead).
# For 200 URLs per sprint, this saves ~10-20 s in redundant racing.
#
# M1 8GB bounded: 256 entries, FIFO eviction, 120 s TTL.

# Winner cache: host → (transport_name, timestamp_monotonic)
_WINNER_CACHE: dict[str, tuple[str, float]] = {}
_WINNER_CACHE_TTL_S: float = 120.0  # 2 min TTL
_WINNER_CACHE_MAX: int = 256  # M1 8GB bounded
# LRU tracking: move to end on access, popleft for eviction
_winner_access_order: deque[str] = deque()
# Lock for cache operations
_winner_cache_lock = asyncio.Lock()
# Stats — protected by _winner_cache_lock
_WINNER_CACHE_STATS: dict[str, int] = {
    "hits": 0,
    "misses": 0,
    "fastpath_failures": 0,
    "fastpath_timeouts": 0,  # ISSUE-022-04: track timeouts separately
}

# ISSUE-022-04: Pre-race gate stats
_PRE_RACE_GATE_STATS: dict[str, int] = {
    "gate_hits": 0,           # transport found via pre-race gate
    "gate_skips": 0,          # skipped (confidence < threshold)
    "gate_no_data": 0,        # no route graph data for domain
    "gate_errors": 0,         # errors accessing route graph
    "gate_timeout_fallbacks": 0,  # pre-race candidate timed out
}
_pre_race_gate_lock = asyncio.Lock()

# RouteGraphService singleton reference (lazy-initialized)
_route_graph_service_lock = asyncio.Lock()
_route_graph_service: "RouteGraphService | None" = None

if TYPE_CHECKING:
    from hledac.universal.knowledge.proxy_routes import RouteGraphService
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore


async def _get_route_graph_service() -> "RouteGraphService | None":
    """Get or create the RouteGraphService singleton.
    
    Lazy initialization to avoid circular imports and expensive DuckDB setup
    on every transport_race import.
    """
    global _route_graph_service
    if _route_graph_service is not None:
        return _route_graph_service
    # Import here to avoid circular imports
    try:
        from hledac.universal.knowledge.proxy_routes import RouteGraphService
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
        
        # Try to get a shared DuckDB store
        store = DuckDBShadowStore.get_shared_instance()
        if store is not None:
            _route_graph_service = RouteGraphService(store=store)
        else:
            _route_graph_service = RouteGraphService()  # in-memory only
        return _route_graph_service
    except Exception:  # noqa: BLE001 — fail-soft: RouteGraphService unavailable
        return None


# Transport aliases map for canonical names
_WINNER_CANONICAL_TRANSPORTS: frozenset[str] = frozenset({
    "httpx", "curl_cffi", "nw_connection", "nw_quic",
})


def _extract_host_for_winner_cache(url: str) -> str:
    """Extract host:port for winner cache key. Port stripped if default."""
    try:
        parsed = _urlparse.urlparse(url)
        host = parsed.netloc.lower()
        if not host:
            host = url.lower()
        # Normalize: strip default port
        if host.endswith(":80") and parsed.scheme == "http":
            host = host[:-3]
        elif host.endswith(":443") and parsed.scheme == "https":
            host = host[:-4]
        return host
    except Exception:  # noqa: BLE001
        return url.lower()


async def _winner_cache_get(host: str) -> tuple[str, float] | None:
    """Look up the cached winning transport for a host.

    Returns (transport_name, timestamp) or None on miss/expiry.
    Updates LRU access order on hit.
    """
    async with _winner_cache_lock:
        if host not in _WINNER_CACHE:
            return None
        transport, ts = _WINNER_CACHE[host]
        if time.monotonic() - ts >= _WINNER_CACHE_TTL_S:
            # Expired — evict
            del _WINNER_CACHE[host]
            if host in _winner_access_order:
                _winner_access_order.remove(host)
            return None
        # Hit — touch LRU
        if host in _winner_access_order:
            _winner_access_order.remove(host)
        _winner_access_order.append(host)
        return (transport, ts)


async def _winner_cache_set(host: str, transport: str) -> None:
    """Store a winning transport for a host."""
    async with _winner_cache_lock:
        now = time.monotonic()
        # Evict oldest if at capacity
        while len(_WINNER_CACHE) >= _WINNER_CACHE_MAX and _winner_access_order:
            oldest = _winner_access_order.popleft()
            _WINNER_CACHE.pop(oldest, None)
        _WINNER_CACHE[host] = (transport, now)
        if host in _winner_access_order:
            _winner_access_order.remove(host)
        _winner_access_order.append(host)


def _winner_cache_stats() -> dict[str, int]:
    """Return winner cache telemetry snapshot."""
    return {
        "winner_cache_size": len(_WINNER_CACHE),
        "winner_cache_max": _WINNER_CACHE_MAX,
        "winner_cache_hits": _WINNER_CACHE_STATS["hits"],
        "winner_cache_misses": _WINNER_CACHE_STATS["misses"],
        "winner_cache_fastpath_failures": _WINNER_CACHE_STATS["fastpath_failures"],
        "winner_cache_fastpath_timeouts": _WINNER_CACHE_STATS["fastpath_timeouts"],
    }


def _reset_winner_cache() -> None:
    """Reset winner cache (for testing)."""
    _WINNER_CACHE.clear()
    _winner_access_order.clear()
    _WINNER_CACHE_STATS["hits"] = 0
    _WINNER_CACHE_STATS["misses"] = 0
    _WINNER_CACHE_STATS["fastpath_failures"] = 0
    _WINNER_CACHE_STATS["fastpath_timeouts"] = 0


def _reset_pre_race_gate_stats() -> None:
    """Reset pre-race gate stats (for testing)."""
    _PRE_RACE_GATE_STATS["gate_hits"] = 0
    _PRE_RACE_GATE_STATS["gate_skips"] = 0
    _PRE_RACE_GATE_STATS["gate_no_data"] = 0
    _PRE_RACE_GATE_STATS["gate_errors"] = 0
    _PRE_RACE_GATE_STATS["gate_timeout_fallbacks"] = 0


# ISSUE-022-04: Transport name mapping from RouteGraphService to transport_race
_ROUTEGRAPH_TRANSPORT_MAP: dict[str, str] = {
    "httpx": "httpx",
    "curl_cffi": "curl_cffi",
    "curl": "curl_cffi",  # alias
    "nw_connection": "nw_connection",
    "network_framework": "nw_connection",  # alias
    "nw_quic": "nw_quic",
    "quic": "nw_quic",  # alias
    "playwright": "playwright",
}


def _map_routegraph_transport(rg_transport: str) -> str | None:
    """Map RouteGraphService transport name to transport_race canonical name.

    Returns None if the transport is not supported in racing.
    """
    if not rg_transport:
        return None
    # Direct match
    if rg_transport in _WINNER_CANONICAL_TRANSPORTS:
        return rg_transport
    # Alias lookup
    return _ROUTEGRAPH_TRANSPORT_MAP.get(rg_transport.lower())


def _pre_race_gate_stats() -> dict[str, int]:
    """Return pre-race gate telemetry snapshot."""
    return dict(_PRE_RACE_GATE_STATS)


# ---------------------------------------------------------------------------
# Transport race result
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RaceResult:
    """Result from a single transport in the race.

    ``winner`` is True for the first successful transport — all others
    are cancelled once a winner is declared.
    """

    transport: str
    result: dict[str, Any] | None = None
    error: str | None = None
    elapsed_ms: float = 0.0
    cancelled: bool = False

    @property
    def success(self) -> bool:
        if self.result is None:
            return False
        status = self.result.get("status_code", 0)
        return 200 <= status < 400 and not self.result.get("error")


# ---------------------------------------------------------------------------
# Transport Race Manager
# ---------------------------------------------------------------------------


class TransportRaceManager:
    """Manages parallel transport racing with circuit breakers and semaphores.

    Singleton — one instance per process. Thread-safe for async use;
    all mutable state is behind asyncio synchronization primitives.

    Lifecycle:
        manager = TransportRaceManager()
        result = await manager.race(url, timeout_s=8.0, headers=...)
    """

    __slots__ = (
        "_global_sem",
        "_transport_sems",
        "_transport_breakers",
        "_stats",
        "_initialized",
    )

    _instance: TransportRaceManager | None = None

    def __new__(cls) -> TransportRaceManager:
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._initialized = False
            cls._instance = instance
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._global_sem: asyncio.Semaphore = asyncio.Semaphore(
            _MAX_CONCURRENT_RACES
        )
        self._transport_sems: dict[str, asyncio.Semaphore] = {}
        self._transport_breakers: dict[str, TransportCircuitBreaker] = {}
        self._stats: dict[str, int] = {
            "races_run": 0,
            "races_won_httpx": 0,
            "races_won_curl_cffi": 0,
            "races_won_nw_connection": 0,
            "races_won_nw_quic": 0,  # SILICON-05
            "races_won_playwright": 0,
            "races_won_curl_cffi_stealth": 0,  # ISSUE-15
            "races_won_curl_cffi_tor": 0,  # ISSUE-15
            "races_all_failed": 0,
            "races_timeout": 0,
            "circuit_breaker_skips": 0,
        }
        self._init_transport("httpx", failure_threshold=5, recovery_timeout=30.0)
        self._init_transport(
            "curl_cffi", failure_threshold=4, recovery_timeout=45.0
        )
        self._init_transport(
            "nw_connection", failure_threshold=3, recovery_timeout=30.0
        )
        self._init_transport(
            "nw_quic", failure_threshold=3, recovery_timeout=30.0  # SILICON-05
        )
        self._init_transport(
            "playwright", failure_threshold=2, recovery_timeout=120.0
        )
        # ISSUE-15: Initialize stealth and Tor transports for racing
        self._init_transport(
            "curl_cffi_stealth", failure_threshold=4, recovery_timeout=45.0
        )
        self._init_transport(
            "curl_cffi_tor", failure_threshold=3, recovery_timeout=60.0
        )
        self._initialized = True

    def _init_transport(
        self, name: str, failure_threshold: int, recovery_timeout: float
    ) -> None:
        """Initialize per-transport semaphore and circuit breaker."""
        limit = _TRANSPORT_SEMAPHORE_LIMITS.get(name, 2)
        self._transport_sems[name] = asyncio.Semaphore(limit)
        self._transport_breakers[name] = TransportCircuitBreaker(
            transport=name,
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
        )

    # ------------------------------------------------------------------
    # Circuit breaker helpers
    # ------------------------------------------------------------------

    def is_transport_open(self, transport: str) -> bool:
        """Check if a transport's circuit breaker is open (transport disabled)."""
        breaker = self._transport_breakers.get(transport)
        if breaker is None:
            return False
        return breaker.is_open()

    def transport_check(self, transport: str) -> bool:
        """Check if transport is allowed (circuit closed or half-open probe).

        Returns True if the transport can be used in a race.
        """
        breaker = self._transport_breakers.get(transport)
        if breaker is None:
            return True  # unknown transport → allow
        decision = breaker.check_circuit()
        if not decision.allowed:
            logger.debug(
                "transport_race: %s circuit open — skipping (retry in %.1fs)",
                transport,
                decision.retry_after_s,
            )
            self._stats["circuit_breaker_skips"] += 1
            return False
        return True

    def record_success(self, transport: str) -> None:
        """Record a successful fetch, resetting the circuit breaker."""
        breaker = self._transport_breakers.get(transport)
        if breaker is not None:
            breaker.record_success()

    # ------------------------------------------------------------------
    # ISSUE-022-04: Pre-race gate using RouteGraphService
    # ------------------------------------------------------------------

    async def _check_pre_race_gate(
        self,
        url: str,
        domain: str,
    ) -> tuple[str | None, float]:
        """Check RouteGraphService for high-confidence transport selection.

        Uses RouteGraphService as a pre-race gate to avoid wasted parallel
        fetches. If a transport has:
          - >95% success rate
          - <500ms p50 latency
          - >=3 observations
        Then use that transport directly instead of racing all transports.

        Args:
            url: The target URL
            domain: Normalized domain (e.g. "example.com")

        Returns:
            (transport_name, confidence) — transport_name is None if race
            should proceed normally. confidence is 0.0-1.0 indicating
            confidence in the recommendation.
        """
        if not _PRE_RACE_GATE_ENABLED:
            return (None, 0.0)

        service = _get_route_graph_service()
        if service is None:
            return (None, 0.0)

        try:
            edges = await service.get_route_stats(domain)
        except Exception:  # noqa: BLE001
            async with _pre_race_gate_lock:
                _PRE_RACE_GATE_STATS["gate_errors"] += 1
            return (None, 0.0)

        if not edges:
            async with _pre_race_gate_lock:
                _PRE_RACE_GATE_STATS["gate_no_data"] += 1
            return (None, 0.0)

        # Map RouteGraphService transport names to our transport names
        # RouteGraphService may track (transport, proxy) pairs; we care about transport
        transport_scores: dict[str, tuple[float, float, int]] = {}  # transport → (score, confidence, obs)

        for edge in edges:
            if not edge.transport:
                continue
            # Map RouteGraphService transport to our canonical transport
            mapped = _map_routegraph_transport(edge.transport)
            if mapped is None:
                continue

            # Check if this transport meets the high-confidence threshold
            if (
                edge.total_attempts < _PRE_RACE_MIN_OBSERVATIONS
            ):
                continue

            success_rate = edge.success_rate
            p50_latency = edge.p50_latency_ms

            if (
                success_rate >= _PRE_RACE_MIN_SUCCESS_RATE
                and p50_latency > 0
                and p50_latency <= _PRE_RACE_MAX_P50_LATENCY_MS
            ):
                # Calculate confidence based on observations
                confidence = min(0.95, edge.total_attempts / 10.0)
                # Composite score: success_rate × latency_score
                latency_score = 1.0 / (1.0 + p50_latency / 500.0)
                score = success_rate * 0.6 + latency_score * 0.4

                if mapped not in transport_scores or score > transport_scores[mapped][0]:
                    transport_scores[mapped] = (score, confidence, edge.total_attempts)

        if not transport_scores:
            async with _pre_race_gate_lock:
                _PRE_RACE_GATE_STATS["gate_skips"] += 1
            return (None, 0.0)

        # Pick the best transport
        best_transport = max(transport_scores, key=lambda t: transport_scores[t][0])
        best_score, best_confidence, best_obs = transport_scores[best_transport]

        # Only use pre-race gate if confidence is high enough
        if best_confidence < _PRE_RACE_CONFIDENCE_THRESHOLD:
            async with _pre_race_gate_lock:
                _PRE_RACE_GATE_STATS["gate_skips"] += 1
            return (None, 0.0)

        # Check circuit breaker
        if not self.transport_check(best_transport):
            async with _pre_race_gate_lock:
                _PRE_RACE_GATE_STATS["gate_skips"] += 1
            return (None, 0.0)

        async with _pre_race_gate_lock:
            _PRE_RACE_GATE_STATS["gate_hits"] += 1

        logger.debug(
            "transport_race: pre-race gate → %s (confidence=%.2f, obs=%d) for %s",
            best_transport, best_confidence, best_obs, domain,
        )
        return (best_transport, best_confidence)

    async def _record_to_route_graph(
        self,
        transport: str,
        domain: str,
        success: bool,
        latency_ms: float = 0.0,
    ) -> None:
        """Record fetch result to RouteGraphService for improved recommendations.

        This enables RouteGraphService to build up statistics for smarter
        pre-race gate decisions on future requests to the same domain.

        Args:
            transport: Transport name (e.g. "httpx", "curl_cffi")
            domain: Normalized domain (e.g. "example.com")
            success: Whether the fetch succeeded
            latency_ms: Actual latency of the fetch in milliseconds
        """
        service = _get_route_graph_service()
        if service is None:
            return

        try:
            # RouteGraphService expects the domain without port
            clean_domain = domain.split(":")[0].lower()
            if not clean_domain:
                return

            if success:
                await service.record_route_success(
                    domain=clean_domain,
                    proxy="",  # No proxy for direct transports
                    transport=transport,
                    latency_ms=latency_ms,
                )
            else:
                await service.record_route_failure(
                    domain=clean_domain,
                    proxy="",  # No proxy for direct transports
                    transport=transport,
                    latency_ms=latency_ms,
                )
        except Exception:  # noqa: BLE001 — fail-soft: don't let recording failures affect the main flow
            pass

    def record_failure(
        self, transport: str, is_timeout: bool = False
    ) -> None:
        """Record a failed fetch, potentially opening the circuit."""
        breaker = self._transport_breakers.get(transport)
        if breaker is not None:
            breaker.record_failure(is_timeout=is_timeout)

    # ------------------------------------------------------------------
    # Race execution
    # ------------------------------------------------------------------

    async def race(
        self,
        url: str,
        *,
        timeout_s: float | None = None,
        headers: dict[str, str] | None = None,
        max_bytes: int = 10 * 1024 * 1024,
        use_js: bool = False,
        use_stealth: bool = False,
    ) -> tuple[dict[str, Any] | None, str]:
        """Race multiple transports in parallel for a single URL.

        Launches httpx, curl_cffi, and nw_connection (optionally playwright,
        curl_cffi_stealth, curl_cffi_tor) concurrently. The first transport
        to return a 2xx/3xx response wins; remaining transports are cancelled.

        ISSUE-15: Stealth and Tor transports can now participate in the race
        alongside clearnet transports. For stealth URLs, we race both clearnet
        AND stealth transports to get the fastest response. For darknet URLs
        (.onion, .i2p, .freenet), Tor/I2P-specific transports are used.

        Args:
            url: Target URL (clearnet or darknet)
            timeout_s: Per-race timeout (default from env or 8.0s)
            headers: Optional HTTP headers for all transports
            max_bytes: Maximum response bytes
            use_js: If True, also include playwright JS renderer in the race
            use_stealth: If True, include stealth profiles in the race

        Returns:
            (result_dict, winning_transport) — result_dict is compatible
            with FetchResult construction. (None, reason) on total failure.
        """
        darknet = _is_darknet_url(url)

        if darknet:
            # Darknet URLs (.onion, .i2p) — race Tor + I2P transports
            return await self._race_darknet(url, headers, timeout_s, max_bytes)

        _auto_js = use_js or _is_likely_js_page(url)
        timeout = timeout_s if timeout_s is not None else _RACE_TIMEOUT_S

        # ── NEXUS-018-011: Winner cache fast path ────────────────────────
        # Before running the full race, check if we already know the
        # winning transport for this host. If the cached winner is still
        # allowed (not circuit-broken), try it directly — saving the
        # 50-100 ms race overhead (create_task × 3 + semaphore + gather).
        # Falls through to the full race on any failure.
        _cache_host = _extract_host_for_winner_cache(url)

        if not _auto_js and not use_stealth:
            # Winner cache fast path
            result = await self._try_winner_cache_fastpath(
                url, _cache_host, headers, max_bytes, timeout
            )
            if result is not None:
                return result

            # ── ISSUE-022-04: Pre-race gate ─────────────────────────────────
            # After winner cache miss, check RouteGraphService for high-confidence
            # transport. If a transport has >95% success rate and <500ms p50 latency
            # for this domain, skip the race and use that transport directly.
            # Only race when confidence is low across all transports.
            result = await self._try_pre_race_gate(url, _cache_host, headers, max_bytes, timeout)
            if result is not None:
                return result

        # Full race execution
        async with self._global_sem:
            self._stats["races_run"] += 1
            eligible = self._get_eligible_transports(url, use_stealth, _auto_js)
            if not eligible:
                logger.warning("transport_race: all transports circuit-broken")
                self._stats["races_all_failed"] += 1
                return (None, "all_transports_circuit_broken")
            tasks = self._launch_transport_tasks(eligible, url, headers, max_bytes, timeout, use_stealth)
            return await self._execute_race(tasks, eligible, timeout, _cache_host)

    async def _try_winner_cache_fastpath(
        self,
        url: str,
        host: str,
        headers: dict[str, str] | None,
        max_bytes: int,
        timeout: float,
    ) -> tuple[dict[str, Any] | None, str] | None:
        """Try the cached winning transport for a host (fast path).

        Returns result tuple on success, None to fall through to full race.
        """
        _cached = await _winner_cache_get(host)
        if _cached is None:
            async with _winner_cache_lock:
                _WINNER_CACHE_STATS["misses"] += 1
            return None

        _cached_transport, _ = _cached
        if _cached_transport not in _WINNER_CANONICAL_TRANSPORTS or not self.transport_check(_cached_transport):
            # Cached transport is circuit-broken — evict
            async with _winner_cache_lock:
                _WINNER_CACHE.pop(host, None)
                if host in _winner_access_order:
                    _winner_access_order.remove(host)
            return None

        async with _winner_cache_lock:
            _WINNER_CACHE_STATS["hits"] += 1
        logger.debug(
            "transport_race: winner cache hit for %s → %s",
            host, _cached_transport,
        )

        async with self._global_sem:
            self._stats["races_run"] += 1
            _fast_result = await _run_transport_standalone(
                self, _cached_transport, url, headers,
                max_bytes, timeout,
            )

        # Detect if fast path timed out
        _fast_is_timeout = (
            _fast_result is not None
            and _fast_result.result is not None
            and "timeout" in (_fast_result.result.get("error") or "").lower()
        )

        if _fast_result is not None and _fast_result.success:
            self.record_success(_cached_transport)
            self._stats[f"races_won_{_cached_transport}"] += 1
            await self._record_to_route_graph(
                _cached_transport, host, success=True,
                latency_ms=_fast_result.elapsed_ms,
            )
            return (_fast_result.result, _cached_transport)

        # Cached winner failed — evict from cache and retry full race
        async with _winner_cache_lock:
            _WINNER_CACHE_STATS["fastpath_failures"] += 1
            if _fast_is_timeout:
                _WINNER_CACHE_STATS["fastpath_timeouts"] += 1
            _WINNER_CACHE.pop(host, None)
            if host in _winner_access_order:
                _winner_access_order.remove(host)

        await self._record_to_route_graph(
            _cached_transport, host, success=False,
            latency_ms=_fast_result.elapsed_ms if _fast_result else 0.0,
        )
        self.record_failure(_cached_transport, is_timeout=_fast_is_timeout)
        logger.debug(
            "transport_race: winner cache fast path failed for %s "
            "(%s), falling through to full race",
            host, _cached_transport,
        )
        return None

    async def _try_pre_race_gate(
        self,
        url: str,
        host: str,
        headers: dict[str, str] | None,
        max_bytes: int,
        timeout: float,
    ) -> tuple[dict[str, Any] | None, str] | None:
        """Try the pre-race gate transport for a host.

        Returns result tuple on success, None to fall through to full race.
        """
        _pre_race_transport, _pre_race_confidence = await self._check_pre_race_gate(
            url, host
        )
        if _pre_race_transport is None:
            return None

        # Try the high-confidence transport directly
        async with self._global_sem:
            self._stats["races_run"] += 1
            _pre_race_result = await _run_transport_standalone(
                self, _pre_race_transport, url, headers,
                max_bytes, timeout,
            )

        if _pre_race_result is not None and _pre_race_result.success:
            self.record_success(_pre_race_transport)
            self._stats[f"races_won_{_pre_race_transport}"] += 1
            if _pre_race_transport in _WINNER_CANONICAL_TRANSPORTS:
                await _winner_cache_set(host, _pre_race_transport)
            await self._record_to_route_graph(
                _pre_race_transport, host, success=True,
                latency_ms=_pre_race_result.elapsed_ms,
            )
            logger.debug(
                "transport_race: pre-race gate win for %s → %s",
                host, _pre_race_transport,
            )
            return (_pre_race_result.result, _pre_race_transport)

        # Pre-race candidate failed — record failure and fall through to race
        _is_timeout = (
            _pre_race_result is not None
            and _pre_race_result.result is not None
            and "timeout" in (_pre_race_result.result.get("error") or "").lower()
        )
        self.record_failure(_pre_race_transport, is_timeout=_is_timeout)
        await self._record_to_route_graph(
            _pre_race_transport, host, success=False,
        )
        logger.debug(
            "transport_race: pre-race gate miss for %s (%s), racing all transports",
            host, _pre_race_transport,
        )
        return None

    def _get_eligible_transports(
        self,
        url: str,
        use_stealth: bool,
        auto_js: bool,
    ) -> list[str]:
        """Determine which transports are eligible to race."""
        eligible: list[str] = []

        # Always include clearnet: httpx, curl_cffi, nw_connection
        for t_name in ("httpx", "curl_cffi", "nw_connection"):
            if self.transport_check(t_name):
                eligible.append(t_name)

        # SILICON-05: Include nw_quic when host is known to support H3
        if not use_stealth and self.transport_check("nw_quic"):
            try:
                from hledac.universal.transport.http3_lane import _cache_get as _h3_cache_get
                from hledac.universal.transport.http3_lane import extract_host as _h3_extract_host
                host = _h3_extract_host(url)
                if host and _h3_cache_get(host) is True:
                    eligible.append("nw_quic")
            except Exception:  # noqa: BLE001
                pass

        # ISSUE-15: Include stealth transport in the race
        if use_stealth and self.transport_check("curl_cffi_stealth"):
            eligible.append("curl_cffi_stealth")

        # ISSUE-15: Include playwright for JS-heavy pages
        if auto_js and self.transport_check("playwright"):
            eligible.append("playwright")

        return eligible

    async def _launch_transport_tasks(
        self,
        eligible: list[str],
        url: str,
        headers: dict[str, str] | None,
        max_bytes: int,
        timeout: float,
        use_stealth: bool,
    ) -> dict[str, asyncio.Task[RaceResult]]:
        """Launch transport tasks with temporal stagger.

        Returns dict of transport_name -> task.
        """
        async def _run_transport(name: str) -> RaceResult:
            """Run a single transport with per-transport semaphore."""
            t_start = time.monotonic()
            sem = self._transport_sems.get(name)
            try:
                if sem is not None:
                    async with sem:
                        result = await self._fetch_one(
                            name, url, headers, max_bytes,
                            timeout, use_stealth=use_stealth
                        )
                else:
                    result = await self._fetch_one(
                        name, url, headers, max_bytes,
                        timeout, use_stealth=use_stealth
                    )
            except asyncio.CancelledError:
                return RaceResult(
                    transport=name,
                    cancelled=True,
                    elapsed_ms=(time.monotonic() - t_start) * 1000,
                )
            except Exception as e:
                logger.debug("transport_race: %s exception: %s", name, e)
                return RaceResult(
                    transport=name,
                    error=str(e),
                    elapsed_ms=(time.monotonic() - t_start) * 1000,
                )
            return RaceResult(
                transport=name,
                result=result if isinstance(result, dict) else None,
                error=result.get("error") if isinstance(result, dict) else str(result),
                elapsed_ms=(time.monotonic() - t_start) * 1000,
            )

        # [FINAL]-019: Launch transports with temporal stagger
        tasks: dict[str, asyncio.Task[RaceResult]] = {}
        import random as _rng
        for idx, t_name in enumerate(eligible):
            if _RACE_STAGGER_MS > 0 and idx > 0:
                await asyncio.sleep(abs(_rng.gauss(0.0, _RACE_STAGGER_MS / 3000.0)))  # type: ignore[arg-type]
            tasks[t_name] = safe_create_task(
                _run_transport(t_name), name=f"transport_race:{t_name}"
            )
        return tasks

    async def _execute_race(
        self,
        tasks: dict[str, asyncio.Task[RaceResult]],
        eligible: list[str],
        timeout: float,
        host: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """Execute the transport race and return the winner.

        Returns (result, transport_name) tuple.
        """
        winner: RaceResult | None = None
        pending: set[asyncio.Task[RaceResult]] = set(tasks.values())

        try:
            async with asyncio.timeout(timeout):
                while pending and winner is None:
                    done, pending = await asyncio.wait(
                        pending,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        try:
                            rr = task.result()
                        except Exception as exc:
                            logger.debug(
                                "transport_race: task exception: %s", exc
                            )
                            continue
                        if rr.success and winner is None:
                            winner = rr
                            break

            # Winner found normally
            if winner is not None and winner.result is not None:
                return self._handle_race_winner(winner, tasks, host)

            # No winner — all transports failed
            return await self._handle_race_all_failed(tasks, eligible, host)

        except asyncio.TimeoutError:
            return await self._handle_race_timeout(tasks, eligible, host, timeout, winner)

        except asyncio.CancelledError:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            raise

    def _handle_race_winner(
        self,
        winner: RaceResult,
        tasks: dict[str, asyncio.Task[RaceResult]],
        host: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """Handle race winner — record success and cancel losers."""
        self.record_success(winner.transport)
        self._stats[f"races_won_{winner.transport}"] += 1
        # Record to RouteGraphService
        self._record_to_route_graph_sync(
            winner.transport, host, success=True,
            latency_ms=winner.elapsed_ms,
        )
        # Cache winning transport
        if winner.transport in _WINNER_CANONICAL_TRANSPORTS:
            asyncio.create_task(_winner_cache_set(host, winner.transport))
            logger.debug(
                "transport_race: winner cache set %s → %s",
                host, winner.transport,
            )
        # Cancel remaining tasks
        for t_name, task in tasks.items():
            if t_name != winner.transport and not task.done():
                task.cancel()
        return (winner.result, winner.transport)

    async def _handle_race_all_failed(
        self,
        tasks: dict[str, asyncio.Task[RaceResult]],
        eligible: list[str],
        host: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """Handle all transports failing in race."""
        self._stats["races_all_failed"] += 1

        # Record failures for all completed transports
        for t_name, task in tasks.items():
            if task.done() and not task.cancelled():
                try:
                    rr = task.result()
                    if rr.error:
                        self.record_failure(
                            t_name,
                            is_timeout="timeout" in (rr.error or "").lower(),
                        )
                        await self._record_to_route_graph(
                            t_name, host, success=False,
                            latency_ms=rr.elapsed_ms,
                        )
                except Exception:
                    self.record_failure(t_name)

        # Try to return partial result
        for t_name, task in tasks.items():
            if task.done() and not task.cancelled():
                try:
                    rr = task.result()
                    if rr.result is not None:
                        _status = rr.result.get("status_code", 0)
                        _is_success = 200 <= _status < 400
                        await self._record_to_route_graph(
                            t_name, host, success=_is_success,
                            latency_ms=rr.elapsed_ms,
                        )
                        return (rr.result, t_name)
                except Exception:  # noqa: BLE001
                    pass

        return (None, "all_transports_failed")

    async def _handle_race_timeout(
        self,
        tasks: dict[str, asyncio.Task[RaceResult]],
        eligible: list[str],
        host: str,
        timeout: float,
        winner: RaceResult | None,
    ) -> tuple[dict[str, Any] | None, str]:
        """Handle race timeout."""
        self._stats["races_timeout"] += 1
        logger.debug("transport_race: race timeout after %.1fs", timeout)

        # Cancel all pending tasks
        for task in tasks.values():
            if not task.done():
                task.cancel()

        # Record timeout to circuit breaker
        for t_name in eligible:
            self.record_failure(t_name, is_timeout=True)
            await self._record_to_route_graph(
                t_name, host, success=False,
            )

        # Return partial result from winner if available
        if winner is not None and winner.result is not None:
            self.record_success(winner.transport)
            self._stats[f"races_won_{winner.transport}"] += 1
            await self._record_to_route_graph(
                winner.transport, host, success=True,
                latency_ms=winner.elapsed_ms,
            )
            if winner.transport in _WINNER_CANONICAL_TRANSPORTS:
                await _winner_cache_set(host, winner.transport)
            return (winner.result, winner.transport)

        return (None, "all_transports_failed")

    def _record_to_route_graph_sync(
        self,
        transport: str,
        domain: str,
        success: bool,
        latency_ms: float = 0.0,
    ) -> None:
        """Sync wrapper for _record_to_route_graph (for use in non-async context)."""
        # Create a fire-and-forget task since we don't need to await
        asyncio.create_task(
            self._record_to_route_graph(transport, domain, success, latency_ms)
        )

    async def _fetch_one(
        self,
        transport: str,
        url: str,
        headers: dict[str, str] | None,
        max_bytes: int,
        timeout_s: float,
        use_stealth: bool = False,
    ) -> dict[str, Any] | None:
        """Execute a single transport fetch.

        Delegates to the appropriate transport implementation.
        Returns a dict compatible with FetchResult construction,
        or None on total failure.

        ISSUE-15: Added curl_cffi_stealth and curl_cffi_tor transports.
        """
        if transport == "nw_connection":
            return await self._fetch_nw(url, timeout_s)
        elif transport == "nw_quic":
            return await self._fetch_nw_quic(url, timeout_s)
        elif transport in ("httpx", "curl_cffi"):
            return await self._fetch_unified(
                transport, url, headers, max_bytes, timeout_s,
                use_stealth=False,
            )
        elif transport == "curl_cffi_stealth":
            return await self._fetch_unified(
                "curl_cffi", url, headers, max_bytes, timeout_s,
                use_stealth=True,
            )
        elif transport == "curl_cffi_tor":
            return await self._fetch_unified(
                "curl_cffi", url, headers, max_bytes, timeout_s,
                use_stealth=True, use_tor=True,
            )
        elif transport == "playwright":
            return await self._fetch_playwright(url, timeout_s)
        return None

    async def _fetch_unified(
        self,
        transport: str,
        url: str,
        headers: dict[str, str] | None,
        max_bytes: int,
        timeout_s: float,
        use_stealth: bool = False,
        use_tor: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch via unified transport (httpx or curl_cffi).

        ISSUE-15: Added use_tor parameter for Tor-specific fetch.
        """
        try:
            from hledac.universal.transport.unified_transport import (
                POLICY_CLEARNET_H2,
                POLICY_STEALTH_CHROME,
                POLICY_TOR,
                fetch_via_unified,
            )

            if use_tor:
                policy = POLICY_TOR
            elif transport == "curl_cffi" and use_stealth:
                policy = POLICY_STEALTH_CHROME
            elif transport == "curl_cffi":
                policy = POLICY_STEALTH_CHROME
            else:
                policy = POLICY_CLEARNET_H2

            result = await safe_wait_for(
                fetch_via_unified(
                    url=url,
                    policy=policy,
                    headers=headers,
                    timeout_s=timeout_s,
                    max_bytes=max_bytes,
                ),
                timeout=timeout_s,
            )
            return result
        except asyncio.TimeoutError:
            logger.debug("transport_race: %s timeout for %s", transport, url)
            return {"url": url, "final_url": url, "status_code": 0,
                    "content_type": "", "text": None, "fetched_bytes": 0,
                    "declared_length": -1, "elapsed_ms": timeout_s * 1000,
                    "error": f"{transport}_timeout", "failure_stage": "race_timeout",
                    "headers": {}}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("transport_race: %s fetch error: %s", transport, e)
            return None

    async def _fetch_nw(
        self, url: str, timeout_s: float
    ) -> dict[str, Any] | None:
        """Fetch via Apple Network.framework (hardware-accelerated TLS 1.3).

        Only on darwin/arm64. Fail-soft — returns None if unavailable.
        """
        try:
            from hledac.universal.transport.nw_connection_lane import (
                fetch_nw_connection,
                is_nw_connection_available,
            )

            if not is_nw_connection_available():
                return None

            result = await safe_wait_for(
                fetch_nw_connection(
                    url,
                    timeout_ms=int(timeout_s * 1000),
                ),
                timeout=timeout_s + 1.0,  # slight grace for thread pool dispatch
            )

            if result is None:
                return None

            # nw_connection returns in a slightly different format;
            # normalize to unified format
            return {
                "url": url,
                "final_url": result.get("final_url", url),
                "status_code": result.get("status_code", 0),
                "content_type": result.get("content_type", ""),
                "text": (
                    result.get("content", b"").decode("utf-8", errors="replace")
                    if isinstance(result.get("content"), bytes)
                    else ""
                ),
                "fetched_bytes": (
                    len(result["content"])
                    if isinstance(result.get("content"), bytes)
                    else 0
                ),
                "declared_length": -1,
                "elapsed_ms": result.get("elapsed_ms", 0.0),
                "error": result.get("error"),
                "failure_stage": "nw_connection" if result.get("error") else None,
                "headers": result.get("headers", {}),
            }
        except asyncio.TimeoutError:
            return None
        except asyncio.CancelledError:
            raise
        except ImportError:
            return None
        except Exception:
            return None

    async def _fetch_nw_quic(
        self, url: str, timeout_s: float
    ) -> dict[str, Any] | None:
        """Fetch via Apple Network.framework native QUIC/HTTP3.

        SILICON-05: Preferred HTTP/3 path on Apple Silicon. Uses
        Network.framework's built-in QUIC stack with hardware-accelerated
        TLS 1.3. Only on darwin/arm64 + macOS 12.0+. Fail-soft.

        Only HTTPS URLs — QUIC always uses TLS 1.3.
        """
        try:
            from hledac.universal.transport.nw_quic_lane import (
                fetch_nw_quic,
                is_nw_quic_available,
            )

            if not is_nw_quic_available():
                return None

            result = await safe_wait_for(
                fetch_nw_quic(
                    url,
                    timeout_ms=int(timeout_s * 1000),
                ),
                timeout=timeout_s + 1.0,  # slight grace for thread pool dispatch
            )

            if result is None:
                return None

            # Normalize to unified format (same as _fetch_nw)
            return {
                "url": url,
                "final_url": result.get("final_url", url),
                "status_code": result.get("status_code", 0),
                "content_type": result.get("content_type", ""),
                "text": (
                    result.get("content", b"").decode("utf-8", errors="replace")
                    if isinstance(result.get("content"), bytes)
                    else ""
                ),
                "fetched_bytes": (
                    len(result["content"])
                    if isinstance(result.get("content"), bytes)
                    else 0
                ),
                "declared_length": -1,
                "elapsed_ms": result.get("elapsed_ms", 0.0),
                "error": result.get("error"),
                "failure_stage": "nw_quic" if result.get("error") else None,
                "headers": result.get("headers", {}),
            }
        except asyncio.TimeoutError:
            return None
        except asyncio.CancelledError:
            raise
        except ImportError:
            return None
        except Exception:
            return None

    async def _fetch_playwright(
        self, url: str, timeout_s: float
    ) -> dict[str, Any] | None:
        """Fetch via Playwright JS renderer.

        Heavy path — only used when use_js=True. Fail-soft.
        """
        try:
            from hledac.universal.fetching.public_fetcher import (
                _fetch_via_playwright,
            )
            result = await safe_wait_for(
                _fetch_via_playwright(url, timeout_s=timeout_s),
                timeout=timeout_s * 2,  # playwright is slow
            )
            return result
        except asyncio.TimeoutError:
            return None
        except asyncio.CancelledError:
            raise
        except ImportError:
            return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # ISSUE-15: Darknet racing and JS page detection
    # ------------------------------------------------------------------

    async def _race_darknet(
        self,
        url: str,
        headers: dict[str, str] | None,
        timeout_s: float | None,
        max_bytes: int,
    ) -> tuple[dict[str, Any] | None, str]:
        """Race Tor and I2P transports for darknet URLs.

        Darknet URLs (.onion, .i2p) cannot use clearnet transports — only
        Tor/I2P-routed transports are eligible. Races curl_cffi_tor against
        direct Tor fetch to get the fastest response.

        ISSUE-022-04: Records results to RouteGraphService for improved routing.

        Returns (result_dict, winning_transport) or (None, error_reason).
        """
        t = timeout_s if timeout_s is not None else _RACE_TIMEOUT_S

        eligible: list[str] = []
        if self.transport_check("curl_cffi_tor"):
            eligible.append("curl_cffi_tor")

        if not eligible:
            logger.warning("transport_race: all darknet transports circuit-broken")
            self._stats["races_all_failed"] += 1
            return await self._fallback_single(url, headers, timeout_s, max_bytes)

        # For darknet, we use a longer timeout (Tor TTFB can be 5-15s)
        dark_timeout = max(t, 30.0) if t < 30.0 else t

        # ISSUE-022-04: Extract domain for RouteGraphService
        _darknet_host = _extract_host_for_winner_cache(url)

        async def _try_tor() -> dict[str, Any] | None:
            try:
                return await self._fetch_one(
                    "curl_cffi_tor", url, headers, max_bytes, dark_timeout
                )
            except Exception:
                return None

        try:
            async with asyncio.timeout(dark_timeout):
                t_start = time.monotonic()
                result = await _try_tor()
                _latency_ms = (time.monotonic() - t_start) * 1000
                if result is not None and result.get("status_code", 0) >= 200:
                    self._stats["races_won_curl_cffi_tor"] += 1
                    # ISSUE-022-04: Record darknet success to RouteGraphService
                    await self._record_to_route_graph(
                        "curl_cffi_tor", _darknet_host, success=True,
                        latency_ms=_latency_ms,
                    )
                    return (result, "curl_cffi_tor")
                # Record darknet failure to RouteGraphService
                await self._record_to_route_graph(
                    "curl_cffi_tor", _darknet_host, success=False,
                    latency_ms=_latency_ms,
                )
        except asyncio.TimeoutError:
            self._stats["races_timeout"] += 1
            # ISSUE-022-04: Record timeout to RouteGraphService
            await self._record_to_route_graph(
                "curl_cffi_tor", _darknet_host, success=False,
            )

        # Fallback to single transport
        return await self._fallback_single(url, headers, timeout_s, max_bytes)

    async def _fallback_single(
        self,
        url: str,
        headers: dict[str, str] | None,
        timeout_s: float | None,
        max_bytes: int,
    ) -> tuple[dict[str, Any] | None, str]:
        """Fallback to single-transport fetch for stealth/darknet URLs."""
        try:
            from hledac.universal.transport.unified_transport import (
                POLICY_STEALTH_CHROME,
                fetch_via_unified,
            )
            t = timeout_s if timeout_s is not None else _RACE_TIMEOUT_S
            t_start = time.monotonic()
            result = await fetch_via_unified(
                url=url,
                policy=POLICY_STEALTH_CHROME,
                headers=headers,
                timeout_s=t,
                max_bytes=max_bytes,
            )
            _latency_ms = (time.monotonic() - t_start) * 1000
            _status = result.get("status_code", 0) if isinstance(result, dict) else 0
            _is_success = 200 <= _status < 400 if isinstance(result, dict) else False
            # ISSUE-022-04: Record fallback result to RouteGraphService
            _host = _extract_host_for_winner_cache(url)
            await self._record_to_route_graph(
                "curl_cffi_stealth", _host, success=_is_success,
                latency_ms=_latency_ms,
            )
            return (result, "curl_cffi_stealth")
        except Exception as e:
            logger.debug("transport_race: fallback single failed: %s", e)
            return (None, f"stealth_failed:{e}")

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, int]:
        """Return racing statistics snapshot."""
        stats = dict(self._stats)
        # NEXUS-018-011: merge winner cache stats
        stats.update(_winner_cache_stats())
        # ISSUE-022-04: merge pre-race gate stats
        stats.update(_pre_race_gate_stats())
        return stats

    def reset_stats(self) -> None:
        """Reset racing statistics."""
        self._stats = {k: 0 for k in self._stats}
        # NEXUS-018-011: also reset winner cache
        _reset_winner_cache()
        # ISSUE-022-04: also reset pre-race gate stats
        _reset_pre_race_gate_stats()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _is_darknet_url(url: str) -> bool:
    """Check if URL targets darknet (Tor/I2P/Freenet)."""
    url_lower = url.lower()
    return (
        url_lower.endswith(".onion")
        or url_lower.endswith(".i2p")
        or url_lower.endswith(".b32.i2p")
        or url_lower.endswith(".freenet")
        or url_lower.startswith("gopher://")
    )


# ── NEXUS-018-011: Winner cache fast-path fetch ────────────────────────────


async def _run_transport_standalone(
    manager: "TransportRaceManager",
    transport: str,
    url: str,
    headers: dict[str, str] | None,
    max_bytes: int,
    timeout_s: float,
) -> RaceResult | None:
    """Run a single transport fetch outside the full race (winner cache fast path).

    Does NOT acquire per-transport semaphore — the fast path is only
    used when the winner cache hits, and we want minimal overhead.
    If the transport fails, returns None so the caller falls through
    to the full race.

    Returns RaceResult on success, None on failure (to trigger full race fallback).
    """
    t_start = time.monotonic()
    try:
        result = await manager._fetch_one(
            transport, url, headers, max_bytes, timeout_s,
        )
    except asyncio.CancelledError:
        return RaceResult(
            transport=transport,
            cancelled=True,
            elapsed_ms=(time.monotonic() - t_start) * 1000,
        )
    except Exception:
        return None

    if result is None:
        return None

    rr = RaceResult(
        transport=transport,
        result=result if isinstance(result, dict) else None,
        error=result.get("error") if isinstance(result, dict) else str(result),
        elapsed_ms=(time.monotonic() - t_start) * 1000,
    )
    return rr


# ISSUE-15: Known JS-heavy domain patterns for auto-detection.
# These sites render primarily via client-side JavaScript and benefit
# from playwright inclusion in the transport race.
_JS_HEAVY_DOMAIN_PATTERNS: tuple[str, ...] = (
    # SPAs and JS frameworks
    "react.", "vue.", "angular.", "svelte.",
    # Known JS-only sites
    "twitter.com", "x.com", "instagram.com", "facebook.com",
    "tiktok.com", "reddit.com", "discord.com",
    # Cloudflare-protected (often JS challenge)
    "cloudflare",
)


def _is_likely_js_page(url: str) -> bool:
    """Auto-detect if a URL is likely to require JS rendering.

    Checks URL patterns indicating SPAs, social media, or Cloudflare-
    protected pages. Returns True if playwright should be considered
    for the transport race.

    False positives are cheap (playwright just gets cancelled if not needed);
    false negatives mean the page renders poorly but clearnet transports
    still return something.
    """
    url_lower = url.lower()
    for pattern in _JS_HEAVY_DOMAIN_PATTERNS:
        if pattern in url_lower:
            return True
    # URL paths indicating JS apps
    js_path_indicators = ("/_next/", "/__nuxt/", "/static/js/", "/_astro/")
    for indicator in js_path_indicators:
        if indicator in url_lower:
            return True
    # SPA hash routing
    if "/#" in url_lower and not url_lower.endswith(".pdf"):
        return True
    return False


def is_transport_race_enabled() -> bool:
    """Return True if transport racing is enabled via env gate."""
    return _RACE_ENABLED


def get_race_manager() -> TransportRaceManager:
    """Return the singleton TransportRaceManager instance."""
    return TransportRaceManager()


def reset_race_manager() -> None:
    """Reset the singleton (for testing)."""
    TransportRaceManager._instance = None


# ---------------------------------------------------------------------------
# Public API — drop-in replacement for _fetch_core
# ---------------------------------------------------------------------------


async def fetch_via_race(
    url: str,
    *,
    timeout_s: float = 35.0,
    max_bytes: int = 10 * 1024 * 1024,
    headers: dict[str, str] | None = None,
    use_js: bool = False,
    use_stealth: bool = False,
    ttfb_timeout_s: float | None = None,
) -> dict[str, Any]:
    """Race multiple transports for a URL — first success wins.

    Drop-in replacement for the sequential _fetch_core → fetch_via_unified
    pipeline. Launches httpx, curl_cffi, and nw_connection in parallel,
    takes the first 2xx/3xx response.

    Args:
        url: Target URL
        timeout_s: Per-request timeout (used as race timeout if shorter than
                   HLEDAC_TRANSPORT_RACE_TIMEOUT_S)
        max_bytes: Maximum response bytes
        headers: Optional HTTP headers
        use_js: If True, include playwright JS renderer
        use_stealth: If True, skip racing (use stealth path directly)
        ttfb_timeout_s: TTFB kill switch — applied AFTER race winner is chosen
                        by the caller (_fetch_core_retryable)

    Returns:
        dict compatible with existing FetchResult construction in
        public_fetcher.py._fetch_core()
    """
    manager = get_race_manager()

    # Race timeout: use the shorter of caller timeout and race timeout,
    # since we don't want a race to outlive the caller's budget
    race_timeout = min(timeout_s, _RACE_TIMEOUT_S)

    # TTFB timeout note: applied by caller (_fetch_core_retryable) via
    # asyncio.wait_for wrapping. The race already has its own timeout
    # so the TTFB guard is redundant during racing — but the caller
    # still applies it post-race for consistency.

    result, winner = await manager.race(
        url=url,
        timeout_s=race_timeout,
        headers=headers,
        max_bytes=max_bytes,
        use_js=use_js,
        use_stealth=use_stealth,
    )

    if result is None:
        return {
            "url": url,
            "final_url": url,
            "status_code": 0,
            "content_type": "",
            "text": None,
            "fetched_bytes": 0,
            "declared_length": -1,
            "elapsed_ms": 0,
            "error": f"race_all_failed:{winner}",
            "failure_stage": "transport_race",
            "headers": {},
        }

    # Add winner transport info for telemetry
    result["_race_winner"] = winner
    return result


__all__ = [
    "TransportRaceManager",
    "RaceResult",
    "fetch_via_race",
    "get_race_manager",
    "is_transport_race_enabled",
    "reset_race_manager",
    # NEXUS-018-011: winner cache exports
    "_winner_cache_stats",
    "_reset_winner_cache",
    "_extract_host_for_winner_cache",
    # ISSUE-022-04: pre-race gate exports
    "_pre_race_gate_stats",
    "_reset_pre_race_gate_stats",
]
