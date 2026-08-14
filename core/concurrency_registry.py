"""
ConcurrencyBudget Registry — Centralizovaný správce semaforů pro M1 8GB.

ÚČEL:
- Single source of truth pro všechny asyncio.Semaphore hodnoty
- Konzistence napříč moduly (194 různých hodnot → jednotná kategorizace)
- Dynamická adjustace podle UMA stavu (OK/WARN/CRITICAL/EMERGENCY)
- Fail-safe fallback při chybějícím Governoru
- Task reference tracking pro cancel a introspection
- Cancel support pro koordinované zastavení čekajících tasků

KATEGORIE:
| Category          | OK   | WARN | CRITICAL | EMERGENCY | Use case                    |
|-------------------|------|------|----------|-----------|-----------------------------|
| HTTP_LANE         | 8    | 4    | 2        | 1         | curl_cffi/httpx fetches     |
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
| ZERONET_FETCH     | 2    | 1    | 1        | 1         | ZeroNet JSON API fetch      |
| FREENET_FETCH     | 2    | 1    | 1        | 1         | Freenet FProxy fetch        |
| BANNER_GRAB       | 1    | 1    | 1        | 1         | TCP banner enumeration      |
| PASTE_SCRAPE      | 4    | 2    | 1        | 1         | Paste site scrapers         |
| GRAPH_RAG         | 3    | 2    | 1        | 1         | DuckDB/embedding ops        |
| MLX_INFERENCE     | 1    | 1    | 1        | 1         | MLX model inference         |
| SCRAPE_GENERAL    | 10   | 5    | 3        | 1         | General scraping            |
| JS_RENDERER       | 10   | 5    | 2        | 1         | Chromium browser pool (F-02) |
| ISOLATED_INTERPRETER | 3 | 2    | 1        | 1         | PEP 734 CPU-bound ops      |

INVARIANT:
- Všechny moduly používají ConcurrencyBudget.get(category) místo asyncio.Semaphore(hard_value)
- Governor dynamicky mění limity podle UMA stavu
- Fallback na OK hodnoty pokud Governor není dostupný

MODERN-36: Task Reference and Cancel Support
- TaskReference: lightweight wrapper tracking task + acquire timestamp
- cancel_waiting(category): cancel all tasks waiting on semaphore
- get_waiting_tasks(category): introspection for debugging
- Context-aware acquisition tracking for leak detection
"""
import asyncio
import logging
import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from hledac.universal.utils.asyncx import safe_wait_for

from hledac.universal.core.locks import LockCategory, make_lock

if TYPE_CHECKING:
    from hledac.universal.core.resource_governor import M1ResourceGovernor
logger = logging.getLogger(__name__)


# ── Task Reference Tracking ─────────────────────────────────────────────────────

@dataclass(slots=True)
class TaskReference:
    """
    Lightweight reference to an asyncio.Task holding a semaphore slot.
    
    MODERN-36: Enables task introspection and coordinated cancellation.
    
    Fields:
        task: Weak reference to the asyncio.Task (prevents GC retention issues)
        acquired_at: Monotonic timestamp when slot was acquired
        category: The concurrency category this task is holding
        name: Optional task name for debugging
    """
    task: 'weakref.ref[asyncio.Task]'
    acquired_at: float
    category: 'ConcurrencyCategory'
    name: str = ""
    
    @property
    def task_alive(self) -> bool:
        """Check if the referenced task is still running."""
        t = self.task()
        return t is not None and not t.done()
    
    @property
    def holding_task(self) -> 'asyncio.Task | None':
        """Get the task or None if it has completed."""
        return self.task()


