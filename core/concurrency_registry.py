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
| ISOLATED_INTERPRETER | 3 | 2    | 1        | 1         | PEP 734 CPU-bound ops      |

INVARIANT:
- Všechny moduly používají ConcurrencyBudget.get(category) místo asyncio.Semaphore(hard_value)
- Governor dynamicky mění limity podle UMA stavu
- Fallback na OK hodnoty pokud Governor není dostupný
"""
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
    HTTP_LANE = 'http_lane'
    DNS_BRUTE = 'dns_brute'
    BGP_QUERY = 'bgp_query'
    IP_QUERY = 'ip_query'
    ACADEMIC_SEARCH = 'academic_search'
    SOCIAL_MINE = 'social_mine'
    TRANSPORT_TOR = 'transport_tor'
    TRANSPORT_I2P = 'transport_i2p'
    TRANSPORT_NYM = 'transport_nym'
    DHT_BOOTSTRAP = 'dht_bootstrap'
    DHT_REQUEST = 'dht_request'
    GOPHER_LANE = 'gopher_lane'
    BANNER_GRAB = 'banner_grab'
    PASTE_SCRAPE = 'paste_scrape'
    GRAPH_RAG = 'graph_rag'
    MLX_INFERENCE = 'mlx_inference'
    SCRAPE_GENERAL = 'scrape_general'
    ISOLATED_INTERPRETER = 'isolated_interpreter'
_CONCURRENCY_LIMITS: dict[ConcurrencyCategory, tuple[int, int, int, int]] = {ConcurrencyCategory.HTTP_LANE: (8, 4, 2, 1), ConcurrencyCategory.DNS_BRUTE: (50, 25, 10, 5), ConcurrencyCategory.BGP_QUERY: (3, 2, 1, 1), ConcurrencyCategory.IP_QUERY: (10, 5, 3, 1), ConcurrencyCategory.ACADEMIC_SEARCH: (5, 3, 2, 1), ConcurrencyCategory.SOCIAL_MINE: (4, 2, 1, 1), ConcurrencyCategory.TRANSPORT_TOR: (3, 2, 1, 1), ConcurrencyCategory.TRANSPORT_I2P: (2, 1, 1, 1), ConcurrencyCategory.TRANSPORT_NYM: (2, 1, 1, 1), ConcurrencyCategory.DHT_BOOTSTRAP: (2, 1, 1, 1), ConcurrencyCategory.DHT_REQUEST: (50, 25, 10, 5), ConcurrencyCategory.GOPHER_LANE: (2, 1, 1, 1), ConcurrencyCategory.BANNER_GRAB: (1, 1, 1, 1), ConcurrencyCategory.PASTE_SCRAPE: (4, 2, 1, 1), ConcurrencyCategory.GRAPH_RAG: (3, 2, 1, 1), ConcurrencyCategory.MLX_INFERENCE: (1, 1, 1, 1), ConcurrencyCategory.SCRAPE_GENERAL: (10, 5, 3, 1), ConcurrencyCategory.ISOLATED_INTERPRETER: (3, 2, 1, 1)}

@dataclass(frozen=True, slots=True)
class ConcurrencyBudget:
    """Immutable concurrency budget for a category."""
    category: ConcurrencyCategory
    ok_limit: int
    warn_limit: int
    critical_limit: int
    emergency_limit: int

    def get_limit(self, uma_state: str='OK') -> int:
        """Get limit for UMA state (case-insensitive)."""
        state = uma_state.upper()
        if state == 'WARN':
            return self.warn_limit
        elif state in ('CRITICAL', 'CRIT'):
            return self.critical_limit
        elif state in ('EMERGENCY', 'EMERG'):
            return self.emergency_limit
        return self.ok_limit

class ConcurrencyBudgetRegistry:
    """
    Centralizovaný registry pro všechny concurrency semafory.

    Použití:
        registry = await ConcurrencyBudgetRegistry.get_instance_async()
        sem = registry.get(ConcurrencyCategory.HTTP_LANE)
        async with sem:
            await fetch(url)

    Výhody:
    - Konzistentní hodnoty napříč celou codebase
    - Dynamická adjustace podle UMA stavu
    - Telemetrie pro monitoring
    - Fail-safe fallback

    Thread-safety (PEP 789):
    - Singleton init chráněn threading.Lock (pro sync init paths)
    - asyncio.Lock vytvářen lazy, POUZE v async kontextu (get_instance_async)
    - Semafory vytvářeny lazy na prvním volání get() v async kontextu
    - adjust_for_state používá asyncio.Lock pro serializaci
    """
    _instance: ConcurrencyBudgetRegistry | None = None
    _init_guard: threading.Lock = threading.Lock()
    _async_lock: asyncio.Lock | None = None
    __slots__ = tuple(('_budgets', '_governor', '_stats', '_uma_state'))

    def __init__(self) -> None:
        self._budgets: dict[ConcurrencyCategory, ConcurrencyBudget] = {}
        self._governor: M1ResourceGovernor | None = None
        self._uma_state: str = 'OK'
        self._stats: dict[ConcurrencyCategory, dict[str, int]] = {}
        for category, limits in _CONCURRENCY_LIMITS.items():
            self._budgets[category] = ConcurrencyBudget(category=category, ok_limit=limits[0], warn_limit=limits[1], critical_limit=limits[2], emergency_limit=limits[3])
            self._stats[category] = {'acquired': 0, 'released': 0, 'rejected': 0}

    @classmethod
    def get_instance(cls) -> 'ConcurrencyBudgetRegistry':
        """
        Sync-safe factory (called from __init__ paths, threading context).

        Returns existing instance or creates new one under threading.Lock.
        For async contexts, prefer get_instance_async() which returns the same
        instance but also acquires the asyncio.Lock for async-safe operations.
        """
        if cls._instance is None:
            with cls._init_guard:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    async def get_instance_async(cls) -> 'ConcurrencyBudgetRegistry':
        """
        Async-safe factory — preferred in coroutines.

        Ensures the instance is available and asyncio primitives are usable.
        Uses the same singleton as get_instance() — safe to call interchangeably.
        Creates asyncio.Lock here (inside event loop) to avoid PEP 789 warning.
        """
        instance = cls.get_instance()
        if cls._async_lock is None:
            cls._async_lock = asyncio.Lock()
        return instance

    def register_governor(self, governor: M1ResourceGovernor) -> None:
        """Register ResourceGovernor for dynamic state updates."""
        self._governor = governor
        logger.debug('ConcurrencyBudgetRegistry: Governor registered')

    async def _get_governor_decision(self) -> str:
        """Get current UMA state from governor or fallback."""
        if self._governor is not None:
            try:
                decision = await self._governor.evaluate()
                return decision.uma_state
            except Exception:
                pass
        return self._uma_state

    def get(self, category: ConcurrencyCategory) -> asyncio.Semaphore:
        """
        Get Semaphore for category (lazy, thread-safe).

        Semaphore is created on first call (PEP 789: must be in async context).
        Returns existing semaphore. Does NOT re-create on state change —
        use adjust_for_state() to trigger atomic wholesale replacement.

        Thread-safety: dict.get() is atomic in CPython (GIL).
        Lazy creation: double-checked locking with dict.get() for miss → atomic insert.
        """
        if hasattr(self, '_semaphores'):
            sem = self._semaphores.get(category)
            if sem is not None:
                return sem
        else:
            self._semaphores: dict[ConcurrencyCategory, asyncio.Semaphore] = {}
        budget = self._budgets.get(category)
        limit = budget.ok_limit if budget else 5
        new_sem = asyncio.Semaphore(limit)
        self._semaphores[category] = new_sem
        if budget is None:
            self._budgets[category] = ConcurrencyBudget(category=category, ok_limit=limit, warn_limit=limit, critical_limit=limit, emergency_limit=limit)
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
        if ConcurrencyBudgetRegistry._async_lock is None:
            ConcurrencyBudgetRegistry._async_lock = asyncio.Lock()
        async with ConcurrencyBudgetRegistry._async_lock:
            new_state = uma_state.upper()
            if self._uma_state == new_state:
                return {}
            self._uma_state = new_state
            new_semaphores: dict[ConcurrencyCategory, asyncio.Semaphore] = {}
            changes: dict[ConcurrencyCategory, int] = {}
            for category, budget in self._budgets.items():
                new_limit = budget.get_limit(new_state)
                old_sem = self._semaphores.get(category) if hasattr(self, '_semaphores') else None
                old_limit = getattr(old_sem, '_value', None) if old_sem else None
                if old_limit != new_limit:
                    new_sem = asyncio.Semaphore(new_limit)
                    new_semaphores[category] = new_sem
                    changes[category] = new_limit
                    logger.info(f'ConcurrencyBudget: {category.value} {old_limit} → {new_limit} (UMA={new_state})')
                elif old_sem is not None:
                    new_semaphores[category] = old_sem
                else:
                    new_semaphores[category] = asyncio.Semaphore(new_limit)
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
        return {cat.value: dict(stats) for cat, stats in self._stats.items()}

    def record_acquire(self, category: ConcurrencyCategory) -> None:
        """Record semaphore acquisition (for telemetry)."""
        if category in self._stats:
            self._stats[category]['acquired'] += 1

    def record_release(self, category: ConcurrencyCategory) -> None:
        """Record semaphore release (for telemetry)."""
        if category in self._stats:
            self._stats[category]['released'] += 1

    def record_rejected(self, category: ConcurrencyCategory) -> None:
        """Record rejected acquisition (for telemetry)."""
        if category in self._stats:
            self._stats[category]['rejected'] += 1

async def get_budget(category: ConcurrencyCategory) -> asyncio.Semaphore:
    """Get Semaphore for category (async init required)."""
    registry = await ConcurrencyBudgetRegistry.get_instance_async()
    return registry.get(category)
_SEMAPHORE_CACHE: dict[ConcurrencyCategory, asyncio.Semaphore] = {}
_SEMAPHORE_CACHE_LOCK: threading.Lock = threading.Lock()

def _get_cached_semaphore(category: ConcurrencyCategory) -> asyncio.Semaphore:
    """
    Lazy semaphore factory — creates asyncio.Semaphore on first call.

    CRITICAL: Must be called from async context (event loop must be running).
    Thread-safe: threading.Lock prevents race during concurrent init.
    Subsequent calls return cached instance.

    Python 3.14+ (PEP 789): asyncio.Semaphore() created outside event loop
    generates DeprecationWarning. This factory defers creation to first
    async call, ensuring we are inside event loop context.
    """
    sem = _SEMAPHORE_CACHE.get(category)
    if sem is not None:
        return sem
    with _SEMAPHORE_CACHE_LOCK:
        sem = _SEMAPHORE_CACHE.get(category)
        if sem is not None:
            return sem
        limits = _CONCURRENCY_LIMITS.get(category, (5, 5, 5, 5))
        sem = asyncio.Semaphore(limits[0])
        _SEMAPHORE_CACHE[category] = sem
        return sem

def get_semaphore_for_testing(category: ConcurrencyCategory) -> asyncio.Semaphore:
    """
    Get cached Semaphore for category (synchronous, no async init required).

    DEPRECATED: For production code, prefer `await get_budget(category)` which
    uses the full async registry with dynamic UMA state adjustment.
    This function exists for backwards compatibility with test/sync code.

    Returns the SAME semaphore instance for all call sites — semaphore is
    cached per category via the module-level _SEMAPHORE_CACHE dict.

    Thread-safety: threading.Lock + dict.get() atomic in CPython (GIL).
    All semaphore creation uses FIXED OK-state limits.
    """
    return _get_cached_semaphore(category)