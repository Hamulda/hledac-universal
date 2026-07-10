"""
LanceDB Shared Connection Pool — Sprint F266-U6

PROBLEM: Multiple LanceDB stores each call lancedb.connect() independently.
Each connection object adds overhead, and on M1 8GB UMA this matters.

SOLUTION: Singleton registry that shares lancedb.connect() per unique path.
Multiple stores pointing to the same path reuse one connection object.

KEY FACTS about LanceDB connections:
- lancedb.connect() creates a connection OBJECT, not a new database
- Same path → same underlying data, different connection objects don't duplicate data
- Connection objects are lightweight (~few KB), but the pattern matters
- LanceDB supports multiple connections to the same database

POOL INVARIANTS (M1 8GB):
- Always-on, no feature flags
- Fail-safe: any error → fall back to direct lancedb.connect()
- Reference counting ensures proper cleanup
- Thread-safe via threading.Lock + asyncio.Lock
- Bounded: MAX_POOL_SIZE=16 connections (prevents runaway)
- No new public APIs beyond get_connection()
"""



import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Pool configuration
_MAX_POOL_SIZE = 16  # Maximum connections per pool
_POOL_WARM_UP = True  # Pre-warm default connections

# Global registry: path -> (connection, refcount, lock)
_registry: dict[str, tuple[Any, int, threading.Lock]] = {}
_registry_lock = threading.Lock()
_async_locks: dict[str, asyncio.Lock] = {}
_async_locks_lock = threading.Lock()  # Use threading.Lock for sync access to _async_locks dict


class LanceDBPoolError(Exception):
    """Pool error — used for debugging, never raised to callers."""
    pass


def _get_async_lock(path: str) -> asyncio.Lock:
    """Get or create async lock for a path (async-safe)."""
    # Fast path without locks for existing keys
    if path in _async_locks:
        return _async_locks[path]

    # Slow path: create new lock
    # Note: we can't hold _async_locks_lock while creating the lock
    # because asyncio.Lock creation is async-unsafe. Use a temporary sync lock.
    with _async_locks_lock:
        if path not in _async_locks:
            _async_locks[path] = asyncio.Lock()
        return _async_locks[path]


def get_connection(uri: str) -> Any:
    """
    Get a shared LanceDB connection for the given URI (sync).

    Thread-safe. Multiple calls with the same URI return the same connection
    object (reference counted). Call release_connection() when done if you
    hold a long-term reference.

    Returns:
        LanceDB connection object (or None on error, fail-safe).

    Raises:
        Nothing — always returns a connection or None.
    """
    normalized = str(Path(uri).resolve())

    # Fast path: existing connection
    with _registry_lock:
        if normalized in _registry:
            conn, refcount, _ = _registry[normalized]
            _registry[normalized] = (conn, refcount + 1, _registry[normalized][2])
            logger.debug(f"[LanceDB:POOL] Reused connection for {normalized} (refs={refcount + 1})")
            return conn

    # Slow path: create new connection
    # Check pool size limit
    with _registry_lock:
        if len(_registry) >= _MAX_POOL_SIZE:
            logger.warning(f"[LanceDB:POOL] Pool size limit reached ({_MAX_POOL_SIZE}), creating uncached connection")
            # Return uncached connection
            return _create_connection(normalized)

    # Create and cache
    conn = _create_connection(normalized)
    if conn is None:
        return None

    with _registry_lock:
        # Double-check after creating
        if normalized in _registry:
            # Another thread created it first
            conn, refcount, lock = _registry[normalized]
            _registry[normalized] = (conn, refcount + 1, lock)
            return conn

        _registry[normalized] = (conn, 1, threading.Lock())
        logger.info(f"[LanceDB:POOL] Cached new connection for {normalized} (total={len(_registry)})")
        return conn


