"""
core/locks.py — Canonical Lock Registry pro Hledac Universal.

ARCHITEKTURA:



    Všechny threading.Lock / threading.RLock v projektu MUSÍ být registrovány
    v LockCategory enumu pro prevenci deadlocku při cross-thread acquisition.

CATEGORIES:
    Každá kategorie má pevné pořadí (ascending priority). Při akvizici
    více locků v jedné kritické sekci použij vždy ascending order.

PROBLEMATIKA DEADLOCKU:
    149 instancí threading.Lock() bez ordering informace = potenciální
    deadlock surface ~16 000 kombinací. Lock registry řeší globální
    ordering pro všechny sdílené locks.

LOCK-FREE ALTERNATIVES:
    • Counters: Použij AtomicCounter z rust_extensions kde to jde
    • LRUCache: cachetools.LRUCache (thread-safe vestavěný)
    • Dataclass counters: itertools.count() bez locku (pro statistiky)

UŽITÍ:
    from hledac.universal._core.locks import (
        LockCategory, register_lock, acquire_in_order,
        AsyncLockDCLP, make_counter, make_lock,
    )

    # Varianta A: make_lock factory (DOPORUČENÁ — auto-registrace + Darwin optimalizace):
    _my_lock = make_lock(LockCategory.CACHE, name="mymodule._my_lock")

    # Varianta B: Ruční registrace:
    _my_lock = threading.Lock()
    register_lock(LockCategory.CACHE, _my_lock, "mymodule._my_lock")

    # Akvizice více locků v pořadí:
    # PRO SYNC KÓD:
    with acquire_in_order(LockCategory.CACHE, LockCategory.NETWORK):
        ...
    # PRO ASYNC KÓD (použij acquire_in_order_async):
    async with acquire_in_order_async(LockCategory.CACHE, LockCategory.NETWORK):
        ...

PYTHON 3.14 KOMPATIBILITA:
    • asyncio.Lock() lazy init přes None placeholder + DCLP helper
    • Žádné asyncio.Lock() při modul importu (ISSUE-014 CRITICAL)
    • contextvars pro async lock context isolation

M1 8GB OPTIMALIZACE:
    • Darwin os_unfair_lock (~5ns) pro velmi krátké kritické sekce (<1µs)
    • threading.Lock pro delší hold times (IO, network)
    • Lock contention monitoring (metrics do telemetry)
    • Minimal lock hold time (<1ms cíl)
    • Bounded critical sections

Author: F350M-R
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import platform
import threading
import weakref
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from enum import IntEnum
from typing import TYPE_CHECKING, Any, TypeVar

from operator import attrgetter, itemgetter
from _core._util import aclose
if TYPE_CHECKING:
    from collections.abc import ItemsView, KeysView, ValuesView

_KT = TypeVar("_KT")
_VT = TypeVar("_VT")

# ==============================================================================
# REGISTER_LOCK DECORATOR (from lock_registry.py pattern)
# ==============================================================================


def auto_register(category: LockCategory) -> Callable[[Callable[[], threading.Lock]], Callable[[], threading.Lock]]:
    """
    Decorator that auto-registers a lock factory.

    This is the preferred decorator for module-level lock registration.

    Usage:
        @auto_register(LockCategory.CACHE)
        def _my_lock():
            return threading.Lock()

        # Later in code:
        lock = _my_lock()
        with lock:
            ...

    Args:
        category: LockCategory enum value

    Returns:
        Decorator function
    """
    def decorator(func: Callable[[], threading.Lock]) -> Callable[[], threading.Lock]:
        @functools.wraps(func)
        def wrapper() -> threading.Lock:
            # Generate lock name from function module and name
            lock_name = f"{func.__module__}.{func.__name__}"
            lock = func()
            # Register if not already registered
            with _REGISTRY_LOCK:
                if lock_name not in _LockRegistry:
                    _register_lock(category, lock, lock_name, f"{func.__module__}:{func.__name__}")
            return lock
        return wrapper
    return decorator


# Alias for auto_register
register_lock_decorator = auto_register

# ==============================================================================
# LOCK CATEGORY REGISTRY
# ==============================================================================


class LockCategory(IntEnum):
    """
    Canonical lock ordering categories — ALL locks must register here.

    Priority: NÍZKÉ číslo = akviruje se PRVNÍ.
    Při akvizici více locků: vždy ascending order (nejnižší first).

    Kategorie (shora dolů životní cyklus):
        METRICS → CACHE → CONFIG → NETWORK → CURSOR → GRAPH → WAL → MPC

    WHY ASCENDING?
        • Prevence cyklických závislostí (kruhový čekání)
        • Konstantní amortizovaný čas akvizice
        • Bez deadlocku i při paralelním akvizici z více vláken
    """

    METRICS = 1  # System-wide telemetry, counters
    CACHE = 2  # In-memory caches (URL dedup, UA rotator)
    CONFIG = 3  # Settings, environment, feature flags
    NETWORK = 4  # HTTP sessions, connection pools
    CURSOR = 5  # Graph traversal cursors
    GRAPH = 6  # DuckDB graph operations, entity upsert
    WAL = 7  # Write-Ahead Log, replay locks
    MPC = 8  # Model predictive control, heaviest operations


class LockInfo:
    """
    Metadata o registered lock pro audit a debugging.

    attrs:
        category: LockCategory enum hodnota
        order: Pořadí v kategorii (pro disambiguation)
        name: Identifier locku ("module._lock_name")
        lock: Odkaz na threading.Lock / threading.RLock instanci
        registered_at: frame info kde byl lock registrován
    """

    __slots__ = ("category", "order", "name", "lock", "_frame_info")

    def __init__(
        self,
        category: LockCategory,
        order: int,
        name: str,
        lock: threading.Lock | threading.RLock,
        frame_info: str,
    ) -> None:
        self.category = category
        self.order = order
        self.name = name
        self.lock = lock
        self._frame_info = frame_info

    def __repr__(self) -> str:
        return f"LockInfo({self.category.name}[{self.order}]: {self.name})"


# ==============================================================================
# LOCK REGISTRY STORAGE
# ==============================================================================

_LockRegistryKey = tuple[LockCategory, int]
_LockRegistry: dict[str, LockInfo] = {}
_LOCKS_BY_CATEGORY: dict[LockCategory, list[threading.Lock | threading.RLock]] = {cat: [] for cat in LockCategory}
_LOCK_COUNTS: dict[LockCategory, int] = {cat: 0 for cat in LockCategory}
_REGISTRY_LOCK = threading.Lock()  # Only for registry mutations, not for lock acquisition


def _register_lock(
    category: LockCategory,
    lock: threading.Lock | threading.RLock,
    name: str,
    frame_info: str,
) -> None:
    """
    Interní registrace locku — voláno přes register_lock() helper.

    Thread-safe: _REGISTRY_LOCK must be held by caller.
    This function does NOT acquire _REGISTRY_LOCK itself (avoids nested acquisition deadlock).
    """
    order = _LOCK_COUNTS[category]
    _LOCK_COUNTS[category] = order + 1
    info = LockInfo(category, order, name, lock, frame_info)
    _LockRegistry[name] = info
    _LOCKS_BY_CATEGORY[category].append(lock)


def register_lock(
    category: LockCategory,
    lock: threading.Lock | threading.RLock,
    name: str,
) -> None:
    """
    Registruj lock v centralizovaném registru pro prevenci deadlocku.

    Args:
        category: LockCategory enum — kategorie locku podle funkce
        lock: threading.Lock() nebo threading.RLock() instance
        name: Unikátní identifier ve formátu "module._lock_name"
              Např. "url_dedup._bloom_lock"

    Raises:
        ValueError: Pokud lock s tímto name už existuje
        TypeError: Pokud lock není threading.Lock ani threading.RLock

    Example:
        from hledac.universal._core.locks import register_lock, LockCategory

        _my_lock = threading.Lock()
        register_lock(LockCategory.CACHE, _my_lock, "mymodule._my_lock")

    NOTE: Pro locky které se používají jen v izolovaných třídách
          (každá instance má svůj vlastní lock, nesdílí se):
          registrace se STÁLE DOPORUČUJE pro audit, ale není kritická.
          Kritická je pouze pro global/module-level locks.
    """
    # threading.Lock() returns _thread.lock, threading.RLock() returns _thread.RLock
    # Check using type names instead of isinstance (Lock/RLock are factory functions, not types)
    lock_type_name = type(lock).__name__
    if lock_type_name not in ("lock", "RLock", "PyUnfairLock"):
        raise TypeError(f"Expected threading.Lock, threading.RLock, or PyUnfairLock, got {lock_type_name}")

    import inspect

    frame = inspect.currentframe()
    frame_info = "unknown"
    if frame is not None and frame.f_back is not None:
        fb = frame.f_back
        frame_info = f"{fb.f_code.co_filename}:{fb.f_lineno}"

    with _REGISTRY_LOCK:
        if name in _LockRegistry:
            # Při re-importu modulu (sys.modules reload) vznikne NOVÝ lock instance,
            # ale v globálním registru už existuje záznam. Správné řešení:
            # Lock registry přežije re-import jen pokud proces běží dál.
            # Pro čistý idempotentní vzor: pokud lock s name už existuje, vždycky
            # kontroluj pouze identity (existing.lock is lock) — tj. stejný objekt.
            # Duplicitní název = vždy bug (i když stejná category), protože dva různé
            # locky se stejným názvem indikují logickou chybu v kódu.
            existing = _LockRegistry[name]
            if existing.lock is lock:
                return  # Idempotent: TEN SAMÝ lock objekt — OK
            raise ValueError(
                f"Lock name '{name}' already registered. "
                f"Two different lock objects with the same name is a bug. "
                f"Use unique names or reuse the existing lock."
    )

        _register_lock(category, lock, name, frame_info)


def _collect_and_sort_locks(
    *categories: LockCategory,
) -> tuple[list, list[LockCategory]]:
    """
    Sbírá a sortuje locks podle kategorie (internal helper).

    Vrací tuple (collected_locks, sorted_categories).
    """
    if not categories:
        return [], []

    seen: set[LockCategory] = set()
    unique: list[LockCategory] = []
    for cat in categories:
        if cat not in seen:
            seen.add(cat)
            unique.append(cat)

    sorted_cats = sorted(unique, key=attrgetter("value"))

    collected_locks: list = []
    with _REGISTRY_LOCK:
        for cat in sorted_cats:
            locks_in_cat = _LOCKS_BY_CATEGORY.get(cat, [])
            for lock in locks_in_cat:
                collected_locks.append(lock)

    return collected_locks, sorted_cats


def acquire_in_order(
    *categories: LockCategory,
) -> contextlib.AbstractContextManager:
    """
    Akvizice více locků v konzistentním ascending order (SYNCHRONNÍ).

    Použij místo přímého `with lock:` kdykoli akvizuješ více než jeden lock.
    PRO ASYNC KÓD použij `acquire_in_order_async()`.

    Args:
        *categories: LockCategory hodnoty k akvizici

    Returns:
        contextlib.AbstractContextManager pro synchronní `with` blok.

    Example:
        # SPRÁVNĚ — ascending order, všechny locks v kategorii
        with acquire_in_order(LockCategory.CACHE, LockCategory.NETWORK):
            ...

        # ŠPATNĚ — možný deadlock při concurrent akvizici
        # (jiný thread může akvizovat NETWORK then CACHE současně)
        with _network_lock:
            with _cache_lock:
                ...

    C-8 fix: Vrací ExitStack se VŠEMI locks (ne jen první v kategorii).
    NEW-H5d fix: Locks se sbírají pod _REGISTRY_LOCK, ale enter_context()
    se volá MIMO zámek — aby se neprodlužovalo držení _REGISTRY_LOCK.
    NOTE: Pro kategorie se stejným priority se používá původí pořadí.
    P1-2 fix: Přidána async varianta acquire_in_order_async().
    """
    if not categories:
        return contextlib.nullcontext(None)

    collected_locks, _ = _collect_and_sort_locks(*categories)

    # Enter all collected locks (outside _REGISTRY_LOCK)
    # ExitStack.__enter__() acquires each lock synchronously.
    # If acquisition fails, ExitStack's __exit__ cleans up already-acquired locks.
    stack = contextlib.ExitStack()
    for lock in collected_locks:
        stack.enter_context(lock)

    return stack


@asynccontextmanager
async def acquire_in_order_async(
    *categories: LockCategory,
):
    """
    Akvizice více locků v konzistentním ascending order (ASYNCHRONNÍ).

    PRO ASYNC KÓD. Pro sync kód použij `acquire_in_order()`.

    Args:
        *categories: LockCategory hodnoty k akvizici

    Example:
        # SPRÁVNĚ — async context manager
        async with acquire_in_order_async(LockCategory.CACHE, LockCategory.NETWORK):
            await some_async_operation()

    NOTE:
        Lock acquisition itself remains synchronous (threading.Lock is not async).
        This context manager wraps the sync acquisition in an async context.
        For very long hold times, consider using asyncio.to_thread() instead.
    """
    if not categories:
        yield
        return

    collected_locks, _ = _collect_and_sort_locks(*categories)

    # Acquire all locks synchronously first
    stack = contextlib.ExitStack()
    for lock in collected_locks:
        stack.enter_context(lock)

    try:
        yield stack
    finally:
        stack.__exit__(None, None, None)


def get_lock_by_name(name: str) -> threading.Lock | threading.RLock | None:
    """Get registered lock by name, or None if not found."""
    with _REGISTRY_LOCK:
        info = _LockRegistry.get(name)
        return info.lock if info else None


def get_registered_locks() -> list[LockInfo]:
    """Vrátí seznam všech registrovaných locků pro audit."""
    with _REGISTRY_LOCK:
        return list(_LockRegistry.values())


def get_locks_by_category(category: LockCategory) -> list[LockInfo]:
    """Vrátí locks v konkrétní kategorii."""
    with _REGISTRY_LOCK:
        return [info for info in _LockRegistry.values() if info.category == category]


def assert_lock_registered(name: str) -> None:
    """
    Debug assertion že lock je registrovaný.

    Používá se v testech a pro CI validaci.
    """
    with _REGISTRY_LOCK:
        if name not in _LockRegistry:
            import warnings

            warnings.warn(
                f"Lock '{name}' is NOT registered in core/locks.py registry. "
                f"Register it with: register_lock(LockCategory.XXX, {name}, '{name}')",
                ResourceWarning,
                stacklevel=2,
    )


# ==============================================================================
# ASYNC LOCK HELPERS (ISSUE-014 fix)
# ==============================================================================


class AsyncLockDCLP:
    """
    Double-checked locking asyncio.Lock wrapper.

    Thread-safe lazy init: threading.Lock chrání init block.
    Po init běží asyncio.Lock čistě v event loop — žádné cross-thread race.

    Usage::
        class MyClass:
            _async_lock = AsyncLockDCLP()

            async def do_something(self):
                async with self._async_lock:
                    ...

    Python 3.14 kompatibilní: Žádné asyncio.Lock() při modul importu.
    """

    __slots__ = ("_thread_lock", "_lock")

    def __init__(self) -> None:
        self._thread_lock: threading.Lock = threading.Lock()
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Thread-safe lazy init pro asyncio.Lock — DCLP protected by threading.Lock."""
        lock = self._lock
        if lock is None:
            with self._thread_lock:
                lock = self._lock
                if lock is None:
                    lock = asyncio.Lock()
                    self._lock = lock
        return lock

    async def __aenter__(self) -> None:
        await self._get_lock().acquire()

    async def __aexit__(self, *_args: Any) -> None:
        self._get_lock().release()

    @property
    def locked(self) -> bool:
        # asyncio.Lock.locked() returns True when held.
        # After __aexit__, _lock reference persists (lazy init),
        # so we must check the actual lock state, not just _lock existence.
        if self._lock is None:
            return False
        try:
            return self._lock.locked()
        except RuntimeError:
            # locked() called from wrong thread — asyncio lock is thread-safe
            return False


