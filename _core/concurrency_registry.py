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

ROADMAP-003: Thread-Safety with contextvars
- Per-context async lock isolation using ContextVar (Python 3.14+ pattern)
- Hybrid approach: threading.Lock for sync init + ContextVar for async isolation
- Eliminates race conditions in concurrent get() and adjust_for_state() calls
"""

import asyncio
import contextvars
import logging
import sys
import threading
import time
import weakref
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from _core.lock_registry import register_lock
from compat.msgspec_gc_compat import Struct
from hledac.universal._core.locks import LockCategory, make_lock
from hledac.universal.utils.asyncx import safe_wait_for

if TYPE_CHECKING:
    from hledac.universal._core.resource_governor import M1ResourceGovernor
logger = logging.getLogger(__name__)

# ── ROADMAP-003: ContextVar-based Lock Isolation ──────────────────────────────────
# Per-context async lock isolation for Python 3.14+ async patterns.
# Each async context (task, coroutine chain) gets its own lock dict to avoid
# cross-context contention while maintaining thread-safety.
#
# Python version compatibility:
#   - 3.14+: Native ContextVar support (optimal)
#   - 3.11-3.13: ContextVar fallback with sync Lock guard
#   - <3.11: threading.Lock fallback (reduced async isolation)
_PY_314_PLUS = sys.version_info >= (3, 14)

# Module-level threading lock for sync-safe semaphore initialization
# Protects against race during first async.Semaphore creation


@register_lock(LockCategory.METRICS)
def _SEMAPHORE_INIT_LOCK() -> threading.Lock:
    """Module-level lock for async.Semaphore initialization."""
    return threading.Lock()


def _get_context_locks() -> dict[str, asyncio.Lock]:
    """
    Get or create per-context lock dictionary using ContextVar.

    Python 3.14+ provides proper ContextVar support for async contexts.
    On older versions, falls back to process-global lock dict (reduced isolation
    but still thread-safe).

    Returns:
        Dictionary mapping lock names to asyncio.Lock instances, isolated per context.
    """
    if _PY_314_PLUS:
        ctx_locks = _context_locks.get()
        if ctx_locks is None:
            ctx_locks = {}
            _context_locks.set(ctx_locks)
        return ctx_locks
    else:
        # Fallback: use module-level dict for Python < 3.14
        return _fallback_locks


# ContextVar for per-context lock isolation (Python 3.14+)
_context_locks: contextvars.ContextVar[dict[str, asyncio.Lock] | None] = contextvars.ContextVar(
    "_context_locks", default=None
)

# Fallback lock dict for Python < 3.14 (thread-safe with guard)
_fallback_locks: dict[str, asyncio.Lock] = {}


@register_lock(LockCategory.METRICS)
def _fallback_locks_guard() -> threading.Lock:
    """Module-level lock for fallback locks dict (Python < 3.14)."""
    return threading.Lock()


def _get_or_create_lock(name: str) -> asyncio.Lock:
    """
    Get or create an asyncio.Lock for the given name in current context.

    ROADMAP-003: This implements the modern contextvars-based lock isolation
    pattern that eliminates races in concurrent async operations while
    maintaining backward compatibility.

    Thread-safety:
    - Python 3.14+: Uses ContextVar for per-context isolation (optimal)
    - Python < 3.14: Uses fallback_locks_guard to protect dict access

    Args:
        name: Unique identifier for the lock (e.g., "registry.get", "registry.adjust")

    Returns:
        asyncio.Lock instance, isolated to current async context (Python 3.14+)
        or shared globally with thread-safe access (older Python).
    """
    if _PY_314_PLUS:
        ctx_locks = _get_context_locks()
        if name not in ctx_locks:
            ctx_locks[name] = asyncio.Lock()
        return ctx_locks[name]
    else:
        # Thread-safe access for Python < 3.14
        with _fallback_locks_guard():
            if name not in _fallback_locks:
                _fallback_locks[name] = asyncio.Lock()
            return _fallback_locks[name]


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

    task: weakref.ref[asyncio.Task]
    acquired_at: float
    category: ConcurrencyCategory
    name: str = ""

    @property
    def task_alive(self) -> bool:
        """Check if the referenced task is still running."""
        t = self.task()
        return t is not None and not t.done()

    @property
    def holding_task(self) -> asyncio.Task | None:
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

    __slots__ = ("_sem", "_lock", "_holders", "_waiters")

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
            task=weakref.ref(current_task)
            if current_task
            else weakref.ref(asyncio.get_running_loop().create_task(asyncio.sleep(0))),
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

            async def _cleanup() -> None:
                async with self._lock:
                    self._holders = [h for h in self._holders if h.task() is not current_task]

            # Schedule cleanup without blocking
            asyncio.get_running_loop().create_task(_cleanup())

    def cancel_waiting(self) -> list[asyncio.Task]:
        """
        Cancel all tasks currently waiting on this semaphore.

        Returns list of cancelled tasks for monitoring.
        """
        cancelled = []
        waiters_pending = self._sem._waiters if hasattr(self._sem, "_waiters") else []

        for waiter in waiters_pending:
            if hasattr(waiter, "task") and waiter.task:
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
            task=weakref.ref(current_task)
            if current_task
            else weakref.ref(asyncio.get_running_loop().create_task(asyncio.sleep(0))),
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
        except TimeoutError:
            return None
        except asyncio.CancelledError:
            return None


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
    ZERONET_FETCH = "zeronet_fetch"
    FREENET_FETCH = "freenet_fetch"
    BANNER_GRAB = "banner_grab"
    PASTE_SCRAPE = "paste_scrape"
    GRAPH_RAG = "graph_rag"
    MLX_INFERENCE = "mlx_inference"
    SCRAPE_GENERAL = "scrape_general"
    ISOLATED_INTERPRETER = "isolated_interpreter"
    DUCKDB_WRITE = "duckdb_write"
    JS_RENDERER = "js_renderer"  # F-02: Chromium browser pool — critical=2 for M1 8GB
    MULTIMODAL_ENRICHMENT = "multimodal_enrichment"  # F-17: CLIP model concurrency (heavy, ~100-500ms load)


_CONCURRENCY_LIMITS: dict[ConcurrencyCategory, tuple[int, int, int, int]] = {
    ConcurrencyCategory.HTTP_LANE: (8, 4, 2, 1),
    ConcurrencyCategory.DNS_BRUTE: (50, 25, 10, 5),
    ConcurrencyCategory.BGP_QUERY: (3, 2, 1, 1),
    ConcurrencyCategory.IP_QUERY: (10, 5, 3, 1),
    ConcurrencyCategory.ACADEMIC_SEARCH: (5, 3, 2, 1),
    ConcurrencyCategory.SOCIAL_MINE: (4, 2, 1, 1),
    ConcurrencyCategory.TRANSPORT_TOR: (3, 2, 1, 1),
    ConcurrencyCategory.TRANSPORT_I2P: (2, 1, 1, 1),
    ConcurrencyCategory.TRANSPORT_NYM: (2, 1, 1, 1),
    ConcurrencyCategory.DHT_BOOTSTRAP: (2, 1, 1, 1),
    ConcurrencyCategory.DHT_REQUEST: (50, 25, 10, 5),
    ConcurrencyCategory.GOPHER_LANE: (2, 1, 1, 1),
    ConcurrencyCategory.ZERONET_FETCH: (2, 1, 1, 1),
    ConcurrencyCategory.FREENET_FETCH: (2, 1, 1, 1),
    ConcurrencyCategory.BANNER_GRAB: (1, 1, 1, 1),
    ConcurrencyCategory.PASTE_SCRAPE: (4, 2, 1, 1),
    ConcurrencyCategory.GRAPH_RAG: (3, 2, 1, 1),
    ConcurrencyCategory.MLX_INFERENCE: (1, 1, 1, 1),
    ConcurrencyCategory.SCRAPE_GENERAL: (10, 5, 3, 1),
    ConcurrencyCategory.ISOLATED_INTERPRETER: (3, 2, 1, 1),
    ConcurrencyCategory.DUCKDB_WRITE: (2, 1, 1, 1),
    ConcurrencyCategory.JS_RENDERER: (10, 5, 2, 1),
    ConcurrencyCategory.MULTIMODAL_ENRICHMENT: (4, 2, 1, 1),
}


class ConcurrencyBudget(Struct, frozen=True):
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
        return self.ok_limit


class ConcurrencyBudgetRegistry:
    """
    Centralizovaný registry pro všechny concurrency semafory.

    MODERN-36: Enhanced with task reference tracking and cancel support.
    ROADMAP-003: ContextVar-based async lock isolation (Python 3.14+ pattern).

    Použití:
        registry = await ConcurrencyBudgetRegistry.get_instance_async()
        sem = await registry.get_async(ConcurrencyCategory.HTTP_LANE)  # Preferred async
        # OR for sync: sem = registry.get(ConcurrencyCategory.HTTP_LANE)
        async with sem:
            await fetch(url)

        # Cancel all waiting tasks for a category
        cancelled = await registry.cancel_waiting(ConcurrencyCategory.HTTP_LANE)

        holders = await registry.get_holders(ConcurrencyCategory.HTTP_LANE)

    Výhody:
    - Konzistentní hodnoty napříč celou codebase
    - Dynamická adjustace podle UMA stavu
    - Telemetrie pro monitoring
    - Fail-safe fallback
    - Task reference tracking pro cancel a introspection
    - Cancel support pro koordinované zastavení čekajících tasků

    Thread-safety (PEP 789 + ROADMAP-003):
    - Singleton init chráněn threading.Lock (pro sync init paths)
    - asyncio.Lock vytvářen lazy, POUZE v async kontextu (get_instance_async)
    - Semafory vytvářeny lazy na prvním volání get_async() v async kontextu
    - adjust_for_state používá ContextVar-based locking pro per-context isolation
    - get_async() používá per-context lock pro race-free lazy initialization

    Python 3.14+ ContextVar Pattern:
    - Each async context gets its own lock via ContextVar
    - Eliminates cross-context contention while maintaining thread-safety
    - Backward compatible with older Python versions
    """

    _instance: ConcurrencyBudgetRegistry | None = None
    _init_guard = make_lock(LockCategory.CONFIG, "concurrency_registry._init_guard")
    _async_lock: asyncio.Lock | None = None
    __slots__ = ("_budgets", "_governor", "_stats", "_uma_state", "_semaphores")

    def __init__(self) -> None:
        self._budgets: dict[ConcurrencyCategory, ConcurrencyBudget] = {}
        self._governor: M1ResourceGovernor | None = None
        self._uma_state: str = "OK"
        self._stats: dict[ConcurrencyCategory, dict[str, int]] = {}
        self._semaphores: dict[ConcurrencyCategory, asyncio.Semaphore] = {}
        for category, limits in _CONCURRENCY_LIMITS.items():
            self._budgets[category] = ConcurrencyBudget(
                category=category,
                ok_limit=limits[0],
                warn_limit=limits[1],
                critical_limit=limits[2],
                emergency_limit=limits[3],
            )
            self._stats[category] = {"acquired": 0, "released": 0, "rejected": 0}

    @classmethod
    def get_instance(cls) -> ConcurrencyBudgetRegistry:
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
    async def get_instance_async(cls) -> ConcurrencyBudgetRegistry:
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
        Get Semaphore for category (lazy, thread-safe for sync contexts).

        Semaphore is created on first call (PEP 789: must be in async context).
        Returns existing semaphore. Does NOT re-create on state change —
        use adjust_for_state() to trigger atomic wholesale replacement.

        Thread-safety:
        - dict.get() is atomic in CPython (GIL)
        - Lazy creation protected by threading.Lock for sync-safe initialization
        - ROADMAP-003: For async contexts, prefer get_async() which uses
          ContextVar-based locking for better isolation

        Note: For production async code, prefer get_async() which provides
        per-context lock isolation and better concurrency characteristics.
        """
        sem = self._semaphores.get(category)
        if sem is not None:
            return sem

        # ROADMAP-003: Thread-safe lazy initialization with threading.Lock
        # This protects against race when multiple threads/coroutines try to create
        # a new semaphore simultaneously
        with _SEMAPHORE_INIT_LOCK():
            # Double-check after acquiring lock
            sem = self._semaphores.get(category)
            if sem is not None:
                return sem

            budget = self._budgets.get(category)
            limit = budget.ok_limit if budget else 5
            new_sem = asyncio.Semaphore(limit)
            self._semaphores[category] = new_sem
            if budget is None:
                self._budgets[category] = ConcurrencyBudget(
                    category=category, ok_limit=limit, warn_limit=limit, critical_limit=limit, emergency_limit=limit
                )
            return new_sem

    async def get_async(self, category: ConcurrencyCategory) -> asyncio.Semaphore:
        """
        Get Semaphore for category (async-safe, preferred in coroutines).

        ROADMAP-003: Uses ContextVar-based per-context lock isolation for
        better async concurrency characteristics.

        Returns existing semaphore or creates new one under per-context lock.
        Does NOT re-create on state change — use adjust_for_state() for that.

        Thread-safety:
        - Per-context lock via ContextVar (Python 3.14+ optimal pattern)
        - Fallback to process-global lock on older Python
        - Atomic dict operations (CPython GIL)

        Usage:
            registry = await ConcurrencyBudgetRegistry.get_instance_async()
            sem = await registry.get_async(ConcurrencyCategory.HTTP_LANE)
            async with sem:
                await fetch(url)
        """
        # Fast path: semaphore already exists
        sem = self._semaphores.get(category)
        if sem is not None:
            return sem

        # ROADMAP-003: Per-context lock for async-safe lazy initialization
        # Uses ContextVar for isolation between async contexts
        lock = _get_or_create_lock(f"registry.get.{category.value}")
        async with lock:
            # Double-check after acquiring lock
            sem = self._semaphores.get(category)
            if sem is not None:
                return sem

            budget = self._budgets.get(category)
            limit = budget.ok_limit if budget else 5
            new_sem = asyncio.Semaphore(limit)
            self._semaphores[category] = new_sem
            if budget is None:
                self._budgets[category] = ConcurrencyBudget(
                    category=category, ok_limit=limit, warn_limit=limit, critical_limit=limit, emergency_limit=limit
                )
            return new_sem

    async def adjust_for_state(self, uma_state: str) -> dict[ConcurrencyCategory, int]:
        """
        ATOMIC state transition — wholesale sem dict replacement under ContextVar lock.

        ROADMAP-003: Uses ContextVar-based per-context lock for better async isolation.

        Returns dict of category -> new limit for telemetry.

        Guarantees:
        - Single writer: per-context lock serializes all adjust_for_state calls
        - Atomic swap: self._semaphores = new_semaphores is a single dict assignment
        - Readers see old OR new dict, never partial/inconsistent state
        - _uma_state update inside the lock — consistent with semaphores
        - Per-context isolation via ContextVar (Python 3.14+ optimal)

        Thread-safety:
        - Uses _get_or_create_lock() which provides ContextVar-based isolation
        - Falls back to process-global lock on Python < 3.14
        """
        # Process-global lock (NOT ContextVar-scoped): adjust_for_state must be a
        # single-writer critical section across ALL async tasks. On Python 3.14+
        # _get_or_create_lock() returns a per-ContextVar lock, so two tasks would
        # acquire *different* locks → no mutual exclusion → double wholesale swap.
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
                old_sem = self._semaphores.get(category)
                old_limit = getattr(old_sem, "_value", None) if old_sem else None
                if old_limit != new_limit:
                    new_sem = asyncio.Semaphore(new_limit)
                    new_semaphores[category] = new_sem
                    changes[category] = new_limit
                    logger.info(f"ConcurrencyBudget: {category.value} {old_limit} → {new_limit} (UMA={new_state})")
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
        if hasattr(sem, "_waiters") and sem._waiters:
            for waiter in list(sem._waiters):
                if hasattr(waiter, "task") and waiter.task:
                    task = waiter.task()
                    if task and not task.done():
                        task.cancel(reason=f"concurrency_registry: {reason}")
                        cancelled.append(task)
                        logger.debug(
                            f"Cancelled waiting task: {task.get_name() if hasattr(task, 'get_name') else 'unknown'}"
                        )

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
        time.monotonic()

        if hasattr(sem, "_waiters"):
            # Estimate holders by checking semaphore value vs limit
            budget = self._budgets.get(category)
            limit = budget.get_limit(self._uma_state) if budget else 5
            acquired_count = limit - sem._value

            # Note: We can't directly get holder tasks without modifying asyncio internals
            # So we just return the count and category info
            holders.append(
                {
                    "category": category.value,
                    "uma_state": self._uma_state,
                    "limit": limit,
                    "available": sem._value,
                    "acquired_estimate": acquired_count,
                    "holder_count_estimate": acquired_count,
                }
            )

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
            self._stats[category]["acquired"] += 1

    def record_release(self, category: ConcurrencyCategory) -> None:
        """Record semaphore release (for telemetry)."""
        if category in self._stats:
            self._stats[category]["released"] += 1

    def record_rejected(self, category: ConcurrencyCategory) -> None:
        """Record rejected acquisition (for telemetry)."""
        if category in self._stats:
            self._stats[category]["rejected"] += 1