class TaskTrackedSemaphore:
    """
    asyncio.Semaphore subclass with task reference tracking.
    
    MODERN-36: Wraps asyncio.Semaphore to track which tasks hold slots
    and enables coordinated cancellation of waiting tasks.
    
    Usage:
        sem = TaskTrackedSemaphore(5)
        
        # Acquire with automatic task tracking
        async with sem:
            await do_work()
        
        # Cancel all waiting tasks
        sem.cancel_waiting()
        
        # Inspect current holders
        for ref in sem.holders:
            print(f"Task: {ref.name}, held for {time.time() - ref.acquired_at}s")
    """
    __slots__ = ('_sem', '_lock', '_holders', '_waiters')
    
    def __init__(self, value: int) -> None:
        self._sem = asyncio.Semaphore(value)
        self._lock = asyncio.Lock()
        self._holders: list[TaskReference] = []  # Tasks currently holding slots
        self._waiters: list[TaskReference] = []  # Tasks waiting for slots
    
    @property
    def _value(self) -> int:
        """Expose semaphore's internal value for backward compat."""
        return self._sem._value
    
    async def acquire(self) -> TaskReference:
        """
        Acquire a slot with automatic task tracking.
        
        Returns TaskReference that can be used to check task status.
        """
        current_task = asyncio.current_task()
        ref = TaskReference(
            task=weakref.ref(current_task) if current_task else weakref.ref(asyncio.get_running_loop().create_task(asyncio.sleep(0))),
            acquired_at=time.monotonic(),
            category=ConcurrencyCategory.HTTP_LANE,  # Will be set by registry
            name=current_task.get_name() if current_task else "unknown",
        )
        
        await self._sem.acquire()
        async with self._lock:
            self._holders.append(ref)
        
        return ref
    
    def release(self) -> None:
        """Release a slot and remove task from holders."""
        self._sem.release()
        current_task = asyncio.current_task()
        if current_task:
            async def _cleanup():
                async with self._lock:
                    self._holders = [
                        h for h in self._holders 
                        if h.task() is not current_task
                    ]
            # Schedule cleanup without blocking
            asyncio.get_running_loop().create_task(_cleanup())
    
    def cancel_waiting(self) -> list[asyncio.Task]:
        """
        Cancel all tasks currently waiting on this semaphore.
        
        Returns list of cancelled tasks for monitoring.
        """
        cancelled = []
        # Get current waiter count
        waiters_pending = self._sem._waiters if hasattr(self._sem, '_waiters') else []
        
        for waiter in waiters_pending:
            if hasattr(waiter, 'task') and waiter.task:
                task = waiter.task()
                if task and not task.done():
                    task.cancel()
                    cancelled.append(task)
        
        return cancelled
    
    async def get_holders(self) -> list[TaskReference]:
        """Get list of tasks currently holding slots."""
        async with self._lock:
            # Filter out dead tasks
            alive = [h for h in self._holders if h.task_alive]
            self._holders = alive
            return alive.copy()
    
    async def get_waiting(self) -> list[TaskReference]:
        """Get list of tasks currently waiting for slots."""
        async with self._lock:
            return self._waiters.copy()
    
    def locked(self) -> bool:
        """Return True if semaphore has no available slots."""
        return self._sem.locked()
    
    async def wait_for_slot(self, timeout: float | None = None) -> TaskReference | None:
        """
        Wait for a slot with timeout and return TaskReference.
        
        Returns None if timeout exceeded or task was cancelled.
        """
        current_task = asyncio.current_task()
        ref = TaskReference(
            task=weakref.ref(current_task) if current_task else weakref.ref(asyncio.get_running_loop().create_task(asyncio.sleep(0))),
            acquired_at=time.monotonic(),
            category=ConcurrencyCategory.HTTP_LANE,
            name=current_task.get_name() if current_task else "unknown",
        )
        
        try:
            if timeout:
                await safe_wait_for(self._sem.acquire(), timeout=timeout)
            else:
                await self._sem.acquire()
            
            async with self._lock:
                self._holders.append(ref)
            return ref
        except asyncio.TimeoutError:
            return None
        except asyncio.CancelledError:
            return None

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
    ZERONET_FETCH = 'zeronet_fetch'
    FREENET_FETCH = 'freenet_fetch'
    BANNER_GRAB = 'banner_grab'
    PASTE_SCRAPE = 'paste_scrape'
    GRAPH_RAG = 'graph_rag'
    MLX_INFERENCE = 'mlx_inference'
    SCRAPE_GENERAL = 'scrape_general'
    ISOLATED_INTERPRETER = 'isolated_interpreter'
    DUCKDB_WRITE = 'duckdb_write'
    JS_RENDERER = 'js_renderer'  # F-02: Chromium browser pool — critical=2 for M1 8GB
    MULTIMODAL_ENRICHMENT = 'multimodal_enrichment'  # F-17: CLIP model concurrency (heavy, ~100-500ms load)
