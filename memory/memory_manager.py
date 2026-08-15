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
import asyncio
import logging
import time
from pathlib import Path
from typing import Any
from hledac.universal.utils.msgspec_json import dumps_str as _msgspec_dumps_str, loads as _msgspec_loads
from _core import aclose
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
try:
    from hledac.universal.utils.lmdb_bulk import putmulti_bounded
except ImportError:
    putmulti_bounded = None

# S-01: Import UnifiedLMDB for memory manager migration
try:
    from hledac.universal._core.lmdb_unified import get_unified_lmdb, SubDB
except ImportError:
    get_unified_lmdb = None
    SubDB = None  # type: ignore[assignment]
logger = logging.getLogger(__name__)
DEFAULT_MAP_SIZE = 128 * 1024 * 1024
MAX_KEYS_PER_SESSION = 1000
MAX_SESSIONS = 1000
SESSION_TTL_DAYS = 30

def _json_dumps(obj: Any) -> bytes:
    """Serialize object to JSON bytes."""
    if ORJSON_AVAILABLE:
        return orjson.dumps(obj)
    return _msgspec_dumps_str(obj).encode('utf-8')

def _json_loads(data) -> Any:
    """Deserialize JSON bytes to object."""
    if data is None:
        return None
    if ORJSON_AVAILABLE:
        try:
            return orjson.loads(data)
        except Exception:  # noqa: BLE001
            pass
    try:
        if isinstance(data, bytes):
            return _msgspec_loads(data.decode('utf-8'))
        elif isinstance(data, str):
            return _msgspec_loads(data)
    except Exception:  # noqa: BLE001
        pass
    return None