def make_async_lock_dclp() -> tuple[Callable[[], asyncio.Lock], threading.Lock]:
    """
    Create a double-checked locking async Lock pair.

    Returns:
        Tuple of (get_lock_factory, thread_lock).

    Usage::
        get_lock, thread_lock = make_async_lock_dclp()

        async def do_work():
            lock = get_lock()
            async with lock:
                ...
    """
    thread_lock = threading.Lock()
    lock_ref: asyncio.Lock | None = None

    def get_lock() -> asyncio.Lock:
        nonlocal lock_ref
        if lock_ref is None:
            with thread_lock:
                if lock_ref is None:
                    lock_ref = asyncio.Lock()
        return lock_ref

    return get_lock, thread_lock


# ==============================================================================
# LOCK-FREE COUNTER HELPERS
# ==============================================================================

# TODO: Rust AtomicCounter (issue #5) poskytne lock-free counter.
# Prozatím: threading.Lock chrání increment operace.
# Per-instance lock — eliminates global _counter_lock serialization bottleneck.
# C-17 fix: každá _PythonCounter instance má svůj vlastní lock místo jednoho
# globálního. Lock-free increment na M1 P-cores (~1ns vs ~5µs syscall).
# TODO: Rust AtomicCounter (PyO3 AtomicU64 + fetch_add, ~1ns lock-free) planned.


