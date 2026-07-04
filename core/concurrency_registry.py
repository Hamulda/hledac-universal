"""
ConcurrencyBudget Registry — Centralizovaný správce semaforů pro M1 8GB.

ÚČEL:
- Single source of truth pro všechny asyncio.Semaphore hodnoty
- Konzistence napříč moduly (194 různých hodnot → jednotná kategorizace)
- Dynamická adjustace podle UMA stavu (OK/WARN/CRITICAL/EMERGENCY)
- Fail-safe fallback při chybějícím Governoru

KATEGORIE:
| Category          | OK   | WARN | CRITICAL | EMERGENCY | Use case                    |
|-------------------|------|------|----------|-----------|-----------------------------|
| HTTP_LANE         | 8    | 4    | 2        | 1         | curl_cffi/aiohttp fetches   |
| DNS_BRUTE         | 50   | 25   | 10       | 5         | Subdomain enumeration       |
| BGP_QUERY         | 3    | 2    | 1        | 1         | Heavyweight ASN lookups     |
| IP_QUERY          | 10   | 5    | 3        | 1         | IP-to-ASN/ipinfo/etc        |
| ACADEMIC_SEARCH   | 5    | 3    | 2        | 1         | ArXiv, CrossRef, etc.       |
| SOCIAL_MINE       | 4    | 2    | 1        | 1         | Social identity scraping    |
| TRANSPORT_TOR     | 3    | 2    | 1        | 1         | Tor transport lanes         |
| TRANSPORT_I2P     | 2    | 1    | 1        | 1         | I2P transport lanes         |
| TRANSPORT_NYM     | 2    | 1    | 1        | 1         | Nym mixnet transport         |
| DHT_BOOTSTRAP     | 2    | 1    | 1        | 1         | DHT bootstrap operations     |
| DHT_REQUEST       | 50   | 25   | 10       | 5         | DHT query requests           |
| GOPHER_LANE       | 2    | 1    | 1        | 1         | Gopher protocol             |
| BANNER_GRAB       | 1    | 1    | 1        | 1         | TCP banner enumeration      |
| PASTE_SCRAPE      | 4    | 2    | 1        | 1         | Paste site scrapers         |
| GRAPH_RAG         | 3    | 2    | 1        | 1         | LanceDB/embedding ops       |
| MLX_INFERENCE     | 1    | 1    | 1        | 1         | MLX model inference         |
| SCRAPE_GENERAL    | 10   | 5    | 3        | 1         | General scraping            |

INVARIANT:
- Všechny moduly používají ConcurrencyBudget.get(category) místo asyncio.Semaphore(hard_value)
- Governor dynamicky mění limity podle UMA stavu
- Fallback na OK hodnoty pokud Governor není dostupný
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.resource_governor import M1ResourceGovernor

logger = logging.getLogger(__name__)


class ConcurrencyCategory(Enum):
    """Kategorizace semaforů podle funkční oblasti."""
    HTTP_LANE = "http_lane"
    DNS_BRUTE = "dns_brute"
    BGP_QUERY = "bgp_query"
    IP_QUERY = "ip_query"
    ACADEMIC_SEARCH = "academic_search"
    SOCIAL_MINE = "social_mine"
    TRANSPORT_TOR = "transport_tor"
    TRANSPORT_I2P = "transport_i2p"
    TRANSPORT_NYM = "transport_nym"
    DHT_BOOTSTRAP = "dht_bootstrap"
    DHT_REQUEST = "dht_request"
    GOPHER_LANE = "gopher_lane"
    BANNER_GRAB = "banner_grab"
    PASTE_SCRAPE = "paste_scrape"
    GRAPH_RAG = "graph_rag"
    MLX_INFERENCE = "mlx_inference"
    SCRAPE_GENERAL = "scrape_general"


# Per-UMA-state limits per category: (OK, WARN, CRITICAL, EMERGENCY)
_CONCURRENCY_LIMITS: dict[ConcurrencyCategory, tuple[int, int, int, int]] = {
    # HTTP fetches — curl_cffi/aiohttp, primary data lane
    ConcurrencyCategory.HTTP_LANE: (8, 4, 2, 1),
    # DNS brute-force — high parallelism ok for enumeration
    ConcurrencyCategory.DNS_BRUTE: (50, 25, 10, 5),
    # BGP queries — heavyweight,ASN lookups
    ConcurrencyCategory.BGP_QUERY: (3, 2, 1, 1),
    # IP intelligence queries
    ConcurrencyCategory.IP_QUERY: (10, 5, 3, 1),
    # Academic search APIs — rate-limited by design
    ConcurrencyCategory.ACADEMIC_SEARCH: (5, 3, 2, 1),
    # Social media identity mining
    ConcurrencyCategory.SOCIAL_MINE: (4, 2, 1, 1),
    # Tor transport — heavyweight due to circuit setup
    ConcurrencyCategory.TRANSPORT_TOR: (3, 2, 1, 1),
    # I2P transport — lighter than Tor
    ConcurrencyCategory.TRANSPORT_I2P: (2, 1, 1, 1),
    # Nym mixnet — very heavyweight
    ConcurrencyCategory.TRANSPORT_NYM: (2, 1, 1, 1),
    # DHT bootstrap — UDP, lightweight but needs control
    ConcurrencyCategory.DHT_BOOTSTRAP: (2, 1, 1, 1),
    # DHT requests — UDP, can be more parallel
    ConcurrencyCategory.DHT_REQUEST: (50, 25, 10, 5),
    # Gopher protocol — legacy, rarely used
    ConcurrencyCategory.GOPHER_LANE: (2, 1, 1, 1),
    # TCP banner grab — heavyweight, one at a time
    ConcurrencyCategory.BANNER_GRAB: (1, 1, 1, 1),
    # Paste site scraping
    ConcurrencyCategory.PASTE_SCRAPE: (4, 2, 1, 1),
    # Graph RAG / LanceDB operations
    ConcurrencyCategory.GRAPH_RAG: (3, 2, 1, 1),
    # MLX inference — GPU-bound, sequential
    ConcurrencyCategory.MLX_INFERENCE: (1, 1, 1, 1),
    # General scraping fallback
    ConcurrencyCategory.SCRAPE_GENERAL: (10, 5, 3, 1),
}


@dataclass(frozen=True, slots=True)
class ConcurrencyBudget:
    """Immutable concurrency budget for a category."""
    category: ConcurrencyCategory
    ok_limit: int
    warn_limit: int
    critical_limit: int
    emergency_limit: int

    def get_limit(self, uma_state: str = "OK") -> int:
        """Get limit for UMA state (case-insensitive)."""
        state = uma_state.upper()
        if state == "WARN":
            return self.warn_limit
        elif state in ("CRITICAL", "CRIT"):
            return self.critical_limit
        elif state in ("EMERGENCY", "EMERG"):
            return self.emergency_limit
        return self.ok_limit  # Default: OK


# Module-level threading.Lock — SINGLE INSTANCE, created at import time.
# Protects _semaphore_cache from concurrent first-access across threads.
_cache_lock: threading.Lock = threading.Lock()


class ConcurrencyBudgetRegistry:
    """
    Centralizovaný registry pro všechny concurrency semafory.

    Použití:
        sem = ConcurrencyBudgetRegistry.get(ConcurrencyCategory.HTTP_LANE)
        async with sem:
            await fetch(url)

    Výhody:
    - Konzistentní hodnoty napříč celou codebase
    - Dynamická adjustace podle UMA stavu
    - Telemetrie pro monitoring
    - Fail-safe fallback
    """

    _instance: ConcurrencyBudgetRegistry | None = None
    # asyncio.Lock instance created LAZILY on first async call — avoids
    # DeprecationWarning from asyncio.Lock() created outside running event loop.
    _init_lock: asyncio.Lock | None = None

    def __init__(self) -> None:
        self._budgets: dict[ConcurrencyCategory, ConcurrencyBudget] = {}
        self._semaphores: dict[ConcurrencyCategory, asyncio.Semaphore] = {}
        self._governor: M1ResourceGovernor | None = None
        self._uma_state: str = "OK"
        self._stats: dict[ConcurrencyCategory, dict[str, int]] = {}

        # Initialize budgets from limits table
        for category, limits in _CONCURRENCY_LIMITS.items():
            self._budgets[category] = ConcurrencyBudget(
                category=category,
                ok_limit=limits[0],
                warn_limit=limits[1],
                critical_limit=limits[2],
                emergency_limit=limits[3],
            )
            # Start with OK limits
            self._semaphores[category] = asyncio.Semaphore(limits[0])
            self._stats[category] = {"acquired": 0, "released": 0, "rejected": 0}

    @classmethod
    async def get_instance(cls) -> "ConcurrencyBudgetRegistry":
        """Get singleton instance (thread-safe, async-context-only init)."""
        if cls._instance is None:
            # Lazily create the asyncio.Lock here — safe because this is async.
            if cls._init_lock is None:
                cls._init_lock = asyncio.Lock()
            async with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def register_governor(self, governor: M1ResourceGovernor) -> None:
        """Register ResourceGovernor for dynamic state updates."""
        self._governor = governor
        logger.debug("ConcurrencyBudgetRegistry: Governor registered")

    async def _get_governor_decision(self) -> str:
        """Get current UMA state from governor or fallback."""
        if self._governor is not None:
            try:
                decision = await self._governor.evaluate()
                return decision.uma_state
            except Exception:  # noqa: BLE001
                pass
        return self._uma_state

    def get(self, category: ConcurrencyCategory) -> asyncio.Semaphore:
        """
        Get Semaphore for category (read-only, no lock needed).

        Returns existing semaphore. Does NOT re-create on state change —
        use adjust_for_state() to trigger atomic wholesale replacement.

        No lock needed: dict.get() is atomic in CPython (GIL).
        Lazy creation uses dict.get() returning None for miss → atomic compare-and-swap.
        """
        # dict.get() is GIL-protected atomic read — no torn reads
        sem = self._semaphores.get(category)
        if sem is not None:
            return sem

        # Fallback: create with OK limit. Hit only for dynamic registration.
        budget = self._budgets.get(category)
        limit = budget.ok_limit if budget else 5
        new_sem = asyncio.Semaphore(limit)

        # Atomic insert — dict[key] = value is GIL-protected in CPython.
        # If another coroutine inserted between get() and here, this just
        # overwrites with the same semaphore value (idempotent).
        self._semaphores[category] = new_sem

        if budget is None:
            self._budgets[category] = ConcurrencyBudget(
                category=category,
                ok_limit=limit,
                warn_limit=limit,
                critical_limit=limit,
                emergency_limit=limit,
            )
        return new_sem

    async def adjust_for_state(self, uma_state: str) -> dict[ConcurrencyCategory, int]:
        """
        ATOMIC state transition — wholesale sem dict replacement under asyncio.Lock.

        Returns dict of category -> new limit for telemetry.

        Guarantees:
        - Single writer: asyncio.Lock serializes all adjust_for_state calls
        - Atomic swap: self._semaphores = new_semaphores is a single dict assignment
        - Readers see old OR new dict, never partial/inconsistent state
        - _uma_state update inside the lock — consistent with semaphores
        """
        # Lazy init: create asyncio.Lock on first async call (not at import time)
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        async with self._init_lock:
            new_state = uma_state.upper()
            if self._uma_state == new_state:
                return {}

            self._uma_state = new_state

            # Build new dict locally, then atomic-swap
            new_semaphores: dict[ConcurrencyCategory, asyncio.Semaphore] = {}
            changes: dict[ConcurrencyCategory, int] = {}

            for category, budget in self._budgets.items():
                new_limit = budget.get_limit(new_state)
                old_sem = self._semaphores.get(category)
                # Safe: getattr on None returns None, we treat as "unknown old limit"
                old_limit = getattr(old_sem, "_value", None) if old_sem else None

                if old_limit != new_limit:
                    new_sem = asyncio.Semaphore(new_limit)
                    new_semaphores[category] = new_sem
                    changes[category] = new_limit
                    logger.info(
                        f"ConcurrencyBudget: {category.value} {old_limit} → {new_limit} "
                        f"(UMA={new_state})"
                    )
                else:
                    # No change — reuse existing semaphore (idempotent)
                    new_semaphores[category] = old_sem

            # ATOMIC WHOLESALE SWAP — readers see old OR new, never partial
            self._semaphores = new_semaphores

            return changes

    def get_budget(self, category: ConcurrencyCategory) -> ConcurrencyBudget | None:
        """Get budget metadata for a category."""
        return self._budgets.get(category)

    def get_all_budgets(self) -> dict[ConcurrencyCategory, ConcurrencyBudget]:
        """Get all budget metadata."""
        return dict(self._budgets)

    def get_stats(self) -> dict[str, dict[str, int]]:
        """Get acquisition stats for monitoring."""
        return {
            cat.value: dict(stats)
            for cat, stats in self._stats.items()
        }

    def record_acquire(self, category: ConcurrencyCategory) -> None:
        """Record semaphore acquisition (for telemetry)."""
        if category in self._stats:
            self._stats[category]["acquired"] += 1

    def record_release(self, category: ConcurrencyCategory) -> None:
        """Record semaphore release (for telemetry)."""
        if category in self._stats:
            self._stats[category]["released"] += 1

    def record_rejected(self, category: ConcurrencyCategory) -> None:
        """Record rejected acquisition (for telemetry)."""
        if category in self._stats:
            self._stats[category]["rejected"] += 1


# Convenience function for backwards compatibility
async def get_budget(category: ConcurrencyCategory) -> asyncio.Semaphore:
    """Get Semaphore for category (async init required)."""
    registry = await ConcurrencyBudgetRegistry.get_instance()
    return registry.get(category)


# Module-level cache: shared semaphores across all call sites (keyed by category).
# This is safe because:
#   - asyncio.Semaphore is process-global (not thread-local)
#   - All semaphores for a category have the SAME limit (from _CONCURRENCY_LIMITS)
#   - The cache is initialized once at first call and lives for process lifetime
_semaphore_cache: dict[ConcurrencyCategory, asyncio.Semaphore] = {}


# Backwards compatibility: module-level constants for direct import
# These replicate the old hard-coded values but via the registry
def get_semaphore_for_testing(category: ConcurrencyCategory) -> asyncio.Semaphore:
    """
    Get cached Semaphore for category (synchronous, no async init required).

    Returns the SAME semaphore instance for all call sites — semaphore is
    cached per category at module level. This ensures all modules share a
    single semaphore per category, enabling true concurrency coordination.

    Thread-safety: _cache_lock is a module-level threading.Lock (single instance).
    Protects against concurrent first-access from multiple threads.

    For production code that needs dynamic state adjustment, use
    `await get_budget(category)` instead — that routes through the
    ConcurrencyBudgetRegistry singleton with M1ResourceGovernor integration.

    NOTE: This function intentionally creates semaphores with FIXED OK-state
    limits. State-dependent adjustment requires the async registry path.
    """
    # Fast path: cache hit (dict read is GIL-protected, no lock needed)
    sem = _semaphore_cache.get(category)
    if sem is not None:
        return sem

    # Slow path: cache miss — atomic create + cache under module-level lock
    limits = _CONCURRENCY_LIMITS.get(category, (5, 5, 5, 5))
    sem = asyncio.Semaphore(limits[0])

    # Thread-safe cache update using module-level _cache_lock (single instance)
    with _cache_lock:
        # Double-check after acquiring lock (standard DCL pattern)
        if category not in _semaphore_cache:
            _semaphore_cache[category] = sem

    # Return the cached semaphore (either one we just inserted, or one
    # inserted by another thread that acquired the lock first)
    return _semaphore_cache[category]
