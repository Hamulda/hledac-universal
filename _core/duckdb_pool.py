"""
core/duckdb_pool.py — ISSUE-04 Canonical DuckDB Connection Pool

Mandate: DuckDB writes MUST route through DuckDBShadowStore.async_ingest_findings_batch().
All other DuckDB operations (reads, scripts) MUST use this pool.

This module provides:
1. Bounded RO connection pool sized by resource_governor ConcurrencyPreset
2. Bounded RW connection pool (single writer, backed by serial write lock)
3. All connections pre-configured with M1 8GB safe defaults
4. Health validation on acquire (prevents stale connections)
5. CI guard: grep for duckdb.connect( outside this module

M1 8GB Safety:
- RO pool: io_threads (2) from ConcurrencyPreset
- RW pool: 1 connection (serial writes via asyncio.Lock)
- Memory: 1GB limit per connection, 2 threads

Usage:
    from _core.duckdb_pool import duckdb_ro_pool, duckdb_rw_pool

    # Read (RO)
    with duckdb_ro_pool.acquire(db_path) as conn:
        rows = conn.execute("SELECT * FROM table").fetchall()

    # Write (MUST use DuckDBShadowStore.async_ingest_findings_batch for canonical path)
    # RW pool only for ad-hoc scripts that cannot use the store

Sprint ISSUE-04 (2026-08-14)
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from _core._util import aclose
from _core.lock_registry import LockCategory, register_lock

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# M1 8GB: io_threads from ConcurrencyPreset (default 2, scales down at pressure)
_RO_POOL_SIZE_DEFAULT = 2
_RO_POOL_MAX_ABSOLUTE = 4  # Absolute ceiling even at high load

# RW pool: single writer with serial lock (DuckDB WAL semantics)
_RW_POOL_SIZE = 1

# Connection validation
_HEALTH_CHECK_SQL = "SELECT 1"
_CONNECTION_VALIDATION_SQL = "SELECT 1, 2"


# =============================================================================
# M1 8GB DuckDB Settings
# =============================================================================

_M1_DUCKDB_SETTINGS: tuple[tuple[str, Any], ...] = (
    ("memory_limit", "1GB"),
    ("max_temp_memory", "1GB"),
    ("threads", 2),
    ("preserve_insertion_order", False),
    ("busy_timeout", "30s"),
    )


@functools.cache
def _get_duckdb_module() -> Any:
    """Lazy import of duckdb - only loaded when pool is actually used.

    Thread-safe via functools.cache internal lock.
    """
    import duckdb

    return duckdb


# =============================================================================
# Resource Governor Integration
# =============================================================================


def _get_ro_pool_size() -> int:
    """
    Get RO pool size from resource_governor ConcurrencyPreset.

    Returns io_threads value (2 default on M1 8GB), scales down at pressure.
    Falls back to default if governor unavailable.
    """
    try:
        from hledac.universal._core.resource_governor import (
            ConcurrencyPreset,
            evaluate_uma_state,
    )
        import psutil

        mem = psutil.virtual_memory()
        system_used_gib = mem.used / 1024**3
        state = evaluate_uma_state(system_used_gib)
        preset = ConcurrencyPreset.from_state(state)
        return preset.io_threads
    except Exception:
        return _RO_POOL_SIZE_DEFAULT


# =============================================================================
# Connection Health Check
# =============================================================================


def _is_connection_alive(conn: Any) -> bool:
    """Check if DuckDB connection is still alive and responsive."""
    try:
        conn.execute(_HEALTH_CHECK_SQL)
        return True
    except Exception:
        return False


def _configure_connection(conn: Any, read_only: bool = True) -> None:
    """
    Apply M1 8GB safe defaults to DuckDB connection.

    Args:
        conn: DuckDB connection to configure
        read_only: If True, opens in read-only mode
    """
    try:
        for setting, value in _M1_DUCKDB_SETTINGS:
            if isinstance(value, bool):
                conn.execute(f"SET {setting} = {value}")
            elif isinstance(value, int):
                conn.execute(f"SET {setting} = {value}")
            else:
                conn.execute(f"SET {setting} = '{value}'")
    except Exception as e:
        logger.debug("[DUCKDB_POOL] Connection config warning: %s", e)


# =============================================================================
# RO Pool Entry
# =============================================================================


@dataclass(slots=True)
class _ROPoolEntry:
    """A pooled RO connection with metadata."""
    conn: Any
    db_path: str
    created_at: float = field(default_factory=lambda: __import__("time").time())
    last_used: float = field(default_factory=lambda: __import__("time").time())
    use_count: int = 0

    def touch(self) -> None:
        """Update last_used timestamp (LRU tracking)."""
        self.last_used = __import__("time").time()
        self.use_count += 1

    def is_alive(self) -> bool:
        """Check if underlying connection is still alive."""
        return _is_connection_alive(self.conn)

    def close(self) -> None:
        """Close the underlying connection."""
        try:
            self.conn.close()
        except Exception:
            pass


# =============================================================================
# Canonical RO Connection Pool
# =============================================================================


class _DuckDBROPool:
    """
    Canonical DuckDB Read-Only connection pool.

    ISSUE-04: Replaces all raw duckdb.connect() calls outside duckdb_store.py.

    Features:
    - Bounded size: io_threads from ConcurrencyPreset (2 on M1 8GB)
    - LRU eviction when full
    - Health check on acquire (evicts stale connections)
    - Thread-safe via threading.Lock
    - Lazy connection creation
    - M1 8GB safe defaults on all connections

    Invariant: Only ONE way to get RO connections — this class.
    """

    __slots__ = (
        "_pool",
        "_paths",
        "_lock",
        "_max_size",
        "_stats",
    )

    def __init__(self, max_size: int | None = None) -> None:
        self._max_size = max_size or _get_ro_pool_size()
        self._pool: deque[_ROPoolEntry] = deque(maxlen=self._max_size)
        self._paths: dict[str, int] = {}  # path -> index in pool
        self._lock = threading.Lock()
        self._stats = {
            "acquire_total": 0,
            "acquire_new": 0,
            "acquire_reuse": 0,
            "evict_stale": 0,
            "evict_lru": 0,
        }

    def _evict_lru(self) -> None:
        """Evict least recently used entry to make room."""
        if not self._pool:
            return
        entry = self._pool.popleft()
        self._paths.pop(entry.db_path, None)
        entry.close()
        self._stats["evict_lru"] += 1

    def _evict_stale(self, entry: _ROPoolEntry) -> None:
        """Evict a stale connection entry."""
        self._paths.pop(entry.db_path, None)
        entry.close()
        self._stats["evict_stale"] += 1

    def _create_connection(self, db_path: str, read_only: bool = True) -> _ROPoolEntry:
        """Create a new RO connection with M1 8GB defaults."""
        duckdb = _get_duckdb_module()
        conn = duckdb.connect(db_path, read_only=read_only)
        _configure_connection(conn, read_only=read_only)
        return _ROPoolEntry(conn=conn, db_path=db_path)

    def acquire(self, db_path: str) -> Any:
        """
        Acquire an RO connection for the given db_path.

        Returns:
            DuckDB connection (not a pool entry — caller gets the raw connection)

        Raises:
            RuntimeError: If connection creation fails
        """
        duckdb = _get_duckdb_module()
        self._stats["acquire_total"] += 1

        with self._lock:
            # Evict stale entries and find reusable connection
            new_pool: deque[_ROPoolEntry] = deque(maxlen=self._max_size)
            result_entry: _ROPoolEntry | None = None

            for entry in self._pool:
                if entry.db_path == db_path and entry.is_alive():
                    result_entry = entry
                    self._stats["acquire_reuse"] += 1
                elif entry.is_alive():
                    new_pool.append(entry)
                else:
                    self._evict_stale(entry)

            self._pool = new_pool

            if result_entry is not None:
                result_entry.touch()
                return result_entry.conn

            # Need to create new connection
            # Evict LRU entries if at capacity
            while len(self._pool) >= self._max_size:
                self._evict_lru()

            try:
                entry = self._create_connection(db_path, read_only=True)
                self._pool.append(entry)
                self._stats["acquire_new"] += 1
                return entry.conn
            except Exception as e:
                raise RuntimeError(f"Failed to create DuckDB connection for {db_path}: {e}") from e

    def release(self, conn: Any) -> None:
        """
        Return a connection to the pool (optional, for explicit release).

        Note: For most use cases, use the context manager instead.
        This method is a no-op — connections are returned automatically
        when the context manager exits.
        """
        # No-op: connections stay in pool for reuse
        # Explicit release not needed with context manager pattern
        pass

    def close_all(self) -> None:
        """Close all pooled connections. Call on process shutdown."""
        with self._lock:
            for entry in self._pool:
                entry.close()
            self._pool.clear()
            self._paths.clear()

    def get_stats(self) -> dict[str, Any]:
        """Return pool statistics."""
        with self._lock:
            return {
                **self._stats,
                "pool_size": len(self._pool),
                "max_size": self._max_size,
                "unique_paths": len(self._paths),
            }

    @property
    def max_size(self) -> int:
        """Current max pool size (may be updated from governor)."""
        return self._max_size

    def refresh_max_size(self) -> None:
        """Refresh max_size from resource_governor."""
        new_size = _get_ro_pool_size()
        if new_size != self._max_size:
            # Shrink pool if needed
            with self._lock:
                self._max_size = new_size
                while len(self._pool) > self._max_size:
                    self._evict_lru()


# =============================================================================
# RW Pool (Single Writer)
# =============================================================================


class _DuckDBRWPool:
    """
    Canonical DuckDB Read-Write connection pool.

    NOTE: For canonical writes, use DuckDBShadowStore.async_ingest_findings_batch().
    This pool is for ad-hoc scripts that cannot use the store.

    Features:
    - Single writer with asyncio.Lock serialization
    - Connection validation on acquire
    - M1 8GB safe defaults
    """

    __slots__ = (
        "_conn",
        "_db_path",
        "_lock",
        "_serial_lock",
        "_stats",
        "_valid",
    )

    def __init__(self) -> None:
        self._conn: Any | None = None
        self._db_path: str | None = None
        self._lock = threading.Lock()
        self._serial_lock: asyncio.Lock | None = None
        self._stats = {
            "acquire_total": 0,
            "acquire_reuse": 0,
            "acquire_new": 0,
        }
        self._valid = False

    def _get_serial_lock(self) -> asyncio.Lock:
        """Get or create the serial write lock."""
        if self._serial_lock is None:
            self._serial_lock = asyncio.Lock()
        return self._serial_lock

    def _create_connection(self, db_path: str) -> Any:
        """Create a new RW connection with M1 8GB defaults."""
        duckdb = _get_duckdb_module()
        conn = duckdb.connect(db_path, read_only=False)
        _configure_connection(conn, read_only=False)
        # Set IMMEDIATE transaction mode for write lock safety
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def acquire(self, db_path: str) -> Any:
        """
        Acquire an RW connection (not recommended — use async_ingest_findings_batch).

        Args:
            db_path: Path to DuckDB database

        Returns:
            DuckDB connection

        Raises:
            RuntimeError: If connection creation fails
        """
        self._stats["acquire_total"] += 1

        with self._lock:
            if self._conn is not None and self._db_path == db_path and self._valid:
                if _is_connection_alive(self._conn):
                    self._stats["acquire_reuse"] += 1
                    return self._conn
                self._valid = False

            # Close old connection
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass

            try:
                self._conn = self._create_connection(db_path)
                self._db_path = db_path
                self._valid = True
                self._stats["acquire_new"] += 1
                return self._conn
            except Exception as e:
                raise RuntimeError(f"Failed to create RW DuckDB connection for {db_path}: {e}") from e

    @property
    def serial_lock(self) -> asyncio.Lock:
        """Get the serial write lock for coordinating writers."""
        return self._get_serial_lock()

    def close_all(self) -> None:
        """Close the RW connection."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
            self._conn = None
            self._db_path = None
            self._valid = False

    def get_stats(self) -> dict[str, Any]:
        """Return pool statistics."""
        with self._lock:
            return {
                **self._stats,
                "has_connection": self._conn is not None,
                "valid": self._valid,
            }