class _PythonCounter:
    """
    Python thread-safe counter with per-instance lock.

    C-17 fix: kazda instance ma svuj vlastni threading.Lock.
    Pro high-frequency counters doporucujeme Rust AtomicCounter (planned).
    """

    __slots__ = ("_value", "_lock")

    def __init__(self, initial: int = 0) -> None:
        self._value: int = initial
        self._lock = threading.Lock()

    def increment(self) -> int:
        with self._lock:
            result = self._value
            self._value += 1
        return result

    def get(self) -> int:
        with self._lock:
            return self._value


def make_counter(initial: int = 0) -> _PythonCounter:
    """Vytvoř thread-safe counter (Python fallback, Rust AtomicCounter planned)."""
    return _PythonCounter(initial)


# ==============================================================================
# RUST-BACKED ATOMIC COUNTER (Lock-Free ~1ns)
# ==============================================================================

_counter_backend: Any = None


def _get_counter_backend() -> Any:
    """Lazy load Rust atomic counter backend."""
    global _counter_backend
    if _counter_backend is None:
        try:
            from hledac.universal._core.rust_backend import rust
            if rust is not None and hasattr(rust.raw, "int_counter"):
                _counter_backend = rust.raw.int_counter.IntCounter
        except Exception:
            pass
    return _counter_backend