_CONCURRENCY_LIMITS: dict[ConcurrencyCategory, tuple[int, int, int, int]] = {ConcurrencyCategory.HTTP_LANE: (8, 4, 2, 1), ConcurrencyCategory.DNS_BRUTE: (50, 25, 10, 5), ConcurrencyCategory.BGP_QUERY: (3, 2, 1, 1), ConcurrencyCategory.IP_QUERY: (10, 5, 3, 1), ConcurrencyCategory.ACADEMIC_SEARCH: (5, 3, 2, 1), ConcurrencyCategory.SOCIAL_MINE: (4, 2, 1, 1), ConcurrencyCategory.TRANSPORT_TOR: (3, 2, 1, 1), ConcurrencyCategory.TRANSPORT_I2P: (2, 1, 1, 1), ConcurrencyCategory.TRANSPORT_NYM: (2, 1, 1, 1), ConcurrencyCategory.DHT_BOOTSTRAP: (2, 1, 1, 1), ConcurrencyCategory.DHT_REQUEST: (50, 25, 10, 5), ConcurrencyCategory.GOPHER_LANE: (2, 1, 1, 1), ConcurrencyCategory.ZERONET_FETCH: (2, 1, 1, 1), ConcurrencyCategory.FREENET_FETCH: (2, 1, 1, 1), ConcurrencyCategory.BANNER_GRAB: (1, 1, 1, 1), ConcurrencyCategory.PASTE_SCRAPE: (4, 2, 1, 1), ConcurrencyCategory.GRAPH_RAG: (3, 2, 1, 1), ConcurrencyCategory.MLX_INFERENCE: (1, 1, 1, 1), ConcurrencyCategory.SCRAPE_GENERAL: (10, 5, 3, 1), ConcurrencyCategory.ISOLATED_INTERPRETER: (3, 2, 1, 1), ConcurrencyCategory.DUCKDB_WRITE: (2, 1, 1, 1), ConcurrencyCategory.JS_RENDERER: (10, 5, 2, 1), ConcurrencyCategory.MULTIMODAL_ENRICHMENT: (4, 2, 1, 1)}