# =============================================================================
# Module-Level Singleton Pools
# =============================================================================

# Global pool instances (lazily initialized)
_ro_pool: _DuckDBROPool | None = None
_rw_pool: _DuckDBRWPool | None = None


@register_lock(LockCategory.GRAPH)
def _pools_lock() -> threading.Lock:
    """Module-level lock for DuckDB pool singletons."""
    return threading.Lock()


def _get_ro_pool() -> _DuckDBROPool:
    """Get the global RO pool singleton."""
    global _ro_pool
    if _ro_pool is None:
        with _pools_lock():
            if _ro_pool is None:
                _ro_pool = _DuckDBROPool()
    return _ro_pool


def _get_rw_pool() -> _DuckDBRWPool:
    """Get the global RW pool singleton."""
    global _rw_pool
    if _rw_pool is None:
        with _pools_lock():
            if _rw_pool is None:
                _rw_pool = _DuckDBRWPool()
    return _rw_pool


# =============================================================================
# Public API
# =============================================================================

@property
def duckdb_ro_pool() -> _DuckDBROPool:
    """Global RO pool instance."""
    return _get_ro_pool()


@property
def duckdb_rw_pool() -> _DuckDBRWPool:
    """Global RW pool instance."""
    return _get_rw_pool()


def duckdb_ro_acquire(db_path: str) -> Any:
    """
    Acquire an RO DuckDB connection from the canonical pool.

    This is the ONLY sanctioned way to get a RO DuckDB connection
    outside of DuckDBShadowStore.

    Args:
        db_path: Path to DuckDB database

    Returns:
        DuckDB connection (caller must NOT close it)

    Usage:
        conn = duckdb_ro_acquire("/path/to/db.duckdb")
        try:
            rows = conn.execute("SELECT * FROM table").fetchall()
        finally:
            pass  # Connection stays in pool
    """
    return _get_ro_pool().acquire(db_path)