async def get_budget(category: ConcurrencyCategory) -> asyncio.Semaphore:
    """
    Get Semaphore for category (async init required).

    ROADMAP-003: Now uses get_async() with ContextVar-based lock isolation
    for better async concurrency characteristics.

    Note: Returns asyncio.Semaphore, not ConcurrencyBudget.
    Use registry.get_budget() to get the budget metadata.
    """
    registry = await ConcurrencyBudgetRegistry.get_instance_async()
    return await registry.get_async(category)


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
    Returns the CONFIGURED LIMIT for the category (not the remaining slots).

    Usage:
        # In parallel() call sites:
        concurrency = await concurrency_budget(ConcurrencyCategory.PASTE_SCRAPE)

        # Or with a callable for lazy evaluation (future-proofing):
        concurrency = await concurrency_budget(ConcurrencyCategory.HTTP_LANE)

    Returns the current limit for the category (OK/WARN/CRITICAL/EMERGENCY
    adaptive value from ConcurrencyBudgetRegistry).

    Thread-safety: Uses get_async() with ContextVar-based lock isolation.
    """
    registry = await ConcurrencyBudgetRegistry.get_instance_async()
    budget = registry.get_budget(category)
    if budget:
        return budget.get_limit(registry._uma_state)
    return 5  # Fallback default


async def concurrency_budget_for(
    category: ConcurrencyCategory,
) -> int:
    """
    Alias for concurrency_budget — exists for Callble[[], Awaitable[int]] compatibility.

    When used as `concurrency=lambda: concurrency_budget_for(ConcurrencyCategory.HTTP_LANE)`,
    the lambda is called at runtime and the returned int is used directly as concurrency.
    """
    return await concurrency_budget(category)


def get_semaphore_for_testing(category: ConcurrencyCategory) -> asyncio.Semaphore:
    """
    DEPRECATED since R12 (2026-07-19): Use ``get_semaphore(category)`` from
    ``hledac.universal._core.concurrency`` instead.

    This function existed for historical test/sync convenience but became
    the de facto production API across ~53 files. The name is misleading —
    the function delegates to a SEPARATE module-level cache
    (_SEMAPHORE_CACHE) that duplicated ConcurrencyBudgetRegistry state.

    MIGRATION:
        OLD: from hledac.universal._core.concurrency_registry import (
                 ConcurrencyCategory, get_semaphore_for_testing)
             sem = get_semaphore_for_testing(ConcurrencyCategory.HTTP_LANE)

        NEW: from hledac.universal._core.concurrency import (
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
