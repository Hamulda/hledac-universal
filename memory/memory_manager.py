"""
Memory Manager with LMDB Persistence
====================================

Dual-layer architecture for session-bound ephemeral storage:

LAYER 1 — Direct Module API (here in memory_manager.py):
    put(session_id, key, value), get(session_id, key), delete(session_id, key)
    Used by: live_public_pipeline (sprint lifecycle), research_loop (RL q-table)
    Scope: per-session working memory, hot/ephemeral, LMDB-backed

LAYER 2 — memory_layer.py wraps this with:
    SharedBlock: cross-session shared data blocks (research context, evidence carriers)
    EntropyMaskingManager: noise injection for privacy, O(|fifo|) eviction
    Used by: research loops that need shared state across hypothesis iterations

WHY SEPARATE FROM DuckDB? DuckDBShadowStore = persistent canonical store for sprint
facts (tier 1), written once per sprint. MemoryManager = micro-session state
updated hundreds of times per sprint. Different lifetimes, different access patterns.

THREAD SAFETY: MemoryManager is NOT thread-safe. All access is async and must
remain within a single event loop. Session isolation prevents cross-session
corruption but concurrent await points within one session are unprotected (by design
— event loop serialized).

M1 8GB Optimized:
- Zero-copy reads via buffers=True
- Bounded key count per session (MAX_KEYS_PER_SESSION)
- Lazy session cleanup (cleanup_old_sessions called on put/get)
- orjson zero-copy deserialization
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

try:
    import orjson
    ORJSON_AVAILABLE = True
except ImportError:
    import json
    ORJSON_AVAILABLE = False

try:
    import lmdb
    LMDB_AVAILABLE = True
except ImportError:
    LMDB_AVAILABLE = False

logger = logging.getLogger(__name__)

# Default bounds
DEFAULT_MAP_SIZE = 128 * 1024 * 1024  # 128MB
MAX_KEYS_PER_SESSION = 1000  # Max keys per session
MAX_SESSIONS = 1000  # Max number of sessions
SESSION_TTL_DAYS = 30  # Sessions expire after 30 days


def _json_dumps(obj: Any) -> bytes:
    """Serialize object to JSON bytes."""
    if ORJSON_AVAILABLE:
        return orjson.dumps(obj)
    return json.dumps(obj).encode('utf-8')


def _json_loads(data) -> Any:
    """Deserialize JSON bytes to object."""
    if data is None:
        return None
    if ORJSON_AVAILABLE:
        try:
            return orjson.loads(data)
        except Exception:
            pass
    try:
        if isinstance(data, bytes):
            return json.loads(data.decode('utf-8'))
        elif isinstance(data, str):
            return json.loads(data)
    except Exception:
        pass
    return None


class MemoryManager:
    """
    Persistent memory manager using LMDB.

    Provides session-based storage for entities, queries, and files.
    Each session has its own key namespace with automatic expiration.
    """

    def __init__(
        self,
        db_path: str | None = None,
        map_size: int = DEFAULT_MAP_SIZE,
        max_keys_per_session: int = MAX_KEYS_PER_SESSION,
        max_sessions: int = MAX_SESSIONS,
        session_ttl_days: int = SESSION_TTL_DAYS,
    ):
        """
        Initialize Memory Manager.

        Args:
            db_path: Path to LMDB database. If None, uses default location.
            map_size: Maximum database size in bytes.
            max_keys_per_session: Maximum keys per session.
            max_sessions: Maximum number of sessions.
            session_ttl_days: Session TTL in days.
        """
        if not LMDB_AVAILABLE:
            raise ImportError("lmdb package not available")

        # Use canonical path if available
        try:
            from hledac.universal.paths import DB_ROOT
            self._db_path = Path(db_path) if db_path else DB_ROOT / "memory_manager.lmdb"
        except ImportError:
            self._db_path = Path(db_path) if db_path else Path("~/memory_manager.lmdb").expanduser()

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._map_size = map_size
        self._max_keys_per_session = max_keys_per_session
        self._max_sessions = max_sessions
        self._session_ttl_days = session_ttl_days

        # Open LMDB environment
        self._env = lmdb.open(
            str(self._db_path),
            map_size=map_size,
            max_dbs=4,  # sessions, entities, queries, files
            writemap=False,
            metasync=True,
        )

        # Async lock for thread safety
        self._lock = asyncio.Lock()

        logger.info(f"MemoryManager initialized at {self._db_path}")

    def _make_session_key(self, session_id: str, key: str) -> bytes:
        """Create a full LMDB key from session_id and key."""
        return f"session:{session_id}:{key}".encode()

    def _make_session_index_key(self, session_id: str) -> bytes:
        """Create session index key."""
        return f"sessions:{session_id}".encode()

    async def put(self, session_id: str, key: str, value: dict) -> bool:
        """
        Store a value in session storage.

        Args:
            session_id: Session identifier
            key: Key within session
            value: Dict value to store

        Returns:
            True if successful, False otherwise
        """
        async with self._lock:
            try:
                full_key = self._make_session_key(session_id, key)
                session_index_key = self._make_session_index_key(session_id)

                # Serialize value
                data = _json_dumps(value)

                # Update session metadata
                now = time.time()
                session_meta = {
                    "session_id": session_id,
                    "last_access": now,
                    "created": now,
                }

                with self._env.begin(write=True) as txn:
                    # Store value
                    txn.put(full_key, data)

                    # Update session index
                    txn.put(session_index_key, _json_dumps(session_meta))

                return True

            except Exception as e:
                logger.error(f"MemoryManager put failed: {e}")
                return False

    async def get(self, session_id: str, key: str) -> dict | None:
        """
        Retrieve a value from session storage.

        Args:
            session_id: Session identifier
            key: Key within session

        Returns:
            Dict value if found, None otherwise
        """
        async with self._lock:
            try:
                full_key = self._make_session_key(session_id, key)
                session_index_key = self._make_session_index_key(session_id)

                with self._env.begin(write=False, buffers=True) as txn:
                    # Get value
                    value = txn.get(full_key)
                    if value is None:
                        return None

                    # Update session last access time
                    session_meta_bytes = txn.get(session_index_key)
                    if session_meta_bytes:
                        session_meta = _json_loads(session_meta_bytes)
                        if session_meta:
                            session_meta["last_access"] = time.time()
                            txn.put(session_index_key, _json_dumps(session_meta))

                    return _json_loads(value)

            except Exception as e:
                logger.error(f"MemoryManager get failed: {e}")
                return None

    async def delete(self, session_id: str, key: str) -> bool:
        """
        Delete a key from session storage.

        Args:
            session_id: Session identifier
            key: Key within session

        Returns:
            True if key existed, False otherwise
        """
        async with self._lock:
            try:
                full_key = self._make_session_key(session_id, key)

                with self._env.begin(write=True) as txn:
                    return txn.delete(full_key)

            except Exception as e:
                logger.error(f"MemoryManager delete failed: {e}")
                return False

    async def get_session_keys(self, session_id: str) -> list[str]:
        """
        Get all keys for a session.

        Args:
            session_id: Session identifier

        Returns:
            List of keys in session
        """
        async with self._lock:
            try:
                keys = []
                prefix = f"session:{session_id}:".encode()

                with self._env.begin(write=False) as txn:
                    cursor = txn.cursor()
                    cursor.set_range(prefix)

                    while cursor.key():
                        key = cursor.key()
                        if not key.startswith(prefix):
                            break
                        # Extract key part after session prefix
                        key_str = key.decode('utf-8')
                        key_part = key_str[len(f"session:{session_id}:"):]
                        keys.append(key_part)
                        cursor.next()

                return keys

            except Exception as e:
                logger.error(f"MemoryManager get_session_keys failed: {e}")
                return []

    async def get_session_history(
        self,
        session_id: str,
        limit: int = 100,
    ) -> list[dict]:
        """
        Get recent history for a session.

        Args:
            session_id: Session identifier
            limit: Maximum number of entries to return

        Returns:
            List of {key, value} dicts, most recent first
        """
        keys = await self.get_session_keys(session_id)
        history = []

        for key in keys[:limit]:
            value = await self.get(session_id, key)
            if value is not None:
                history.append({"key": key, "value": value})

        # Sort by key timestamp if available
        history.sort(
            key=lambda x: x["value"].get("timestamp", 0) if isinstance(x["value"], dict) else 0,
            reverse=True,
        )

        return history[:limit]

    async def clear_session(self, session_id: str) -> bool:
        """
        Clear all keys for a session.

        Args:
            session_id: Session identifier

        Returns:
            True if successful, False otherwise
        """
        async with self._lock:
            try:
                keys = await self.get_session_keys(session_id)

                with self._env.begin(write=True) as txn:
                    # Delete all session keys
                    for key in keys:
                        full_key = self._make_session_key(session_id, key)
                        txn.delete(full_key)

                    # Delete session index
                    session_index_key = self._make_session_index_key(session_id)
                    txn.delete(session_index_key)

                return True

            except Exception as e:
                logger.error(f"MemoryManager clear_session failed: {e}")
                return False

    async def list_sessions(self) -> list[str]:
        """
        List all session IDs.

        Returns:
            List of session IDs
        """
        async with self._lock:
            try:
                sessions = []
                prefix = b"sessions:"

                with self._env.begin(write=False) as txn:
                    cursor = txn.cursor()
                    cursor.set_range(prefix)

                    while cursor.key():
                        key = cursor.key()
                        if not key.startswith(prefix):
                            break
                        session_id = key[len(prefix):].decode('utf-8')
                        sessions.append(session_id)
                        cursor.next()

                return sessions

            except Exception as e:
                logger.error(f"MemoryManager list_sessions failed: {e}")
                return []

    async def cleanup_old_sessions(self) -> int:
        """
        Remove sessions older than TTL.

        Returns:
            Number of sessions removed
        """
        async with self._lock:
            try:
                sessions = await self.list_sessions()
                now = time.time()
                ttl_seconds = self._session_ttl_days * 24 * 3600
                removed = 0

                for session_id in sessions:
                    session_index_key = self._make_session_index_key(session_id)

                    with self._env.begin(write=False, buffers=True) as txn:
                        meta_bytes = txn.get(session_index_key)
                        if meta_bytes is None:
                            continue

                        meta = _json_loads(meta_bytes)
                        if meta is None:
                            continue

                        last_access = meta.get("last_access", 0)
                        if now - last_access > ttl_seconds:
                            await self.clear_session(session_id)
                            removed += 1

                return removed

            except Exception as e:
                logger.error(f"MemoryManager cleanup_old_sessions failed: {e}")
                return 0

    def close(self) -> None:
        """Close the database."""
        if hasattr(self, '_env') and self._env:
            self._env.close()
            logger.info("MemoryManager closed")

    def __enter__(self) -> MemoryManager:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


# Singleton instance
_memory_manager: MemoryManager | None = None


async def get_memory_manager() -> MemoryManager:
    """
    Get the singleton MemoryManager instance.

    Returns:
        MemoryManager singleton
    """
    global _memory_manager

    if _memory_manager is None:
        _memory_manager = MemoryManager()

    return _memory_manager


async def close_memory_manager() -> None:
    """Close the singleton MemoryManager."""
    global _memory_manager

    if _memory_manager is not None:
        _memory_manager.close()
        _memory_manager = None


# Convenience functions that use the singleton
async def memory_put(session_id: str, key: str, value: dict) -> bool:
    """Store a value in memory."""
    mgr = await get_memory_manager()
    return await mgr.put(session_id, key, value)


async def memory_get(session_id: str, key: str) -> dict | None:
    """Retrieve a value from memory."""
    mgr = await get_memory_manager()
    return await mgr.get(session_id, key)


async def memory_delete(session_id: str, key: str) -> bool:
    """Delete a key from memory."""
    mgr = await get_memory_manager()
    return await mgr.delete(session_id, key)


async def memory_get_history(session_id: str, limit: int = 100) -> list[dict]:
    """Get session history from memory."""
    mgr = await get_memory_manager()
    return await mgr.get_session_history(session_id, limit)


async def export_session(session_id: str) -> dict[str, Any]:
    """
    FÁZE P18: Export all findings and hypotheses from a session as JSON.

    Args:
        session_id: Session identifier to export

    Returns:
        Dict with 'session_id', 'findings', 'hypotheses', and metadata
    """
    mgr = await get_memory_manager()
    keys = await mgr.get_session_keys(session_id)

    findings: list[dict] = []
    hypotheses: list[dict] = []
    other: list[dict] = []

    for key in keys:
        value = await mgr.get(session_id, key)
        if value is None:
            continue
        if key.startswith("finding:"):
            findings.append(value)
        elif key.startswith("hypothesis:"):
            hypotheses.append(value)
        else:
            other.append({"key": key, "value": value})

    return {
        "session_id": session_id,
        "findings": findings,
        "hypotheses": hypotheses,
        "other": other,
        "findings_count": len(findings),
        "hypotheses_count": len(hypotheses),
    }


__all__ = [
    "MemoryManager",
    "get_memory_manager",
    "close_memory_manager",
    "memory_put",
    "memory_get",
    "memory_delete",
    "memory_get_history",
    "export_session",
]
