"""
_core/lock_registry.py — Lock Registry Compatibility Layer

Tento soubor existuje pro zpětnou kompatibilitu s moduly, které importují z lock_registry.
Nový kód by měl importovat přímo z _core.locks.

Přesměrování na _core.locks:
    from _core.locks import (
        LockCategory,
        register_lock,
        auto_register,
        make_atomic_counter,
        AtomicCounter,
        ...
    )

ARCHITEKTURA:
    • _core/locks.py je nyní kanonický zdroj pro všechny lock funkce
    • Tento soubor re-exportuje pro zpětnou kompatibilitu
    • Registrace locků: _core/locks.py

MIGRACE:
    Staré:  from _core.lock_registry import LockCategory, register_lock
    Nové:   from _core.locks import LockCategory, register_lock
"""

# Re-export everything from _core.locks for backward compatibility
from _core.locks import (
    # Registry core
    LockCategory,
    LockInfo,
    register_lock,
    auto_register,
    register_lock_decorator,
    # Multi-lock acquisition
    acquire_in_order,
    acquire_in_order_async,
    # Registry queries
    get_registered_locks,
    get_locks_by_category,
    get_lock_by_name,
    assert_lock_registered,
    # Async helpers
    AsyncLockDCLP,
    make_async_lock_dclp,
    # Factories
    make_counter,
    make_lock,
    make_atomic_counter,
    # Atomic counter
    AtomicCounter,
)

# Legacy LockRegistry class alias (for code that may use it)
from _core.locks import _LockRegistry as LockRegistry

__all__ = [
    # Registry core
    "LockCategory",
    "LockInfo",
    "register_lock",
    "auto_register",
    "register_lock_decorator",
    "LockRegistry",
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