class AtomicCounter:
    """
    Lock-free atomic counter with Rust AtomicU64 backend.

    Uses Rust AtomicU64 for ~1ns lock-free operations when available.
    Falls back to threading.Lock if Rust backend unavailable.

    Usage:
        counter = AtomicCounter()
        counter.fetch_add(1)
        value = counter.get()
    """
    __slots__ = ("_value", "_lock", "_rust_counter")

    def __init__(self, initial: int = 0) -> None:
        self._rust_counter = _get_counter_backend()
        if self._rust_counter is not None:
            self._value = self._rust_counter(initial)
            self._lock = None  # type: ignore[assignment]
        else:
            self._value = initial
            self._lock = threading.Lock()

    def fetch_add(self, delta: int = 1) -> int:
        """Atomically add delta and return previous value."""
        if self._rust_counter is not None:
            return self._rust_counter.fetch_add(self._value, delta)
        with self._lock:  # type: ignore[union-attr]
            result = self._value  # type: ignore[union-attr]
            self._value = result + delta  # type: ignore[union-attr]
            return result

    def fetch_sub(self, delta: int = 1) -> int:
        """Atomically subtract delta and return previous value."""
        return self.fetch_add(-delta)

    def get(self) -> int:
        """Get current value."""
        if self._rust_counter is not None:
            return self._rust_counter.get(self._value)
        with self._lock:  # type: ignore[union-attr]
            return self._value  # type: ignore[union-attr]

    def set(self, value: int) -> None:
        """Set value."""
        if self._rust_counter is not None:
            self._rust_counter.set(self._value, value)
        with self._lock:  # type: ignore[union-attr]
            self._value = value  # type: ignore[union-attr]