async def get_connection_async(uri: str) -> Any:
    """
    Get a shared LanceDB connection for the given URI (async-safe).

    Uses per-path asyncio.Lock to prevent concurrent initialization.

    Returns:
        LanceDB connection object (or None on error, fail-safe).
    """
    normalized = str(Path(uri).resolve())

    # Fast path: existing connection
    with _registry_lock:
        if normalized in _registry:
            conn, refcount, lock = _registry[normalized]
            _registry[normalized] = (conn, refcount + 1, lock)
            logger.debug(f"[LanceDB:POOL] Reused connection for {normalized} (refs={refcount + 1})")
            return conn

    # Slow path: acquire per-path lock and create
    path_lock = _get_async_lock(normalized)
    async with path_lock:
        # Double-check after acquiring lock
        with _registry_lock:
            if normalized in _registry:
                conn, refcount, lock = _registry[normalized]
                _registry[normalized] = (conn, refcount + 1, lock)
                return conn

        # Check pool size
        with _registry_lock:
            if len(_registry) >= _MAX_POOL_SIZE:
                logger.warning(f"[LanceDB:POOL] Pool size limit reached, creating uncached connection")
                return _create_connection(normalized)

        # Create and cache
        conn = _create_connection(normalized)
        if conn is None:
            return None

        with _registry_lock:
            # Double-check after creating
            if normalized in _registry:
                conn, refcount, lock = _registry[normalized]
                _registry[normalized] = (conn, refcount + 1, lock)
                return conn

            _registry[normalized] = (conn, 1, threading.Lock())
            logger.info(f"[LanceDB:POOL] Cached new connection for {normalized} (total={len(_registry)})")
            return conn


def _create_connection(uri: str) -> Any:
    """Create a new LanceDB connection (internal, not cached on error)."""
    try:
        import lancedb
        return lancedb.connect(uri)
    except Exception as e:
        logger.warning(f"[LanceDB:POOL] Failed to connect to {uri}: {e}")
        return None


def release_connection(uri: str) -> None:
    """
    Release a reference to a connection (reference decrement).

    Note: This does NOT close the connection — LanceDB connections are
    lightweight and closing/reopening is expensive. Connections are held
    until process exit or explicit close_connection().

    Call this when you explicitly want to release a reference you hold
    beyond the typical store lifecycle.
    """
    normalized = str(Path(uri).resolve())

    with _registry_lock:
        if normalized not in _registry:
            return

        conn, refcount, lock = _registry[normalized]
        new_refcount = refcount - 1

        if new_refcount <= 0:
            # Last reference gone — close and remove
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            del _registry[normalized]
            with _async_locks_lock:
                _async_locks.pop(normalized, None)
            logger.info(f"[LanceDB:POOL] Closed connection for {normalized} (total={len(_registry)})")
        else:
            _registry[normalized] = (conn, new_refcount, lock)
            logger.debug(f"[LanceDB:POOL] Released reference for {normalized} (refs={new_refcount})")


def close_connection(uri: str) -> None:
    """
    Explicitly close a connection regardless of reference count.

    Use for cleanup during shutdown or when you know the store is done.
    """
    normalized = str(Path(uri).resolve())

    with _registry_lock:
        if normalized not in _registry:
            return

        conn, _, lock = _registry[normalized]
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        del _registry[normalized]

    with _async_locks_lock:
        _async_locks.pop(normalized, None)

    logger.info(f"[LanceDB:POOL] Force-closed connection for {normalized} (total={len(_registry)})")


def close_all_connections() -> None:
    """Close all pooled connections (process shutdown)."""
    global _registry, _async_locks

    with _registry_lock:
        paths = list(_registry.keys())
        for path in paths:
            conn, _, _ = _registry[path]
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        _registry.clear()

    with _async_locks_lock:
        _async_locks.clear()

    logger.info(f"[LanceDB:POOL] Closed all connections")


def get_pool_stats() -> dict[str, Any]:
    """Get pool statistics for debugging/monitoring."""
    with _registry_lock:
        return {
            "total_connections": len(_registry),
            "max_connections": _MAX_POOL_SIZE,
            "connections": {
                path: {"refcount": refcount}
                for path, (_, refcount, _) in _registry.items()
            },
        }