def duckdb_rw_acquire(db_path: str) -> Any:
    """
    Acquire an RW DuckDB connection from the canonical pool.

    WARNING: For canonical writes, use DuckDBShadowStore.async_ingest_findings_batch().
    This function is for ad-hoc scripts only.

    Args:
        db_path: Path to DuckDB database

    Returns:
        DuckDB connection

    Usage:
        with duckdb_serial_write():
            conn = duckdb_rw_acquire("/path/to/db.duckdb")
            conn.execute("INSERT INTO ...")
    """
    return _get_rw_pool().acquire(db_path)


@contextlib.contextmanager
def duckdb_ro_connection(db_path: str) -> Any:
    """
    Context manager for RO DuckDB connection from canonical pool.

    Usage:
        with duckdb_ro_connection("/path/to/db.duckdb") as conn:
            rows = conn.execute("SELECT * FROM table").fetchall()
    """
    conn = duckdb_ro_acquire(db_path)
    try:
        yield conn
    finally:
        pass  # Connection returned to pool automatically


@contextlib.contextmanager
async def duckdb_serial_write(db_path: str) -> Any:
    """
    Context manager for serial RW DuckDB access.

    Acquires the serial write lock and returns an RW connection.
    Only ONE writer can hold this lock at a time.

    WARNING: For canonical writes, use DuckDBShadowStore.async_ingest_findings_batch().

    Usage:
        async with duckdb_serial_write("/path/to/db.duckdb") as conn:
            await asyncio.to_thread(conn.execute, "INSERT INTO ...")
    """
    pool = _get_rw_pool()
    async with pool.serial_lock:
        conn = pool.acquire(db_path)
        try:
            yield conn
        finally:
            pass  # Connection stays alive for reuse