class MemoryManager:
    """
    Persistent memory manager using LMDB.

    Provides session-based storage for entities, queries, and files.
    Each session has its own key namespace with automatic expiration.
    """
    __slots__ = tuple(('_sub_db', '_env', '_lock', '_map_size', '_max_keys_per_session', '_max_sessions', '_session_ttl_days'))

    def __init__(self, db_path: str | None=None, map_size: int=DEFAULT_MAP_SIZE, max_keys_per_session: int=MAX_KEYS_PER_SESSION, max_sessions: int=MAX_SESSIONS, session_ttl_days: int=SESSION_TTL_DAYS):
        """
        Initialize Memory Manager.

        Args:
            db_path: Deprecated, ignored. Kept for API compat.
            map_size: Deprecated, ignored. Shared via UnifiedLMDB.
            max_keys_per_session: Maximum keys per session.
            max_sessions: Maximum number of sessions.
            session_ttl_days: Session TTL in days.
        """
        if not LMDB_AVAILABLE:
            raise ImportError('lmdb package not available')
        self._map_size = map_size  # kept for compat, not used
        self._max_keys_per_session = max_keys_per_session
        self._max_sessions = max_sessions
        self._session_ttl_days = session_ttl_days
        # S-01: Use UnifiedLMDB singleton instead of separate env
        if get_unified_lmdb is not None:
            _store = get_unified_lmdb()
            self._env = _store.env()
            self._sub_db = _store.open_db(SubDB.SESSION_META)
        else:
            # Fallback for environments where UnifiedLMDB is not available
            try:
                from hledac.universal.paths import DB_ROOT
                db_path_fallback = Path(db_path) if db_path else DB_ROOT / 'memory_manager.lmdb'
            except ImportError:
                db_path_fallback = Path(db_path) if db_path else Path('~/memory_manager.lmdb').expanduser()
            db_path_fallback.parent.mkdir(parents=True, exist_ok=True)
            self._env = lmdb.open(str(db_path_fallback), map_size=map_size, max_dbs=4, writemap=False, metasync=True)
            self._sub_db = None
        self._lock = asyncio.Lock()
        logger.info('MemoryManager initialized (UnifiedLMDB)' if self._sub_db is not None else f'MemoryManager initialized at fallback path')

    def _make_session_key(self, session_id: str, key: str) -> bytes:
        """Create a full LMDB key from session_id and key."""
        return f'session:{session_id}:{key}'.encode()

    def _make_session_index_key(self, session_id: str) -> bytes:
        """Create session index key."""
        return f'sessions:{session_id}'.encode()

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
                data = _json_dumps(value)
                now = time.time()
                session_meta = {'session_id': session_id, 'last_access': now, 'created': now}
                if putmulti_bounded is not None:
                    # S-01: Use sub_db for UnifiedLMDB isolation
                    putmulti_bounded(self._env, [(full_key, data), (session_index_key, _json_dumps(session_meta))], overwrite=True, sub_db=self._sub_db)
                else:
                    with self._env.begin(write=True, db=self._sub_db) as txn:
                        txn.put(full_key, data)
                        txn.put(session_index_key, _json_dumps(session_meta))
                return True
            except Exception as e:
                logger.error(f'MemoryManager put failed: {e}')
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

                # Phase 1: Read-only txn for data retrieval (zero-copy via buffers=True)
                with self._env.begin(write=False, buffers=True, db=self._sub_db) as txn:
                    value = txn.get(full_key)
                    if value is None:
                        return None
                    session_meta_bytes = txn.get(session_index_key)
                    # P0-4 FIX: Convert memoryview to bytes INSIDE the with block.
                    # With buffers=True, LMDB returns memoryview tied to txn's buffer.
                    # After txn closes, memoryview is invalid → ValueError on loads().
                    if isinstance(value, memoryview):
                        value = bytes(value)
                    if isinstance(session_meta_bytes, memoryview):
                        session_meta_bytes = bytes(session_meta_bytes)
                    session_meta = _json_loads(session_meta_bytes) if session_meta_bytes else None

                # Phase 2: Separate write txn only if session_meta needs update
                # (P1-3 fix: txn.put() in read-only txn caused silent ReadonlyError)
                if session_meta:
                    session_meta['last_access'] = time.time()
                    with self._env.begin(write=True, db=self._sub_db) as txn:
                        txn.put(session_index_key, _json_dumps(session_meta))

                return _json_loads(value)
            except Exception as e:
                logger.error(f'MemoryManager get failed: {e}')
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
                session_index_key = self._make_session_index_key(session_id)
                prefix = f'session:{session_id}:'.encode()

                with self._env.begin(write=True, db=self._sub_db) as txn:
                    # Check if this is the last key in session
                    remaining_keys = 0
                    cursor = txn.cursor()
                    cursor.set_range(prefix)
                    while cursor.key():
                        k = cursor.key()
                        if not k.startswith(prefix):
                            break
                        remaining_keys += 1
                        if remaining_keys > 1:
                            break
                        cursor.next()

                    # Delete the key
                    deleted = txn.delete(full_key)

                    # If this was the last key, clean up session_index_key to prevent orphan
                    if remaining_keys == 1:
                        txn.delete(session_index_key)

                    return deleted
            except Exception as e:
                logger.error(f'MemoryManager delete failed: {e}')
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
                prefix = f'session:{session_id}:'.encode()
                # buffers=True for zero-copy reads, consistent with get() optimization
                with self._env.begin(write=False, buffers=True, db=self._sub_db) as txn:
                    cursor = txn.cursor()
                    cursor.set_range(prefix)
                    while True:
                        key = cursor.key()
                        if key is None or not key.startswith(prefix):
                            break
                        # S-02: zero-copy — removeprefix then decode only the suffix (+1 allocs vs +2)
                        key_part = key[len(prefix):].decode('utf-8')
                        keys.append(key_part)
                        cursor.next()
                return keys
            except Exception as e:
                logger.error(f'MemoryManager get_session_keys failed: {e}')
                return []

    async def get_session_history(self, session_id: str, limit: int=100) -> list[dict]:
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
        # Direct LMDB read to avoid triggering last_access update on every history access
        full_prefix = f'session:{session_id}:'.encode()
        with self._env.begin(write=False, buffers=True, db=self._sub_db) as txn:
            for key in keys[:limit]:
                full_key = full_prefix + key.encode()
                value_bytes = txn.get(full_key)
                if value_bytes is not None:
                    value = _json_loads(value_bytes)
                    if value is not None:
                        history.append({'key': key, 'value': value})
        # Sort by timestamp if available, otherwise maintain insertion order
        history.sort(key=lambda item: item.get('value', {}).get('timestamp', 0), reverse=True)
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
                with self._env.begin(write=True, db=self._sub_db) as txn:
                    for key in keys:
                        full_key = self._make_session_key(session_id, key)
                        txn.delete(full_key)
                    session_index_key = self._make_session_index_key(session_id)
                    txn.delete(session_index_key)
                return True
            except Exception as e:
                logger.error(f'MemoryManager clear_session failed: {e}')
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
                prefix = b'sessions:'
                # buffers=True for zero-copy reads, consistent with other read paths
                with self._env.begin(write=False, buffers=True, db=self._sub_db) as txn:
                    cursor = txn.cursor()
                    cursor.set_range(prefix)
                    while True:
                        key = cursor.key()
                        if key is None or not key.startswith(prefix):
                            break
                        # S-02: zero-copy — removeprefix then decode only the suffix (+1 allocs vs +2)
                        session_id = key[len(prefix):].decode('utf-8')
                        sessions.append(session_id)
                        cursor.next()
                return sessions
            except Exception as e:
                logger.error(f'MemoryManager list_sessions failed: {e}')
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

                # OPTIMIZATION: Single read transaction for all session metas
                # (was: N transactions for N sessions)
                expired_session_ids: list[str] = []
                prefix = b'sessions:'

                with self._env.begin(write=False, buffers=True, db=self._sub_db) as txn:
                    for session_id in sessions:
                        session_index_key = self._make_session_index_key(session_id)
                        meta_bytes = txn.get(session_index_key)
                        if meta_bytes is None:
                            continue
                        try:
                            # OPTIMIZATION: _json_loads handles memoryview directly
                            meta = _json_loads(meta_bytes)
                            if meta is None:
                                continue
                            last_access = meta.get('last_access', 0)
                            if now - last_access > ttl_seconds:
                                expired_session_ids.append(session_id)
                        except Exception:
                            continue

                # OPTIMIZATION: Single write transaction for all deletions
                # (was: N write transactions via clear_session())
                removed = 0
                if expired_session_ids:
                    for session_id in expired_session_ids:
                        try:
                            await self.clear_session(session_id)
                            removed += 1
                        except Exception:
                            continue

                return removed
            except Exception as e:
                logger.error(f'MemoryManager cleanup_old_sessions failed: {e}')
                return 0

    def close(self) -> None:
        """Close the database."""
        if hasattr(self, '_env') and self._env:
            self._env.close()
            logger.info('MemoryManager closed')

    def __enter__(self) -> MemoryManager:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass
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

async def memory_get_history(session_id: str, limit: int=100) -> list[dict]:
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
    # Direct LMDB read to avoid triggering last_access update during export
    full_prefix = f'session:{session_id}:'.encode()
    with mgr._env.begin(write=False, buffers=True, db=mgr._sub_db) as txn:
        for key in keys:
            full_key = full_prefix + key.encode()
            value_bytes = txn.get(full_key)
            if value_bytes is None:
                continue
            value = _json_loads(value_bytes)
            if value is None:
                continue
            if key.startswith('finding:'):
                findings.append(value)
            elif key.startswith('hypothesis:'):
                hypotheses.append(value)
            else:
                other.append({'key': key, 'value': value})
    return {'session_id': session_id, 'findings': findings, 'hypotheses': hypotheses, 'other': other, 'findings_count': len(findings), 'hypotheses_count': len(hypotheses)}
__all__ = ['MemoryManager', 'get_memory_manager', 'close_memory_manager', 'memory_put', 'memory_get', 'memory_delete', 'memory_get_history', 'export_session']