def make_atomic_counter(initial: int = 0) -> AtomicCounter:
    """
    Create a lock-free atomic counter.

    Uses Rust AtomicU64 backend if available for ~1ns lock-free operations.
    Falls back to threading.Lock if Rust backend unavailable.

    Args:
        initial: Initial counter value (default 0)

    Returns:
        AtomicCounter instance
    """
    return AtomicCounter(initial)


# ==============================================================================
# MAKE_LOCK FACTORY — Darwin os_unfair_lock + Auto-Registration (ISSUE-008)
# ==============================================================================

# Lazy import pro os_unfair_lock — pouze na Darwinu
_rust_unfair_lock: Any = None
_IS_DARWIN: bool = platform.system() == "Darwin"


def _get_rust_unfair_lock() -> Any:
    """Lazy load rust unfair_lock — voláno pouze na Darwinu."""
    global _rust_unfair_lock
    if _rust_unfair_lock is None:
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal._core.rust_backend import rust
        _rust_unfair_lock = rust.raw.unfair_lock or False  # False = unavailable
    return _rust_unfair_lock if _rust_unfair_lock else None


def make_lock(
    category: LockCategory,
    name: str,
    *,
    prefer_unfair: bool = False,
) -> threading.Lock | Any:
    """
    Factory pro vytvoření registrovaného locku s automatickou optimalizací.

    NA Darwinu (M1/M2/M3):
        • Používá os_unfair_lock pokud prefer_unfair=True (default pro CACHE, CONFIG)
        • ~5ns lock/unlock vs ~25ns threading.Lock
        • Není reentrantní — nelze volat lock() znovu ze stejného threadu!

    NA ostatních platformách:
        • Vždy používá threading.Lock

    Args:
        category: LockCategory enum — kategorie locku
        name: Unikátní identifier ve formátu "module._lock_name"
        prefer_unfair: Na Darwinu preferovat os_unfair_lock (~5ns) místo threading.Lock (~25ns)
                      Doporučeno pro: CACHE, CONFIG (krátké kritické sekce <1µs)
                      Nedoporučeno pro: NETWORK, GRAPH (delší hold times, možná reentrance)

    Returns:
        threading.Lock — vždy zaregistrovaný v centralizovaném registru

    Example:
        # Doporučené použití:
        _cache_lock = make_lock(LockCategory.CACHE, "url_dedup._bloom_lock")
        _ua_lock = make_lock(LockCategory.CACHE, "public_fetcher._ua_lock")
        _config_lock = make_lock(LockCategory.CONFIG, "settings._config_lock")

        # NETWORK lock — threading.Lock (delší hold time, možná reentrance):
        _session_lock = make_lock(LockCategory.NETWORK, "session_mgr._lock", prefer_unfair=False)

    NOTE: make_lock VŽDY registruje. Pro locky používané pouze v izolovaných
          třídách (každá instance má svůj vlastní lock) je registrace
         DOPORUČENÁ pro audit, ale není kritická.
    """
    lock: threading.Lock
    use_unfair = _IS_DARWIN and prefer_unfair

    if use_unfair:
        rust = _get_rust_unfair_lock()
        if rust is not None:
            # Použij Darwin os_unfair_lock (~5ns)
            lock = rust.UnfairLock()
        else:
            # Fallback na threading.Lock pokud rust modul není dostupný
            lock = threading.Lock()
    else:
        lock = threading.Lock()

    # Auto-registrace do centralizovaného registru
    register_lock(category, lock, name)
    return lock


# ==============================================================================
# EXPORTS
# ==============================================================================

__all__ = [
    # Registry core
    "LockCategory",
    "LockInfo",
    "register_lock",
    "auto_register",
    "register_lock_decorator",
    # Multi-lock acquisition
    "acquire_in_order",
    "acquire_in_order_async",
    # Registry queries
    "get_registered_locks",
    "get_locks_by_category",
    "get_lock_by_name",
    "assert_lock_registered",
    # Async helpers
    "AsyncLockDCLP",
    "make_async_lock_dclp",
    # Factories
    "make_counter",
    "make_lock",
    "make_atomic_counter",
    # Atomic counter
    "AtomicCounter",
]