def close_all_pools() -> None:
    """Close all DuckDB pools. Call on process shutdown."""
    _get_ro_pool().close_all()
    _get_rw_pool().close_all()


def get_pool_stats() -> dict[str, Any]:
    """Get statistics for all pools."""
    return {
        "ro": _get_ro_pool().get_stats(),
        "rw": _get_rw_pool().get_stats(),
    }


# =============================================================================
# CI Guard: Unauthorized duckdb.connect Detection
# =============================================================================

# Authorized modules that may use duckdb.connect directly:
AUTHORIZED_DUCKDB_MODULES: frozenset[str] = frozenset([
    "knowledge/duckdb_store.py",           # DuckDBShadowStore - canonical store
    "knowledge/duckdb_wal_manager.py",     # WAL manager
    "knowledge/duckdb_base.py",            # Base class
    "core/duckdb_pool.py",                 # THIS module - canonical pool
])

# Pattern for detecting raw duckdb.connect usage
_DUCKDB_CONNECT_PATTERN = r'duckdb\.connect\s*\('


def check_unauthorized_duckdb_connect(file_path: str) -> list[str]:
    """
    Check if a file uses duckdb.connect outside authorized modules.

    This is the CI guard for ISSUE-04.

    Args:
        file_path: Path to Python file to check

    Returns:
        List of issues found (empty if clean)
    """
    import re

    issues = []

    # Skip authorized modules
    normalized_path = file_path.replace("\\", "/")
    for authorized in AUTHORIZED_DUCKDB_MODULES:
        if authorized in normalized_path:
            return []

    try:
        with open(file_path, "r") as f:
            content = f.read()

        # Find all duckdb.connect( occurrences
        for i, line in enumerate(content.split("\n"), 1):
            if re.search(_DUCKDB_CONNECT_PATTERN, line):
                # Exclude comments
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                issues.append(f"  Line {i}: {line.strip()}")

    except Exception as e:
        issues.append(f"  Error reading file: {e}")

    return issues