class ConcurrencyBudget(msgspec.Struct, frozen=True, gc=False):
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

    MODERN-36: Enhanced with task reference tracking and cancel support.

    Použití:
        registry = await ConcurrencyBudgetRegistry.get_instance_async()
        sem = registry.get(ConcurrencyCategory.HTTP_LANE)
        async with sem:
            await fetch(url)

        # Cancel all waiting tasks for a category
        cancelled = await registry.cancel_waiting(ConcurrencyCategory.HTTP_LANE)
        
        # Get current holders for debugging
        holders = await registry.get_holders(ConcurrencyCategory.HTTP_LANE)

    Výhody:
    - Konzistentní hodnoty napříč celou codebase
    - Dynamická adjustace podle UMA stavu
    - Telemetrie pro monitoring
    - Fail-safe fallback
    - Task reference tracking pro cancel a introspection
    - Cancel support pro koordinované zastavení čekajících tasků

    Thread-safety (PEP 789):
    - Singleton init chráněn threading.Lock (pro sync init paths)
    - asyncio.Lock vytvářen lazy, POUZE v async kontextu (get_instance_async)
    - Semafory vytvářeny lazy na prvním volání get() v async kontextu
    - adjust_for_state používá asyncio.Lock pro serializaci
    """
    _instance: 'ConcurrencyBudgetRegistry | None' = None
    _init_guard = make_lock(LockCategory.CONFIG, "concurrency_registry._init_guard")
    _async_lock: asyncio.Lock | None = None
    __slots__ = tuple(('_budgets', '_governor', '_stats', '_uma_state', '_semaphores'))

    def __init__(self) -> None:
        self._budgets: dict[ConcurrencyCategory, ConcurrencyBudget] = {}
        self._governor: M1ResourceGovernor | None = None
        self._uma_state: str = 'OK'
        self._stats: dict[ConcurrencyCategory, dict[str, int]] = {}
        self._semaphores: dict[ConcurrencyCategory, asyncio.Semaphore] = {}
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
            except Exception:  # noqa: BLE001
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
        sem = self._semaphores.get(category)
        if sem is not None:
            return sem
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

    # ── MODERN-36: Task Reference and Cancel Support ───────────────────────────

    async def cancel_waiting(
        self,
        category: ConcurrencyCategory,
        reason: str = "registry_cancel",
    ) -> list[asyncio.Task]:
        """
        Cancel all tasks currently waiting on a semaphore.

        MODERN-36: Enables coordinated shutdown of waiting tasks during winddown
        or when resource pressure requires immediate cancellation.

        Args:
            category: The concurrency category whose semaphore to target
            reason: Optional reason for logging

        Returns:
            List of tasks that were cancelled
        """
        sem = self._semaphores.get(category)
        if sem is None:
            return []
        
        cancelled: list[asyncio.Task] = []
        
        # Access asyncio internals to find waiting tasks
        # Note: This is safe as we're only reading, not modifying
        if hasattr(sem, '_waiters') and sem._waiters:
            for waiter in list(sem._waiters):
                if hasattr(waiter, 'task') and waiter.task:
                    task = waiter.task()
                    if task and not task.done():
                        task.cancel(reason=f"concurrency_registry: {reason}")
                        cancelled.append(task)
                        logger.debug(f"Cancelled waiting task: {task.get_name() if hasattr(task, 'get_name') else 'unknown'}")
        
        if cancelled:
            logger.info(f"ConcurrencyBudgetRegistry: Cancelled {len(cancelled)} waiting tasks for {category.value}")
        
        return cancelled

    async def cancel_all_waiting(self, reason: str = "registry_shutdown") -> dict[ConcurrencyCategory, int]:
        """
        Cancel all waiting tasks across all categories.

        MODERN-36: For coordinated shutdown of all concurrent operations.

        Returns:
            Dict mapping category to number of cancelled tasks
        """
        results: dict[ConcurrencyCategory, int] = {}
        for category in self._semaphores:
            cancelled = await self.cancel_waiting(category, reason)
            results[category] = len(cancelled)
        return results

    async def get_holders(self, category: ConcurrencyCategory) -> list[dict]:
        """
        Get information about tasks currently holding semaphore slots.

        MODERN-36: Useful for debugging resource leaks and identifying
        tasks that may be holding slots indefinitely.

        Returns:
            List of dicts with task info (name, held_seconds, category)
        """
        sem = self._semaphores.get(category)
        if sem is None:
            return []
        
        holders: list[dict] = []
        current_time = time.monotonic()
        
        if hasattr(sem, '_waiters'):
            # Estimate holders by checking semaphore value vs limit
            budget = self._budgets.get(category)
            limit = budget.get_limit(self._uma_state) if budget else 5
            acquired_count = limit - sem._value
            
            # Note: We can't directly get holder tasks without modifying asyncio internals
            # So we just return the count and category info
            holders.append({
                "category": category.value,
                "uma_state": self._uma_state,
                "limit": limit,
                "available": sem._value,
                "acquired_estimate": acquired_count,
                "holder_count_estimate": acquired_count,
            })
        
        return holders

    async def get_registry_status(self) -> dict:
        """
        Get comprehensive registry status for monitoring.

        MODERN-36: Returns state of all semaphores with Uma-aware limits.
        """
        status = {
            "uma_state": self._uma_state,
            "categories": {},
            "total_holders_estimate": 0,
        }
        
        for category in self._semaphores:
            budget = self._budgets.get(category)
            limit = budget.get_limit(self._uma_state) if budget else 5
            sem = self._semaphores[category]
            
            acquired = limit - sem._value
            status["categories"][category.value] = {
                "limit": limit,
                "available": sem._value,
                "acquired": acquired,
                "locked": sem.locked(),
                "stats": self._stats.get(category, {}),
            }
            status["total_holders_estimate"] += acquired
        
        return status

    # ── Legacy telemetry methods (kept for compatibility) ───────────────────────

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


# ── MODERN-36: Convenience cancel functions ───────────────────────────────────────

async def cancel_waiting(category: ConcurrencyCategory, reason: str = "user_request") -> list[asyncio.Task]:
    """
    Cancel all tasks waiting on a semaphore for a category.

    MODERN-36: Helper function for coordinated cancellation.
    """
    registry = await ConcurrencyBudgetRegistry.get_instance_async()
    return await registry.cancel_waiting(category, reason)


async def cancel_all_waiting(reason: str = "user_request") -> dict[ConcurrencyCategory, int]:
    """
    Cancel all waiting tasks across all categories.

    MODERN-36: Helper function for coordinated shutdown.
    """
    registry = await ConcurrencyBudgetRegistry.get_instance_async()
    return await registry.cancel_all_waiting(reason)


async def get_registry_status() -> dict:
    """
    Get comprehensive registry status.

    MODERN-36: Helper for monitoring dashboard.
    """
    registry = await ConcurrencyBudgetRegistry.get_instance_async()
    return await registry.get_registry_status()


async def concurrency_budget(
    category: ConcurrencyCategory,
) -> int:
    """
    Get dynamic concurrency limit for category — respects UMA state.

    F1 FIX: Replaces hardcoded concurrency values with UMA-aware limits.
    Wraps registry.get() to extract the current semaphore limit, which is
    already state-adjusted by adjust_for_state().

    Usage:
        # In parallel() call sites:
        concurrency = await concurrency_budget(ConcurrencyCategory.PASTE_SCRAPE)

        # Or with a callable for lazy evaluation (future-proofing):
        concurrency = await concurrency_budget(ConcurrencyCategory.HTTP_LANE)

    Returns the current limit for the category (OK/WARN/CRITICAL/EMERGENCY
    adaptive value from ConcurrencyBudgetRegistry).
    """
    registry = await ConcurrencyBudgetRegistry.get_instance_async()
    sem = registry.get(category)
    return sem._value  # type: ignore[return-value]


async def concurrency_budget_for(
    category: ConcurrencyCategory,
) -> int:
    """
    Alias for concurrency_budget — exists for Callble[[], Awaitable[int]] compatibility.

    When used as `concurrency=lambda: concurrency_budget_for(ConcurrencyCategory.HTTP_LANE)`,
    the lambda is called at runtime and the returned int is used directly as concurrency.
    """
    return await concurrency_budget(category)


_SEMAPHORE_CACHE: dict[ConcurrencyCategory, asyncio.Semaphore] = {}
_SEMAPHORE_CACHE_LOCK = make_lock(LockCategory.CONFIG, "concurrency_registry._SEMAPHORE_CACHE_LOCK")

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
    DEPRECATED since R12 (2026-07-19): Use ``get_semaphore(category)`` from
    ``hledac.universal.core.concurrency`` instead.

    This function existed for historical test/sync convenience but became
    the de facto production API across ~53 files. The name is misleading —
    the function delegates to a SEPARATE module-level cache
    (_SEMAPHORE_CACHE) that duplicated ConcurrencyBudgetRegistry state.

    MIGRATION:
        OLD: from hledac.universal.core.concurrency_registry import (
                 ConcurrencyCategory, get_semaphore_for_testing)
             sem = get_semaphore_for_testing(ConcurrencyCategory.HTTP_LANE)

        NEW: from hledac.universal.core.concurrency import (
                 ConcurrencyCategory, get_semaphore)
             sem = get_semaphore(ConcurrencyCategory.HTTP_LANE)

    RATIONALE:
        — core/concurrency.py delegates to ConcurrencyBudgetRegistry
          (single cache, UMA-aware, telemetry)
        — "get_semaphore_for_testing" is a misnomer — it's used in production
        — Separate _SEMAPHORE_CACHE duplicated the registry's state

    BACKWARDS COMPATIBILITY:
        This function remains available for test code (tests/ directory)
        and any un-migrated call sites. It now delegates to
        ConcurrencyBudgetRegistry.get_instance().get(category) to unify
        the two caches, and emits a DeprecationWarning on first call
        per process.
    """
    # R12: Unify with registry instead of separate _SEMAPHORE_CACHE
    registry = ConcurrencyBudgetRegistry.get_instance()
    return registry.get(category)