def run_ci_guard() -> int:
    """
    Run CI guard to check for unauthorized duckdb.connect usage.

    Returns:
        0 if clean, 1 if violations found
    """
    import glob
    import sys

    print("[ISSUE-04 CI Guard] Checking for unauthorized duckdb.connect() usage...")
    print()

    violations = []

    # Check all Python files except tests and authorized modules
    for pattern in ["**/*.py", "*.py"]:
        for file_path in glob.glob(pattern, recursive=True):
            # Skip tests and scripts
            if "/tests/" in file_path or file_path.startswith("tests/"):
                continue
            if "/.scratch/" in file_path:
                continue
            if "/benchmarks" in file_path:
                continue

            issues = check_unauthorized_duckdb_connect(file_path)
            if issues:
                violations.append((file_path, issues))

    if not violations:
        print("[PASS] No unauthorized duckdb.connect() usage found.")
        return 0

    print("[FAIL] Unauthorized duckdb.connect() usage detected:")
    print()
    for file_path, issues in violations:
        print(f"  {file_path}:")
        for issue in issues:
            print(issue)
        print()

    print(f"Total: {len(violations)} file(s) with violations")
    print()
    print("Authorized modules for duckdb.connect:")
    for mod in sorted(AUTHORIZED_DUCKDB_MODULES):
        print(f"  - {mod}")
    print()
    print("All other code must use:")
    print("  - duckdb_ro_acquire() / duckdb_ro_connection() for reads")
    print("  - DuckDBShadowStore.async_ingest_findings_batch() for canonical writes")
    print()

    return 1


if __name__ == "__main__":
    sys.exit(run_ci_guard())
