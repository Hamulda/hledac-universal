"""
Dead-Letter Queue Manager — DLQ-02
==================================
Izolované ukládání koruptních/nevalidních payloadů pro pozdější inspekci.

Architektura:
- SQLite backend (WAL mode, bounded)
- Payloady indexovány podle: sprint_id, source, error_type, timestamp
- Automatické čištění starých záznamů (>30 dní)
- Decorator pro automatické zachycování výjimek
- Fail-safe: žádné výjimky nikdy neproniknou ven

Použití:
    from hledac.universal.core.dlq_manager import get_dlq_manager, dlq_catch

    # Přímé ukládání
    dlq = get_dlq_manager()
    dlq.store_payload(payload_data, sprint_id, "my_source", error, metadata={})

    # Decorator pro automatické zachycování
    @dlq_catch(source="my_module.process")
    async def process(data):
        ...
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

import aiosqlite

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON helpers — routed through canonical codec (Issue 10 fix)
# ---------------------------------------------------------------------------


def _json_encode(obj: Any) -> str:
    """Encode Python object to JSON string via canonical codec."""
    from hledac.universal.utils.codec import encode_str
    return encode_str(obj)


def _json_decode(raw: str | None) -> Any:
    """Decode JSON string to Python object via canonical codec."""
    if not raw:
        return {}
    from hledac.universal.utils.codec import decode
    return decode(raw)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DLQPayload:
    """Payload uložený v Dead-Letter Queue."""
    payload_id: str  # SHA256 hash obsahu
    sprint_id: str
    source: str  # "synthesis_runner", "continuous_batch_engine", atd.
    error_type: str  # Typ výjimky
    error_message: str
    payload_data: bytes  # Serializovaný původní payload
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    attempt_count: int = 0
    last_attempt_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'payload_id': self.payload_id,
            'sprint_id': self.sprint_id,
            'source': self.source,
            'error_type': self.error_type,
            'error_message': self.error_message,
            'payload_data': self.payload_data.hex(),
            'metadata': self.metadata,
            'created_at': self.created_at.isoformat(),
            'attempt_count': self.attempt_count,
            'last_attempt_at': self.last_attempt_at.isoformat() if self.last_attempt_at else None,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> 'DLQPayload':
        return cls(
            payload_id=row['payload_id'],
            sprint_id=row['sprint_id'],
            source=row['source'],
            error_type=row['error_type'],
            error_message=row['error_message'],
            payload_data=bytes.fromhex(row['payload_data']),
            metadata=_json_decode(row['metadata']) if row['metadata'] else {},
            created_at=datetime.fromisoformat(row['created_at']),
            attempt_count=row['attempt_count'],
            last_attempt_at=datetime.fromisoformat(row['last_attempt_at'])
                if row['last_attempt_at'] else None,
        )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class DLQSchema:
    """SQL schéma pro DLQ databázi."""
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS dlq_payloads (
        payload_id TEXT PRIMARY KEY,
        sprint_id TEXT NOT NULL,
        source TEXT NOT NULL,
        error_type TEXT NOT NULL,
        error_message TEXT NOT NULL,
        payload_data TEXT NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        last_attempt_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_dlq_sprint_id ON dlq_payloads(sprint_id);
    CREATE INDEX IF NOT EXISTS idx_dlq_source ON dlq_payloads(source);
    CREATE INDEX IF NOT EXISTS idx_dlq_error_type ON dlq_payloads(error_type);
    CREATE INDEX IF NOT EXISTS idx_dlq_created_at ON dlq_payloads(created_at);
    CREATE INDEX IF NOT EXISTS idx_dlq_attempt_count ON dlq_payloads(attempt_count);

    CREATE TABLE IF NOT EXISTS dlq_stats (
        source TEXT NOT NULL,
        error_type TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 0,
        last_occurrence TEXT NOT NULL,
        PRIMARY KEY (source, error_type)
    );
    """
    MAX_PAYLOAD_SIZE = 2 * 1024 * 1024  # 2 MB max payload


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class DLQManager:
    """Manager pro Dead-Letter Queue s SQLite backendem.

    Bounded: max_age_days cleanup, max payload size, WAL mode.
    Thread-safe: RLock pro sync, asyncio.Lock pro async.
    Fail-safe: žádné výjimky ven z veřejných metod.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (Path.home() / '.hledac' / 'dlq' / 'dead_letter_queue.db')
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._async_lock: Optional[asyncio.Lock] = None
        self._initialized = False

    def _get_async_lock(self) -> asyncio.Lock:
        """Lazy async lock initialization."""
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    def _get_connection(self) -> sqlite3.Connection:
        """Získá synchronní SQLite připojení."""
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute(f"PRAGMA max_payload_size={DLQSchema.MAX_PAYLOAD_SIZE}")
        return conn

    async def _get_async_connection(self) -> aiosqlite.Connection:
        """Získá asynchronní SQLite připojení."""
        conn = await aiosqlite.connect(
            str(self.db_path),
            uri=True,
        )
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _initialize_schema(self, conn: sqlite3.Connection) -> None:
        """Inicializuje databázové schéma."""
        conn.executescript(DLQSchema.SCHEMA)
        self._initialized = True

    def _ensure_initialized(self) -> None:
        """Zajistí, že schéma je inicializováno (sync)."""
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            conn = self._get_connection()
            try:
                self._initialize_schema(conn)
            finally:
                conn.close()

    async def _ensure_initialized_async(self) -> None:
        """Zajistí, že schéma je inicializováno (async)."""
        if self._initialized:
            return
        async with self._get_async_lock():
            if self._initialized:
                return
            conn = await self._get_async_connection()
            try:
                await conn.executescript(DLQSchema.SCHEMA)
                self._initialized = True
            finally:
                await conn.close()

    # ---------------------------------------------------------------------------
    # Store
    # ---------------------------------------------------------------------------

    def store_payload(
        self,
        payload_data: bytes,
        sprint_id: str,
        source: str,
        error: Exception,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Uloží payload do DLQ (sync)."""
        try:
            self._ensure_initialized()
        except Exception as e:
            logger.debug("dlq_init_failed: %s", e)
            return ""

        # Bound: truncate oversized payloads
        if len(payload_data) > DLQSchema.MAX_PAYLOAD_SIZE:
            payload_data = payload_data[:DLQSchema.MAX_PAYLOAD_SIZE]

        payload_id = hashlib.sha256(payload_data).hexdigest()

        payload = DLQPayload(
            payload_id=payload_id,
            sprint_id=sprint_id,
            source=source,
            error_type=type(error).__qualname__,
            error_message=str(error)[:1000],  # Bound error message
            payload_data=payload_data,
            metadata=metadata or {},
        )

        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO dlq_payloads
                (payload_id, sprint_id, source, error_type, error_message,
                 payload_data, metadata, created_at, attempt_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    payload.payload_id,
                    payload.sprint_id,
                    payload.source,
                    payload.error_type,
                    payload.error_message,
                    payload.payload_data.hex(),
                    _json_encode(payload.metadata),
                    payload.created_at.isoformat(),
                ),
            )

            # Update stats (fire-and-forget)
            try:
                conn.execute(
                    """
                    INSERT INTO dlq_stats (source, error_type, count, last_occurrence)
                    VALUES (?, ?, 1, ?)
                    ON CONFLICT(source, error_type) DO UPDATE SET
                        count = count + 1,
                        last_occurrence = ?
                    """,
                    (
                        payload.source,
                        payload.error_type,
                        payload.created_at.isoformat(),
                        payload.created_at.isoformat(),
                    ),
                )
            except Exception:
                pass

        except Exception as e:
            logger.debug("dlq_store_failed: %s", e)
        finally:
            conn.close()

        return payload_id

    async def store_payload_async(
        self,
        payload_data: bytes,
        sprint_id: str,
        source: str,
        error: Exception,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Uloží payload do DLQ (async)."""
        try:
            await self._ensure_initialized_async()
        except Exception as e:
            logger.debug("dlq_init_async_failed: %s", e)
            return ""

        # Bound: truncate oversized payloads
        if len(payload_data) > DLQSchema.MAX_PAYLOAD_SIZE:
            payload_data = payload_data[:DLQSchema.MAX_PAYLOAD_SIZE]

        payload_id = hashlib.sha256(payload_data).hexdigest()

        payload = DLQPayload(
            payload_id=payload_id,
            sprint_id=sprint_id,
            source=source,
            error_type=type(error).__qualname__,
            error_message=str(error)[:1000],
            payload_data=payload_data,
            metadata=metadata or {},
        )

        conn = await self._get_async_connection()
        try:
            await conn.execute(
                """
                INSERT OR IGNORE INTO dlq_payloads
                (payload_id, sprint_id, source, error_type, error_message,
                 payload_data, metadata, created_at, attempt_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    payload.payload_id,
                    payload.sprint_id,
                    payload.source,
                    payload.error_type,
                    payload.error_message,
                    payload.payload_data.hex(),
                    _json_encode(payload.metadata),
                    payload.created_at.isoformat(),
                ),
            )

            try:
                await conn.execute(
                    """
                    INSERT INTO dlq_stats (source, error_type, count, last_occurrence)
                    VALUES (?, ?, 1, ?)
                    ON CONFLICT(source, error_type) DO UPDATE SET
                        count = count + 1,
                        last_occurrence = ?
                    """,
                    (
                        payload.source,
                        payload.error_type,
                        payload.created_at.isoformat(),
                        payload.created_at.isoformat(),
                    ),
                )
            except Exception:
                pass

            await conn.commit()
        except Exception as e:
            logger.debug("dlq_store_async_failed: %s", e)
        finally:
            await conn.close()

        return payload_id

    # ---------------------------------------------------------------------------
    # Retrieve
    # ---------------------------------------------------------------------------

    def retrieve_payloads(
        self,
        source: Optional[str] = None,
        error_type: Optional[str] = None,
        sprint_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DLQPayload]:
        """Načte payloady z DLQ podle kritérií (sync)."""
        try:
            self._ensure_initialized()
        except Exception:
            return []

        query = "SELECT * FROM dlq_payloads WHERE 1=1"
        params: list[Any] = []

        if source:
            query += " AND source = ?"
            params.append(source)
        if error_type:
            query += " AND error_type = ?"
            params.append(error_type)
        if sprint_id:
            query += " AND sprint_id = ?"
            params.append(sprint_id)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        conn = self._get_connection()
        try:
            cursor = conn.execute(query, params)
            return [DLQPayload.from_row(row) for row in cursor.fetchall()]
        except Exception:
            return []
        finally:
            conn.close()

    async def retrieve_payloads_async(
        self,
        source: Optional[str] = None,
        error_type: Optional[str] = None,
        sprint_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DLQPayload]:
        """Načte payloady z DLQ podle kritérií (async)."""
        try:
            await self._ensure_initialized_async()
        except Exception:
            return []

        query = "SELECT * FROM dlq_payloads WHERE 1=1"
        params: list[Any] = []

        if source:
            query += " AND source = ?"
            params.append(source)
        if error_type:
            query += " AND error_type = ?"
            params.append(error_type)
        if sprint_id:
            query += " AND sprint_id = ?"
            params.append(sprint_id)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        conn = await self._get_async_connection()
        try:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [DLQPayload.from_row(row) for row in rows]
        except Exception:
            return []
        finally:
            await conn.close()

    # ---------------------------------------------------------------------------
    # Retry tracking
    # ---------------------------------------------------------------------------

    def increment_attempt_count(self, payload_id: str) -> None:
        """Zvýší počet pokusů pro payload (sync)."""
        try:
            self._ensure_initialized()
        except Exception:
            return

        conn = self._get_connection()
        try:
            conn.execute(
                """
                UPDATE dlq_payloads
                SET attempt_count = attempt_count + 1,
                    last_attempt_at = ?
                WHERE payload_id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), payload_id),
            )
        except Exception:
            pass
        finally:
            conn.close()

    async def increment_attempt_count_async(self, payload_id: str) -> None:
        """Zvýší počet pokusů pro payload (async)."""
        try:
            await self._ensure_initialized_async()
        except Exception:
            return

        conn = await self._get_async_connection()
        try:
            await conn.execute(
                """
                UPDATE dlq_payloads
                SET attempt_count = attempt_count + 1,
                    last_attempt_at = ?
                WHERE payload_id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), payload_id),
            )
            await conn.commit()
        except Exception:
            pass
        finally:
            await conn.close()

    # ---------------------------------------------------------------------------
    # Cleanup
    # ---------------------------------------------------------------------------

    def cleanup_old_payloads(self, max_age_days: int = 30) -> int:
        """Smaže staré payloady (>max_age_days dní)."""
        try:
            self._ensure_initialized()
        except Exception:
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "DELETE FROM dlq_payloads WHERE created_at < ?",
                (cutoff.isoformat(),),
            )
            return cursor.rowcount
        except Exception:
            return 0
        finally:
            conn.close()

    async def cleanup_old_payloads_async(self, max_age_days: int = 30) -> int:
        """Smaže staré payloady (async)."""
        try:
            await self._ensure_initialized_async()
        except Exception:
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        conn = await self._get_async_connection()
        try:
            cursor = await conn.execute(
                "DELETE FROM dlq_payloads WHERE created_at < ?",
                (cutoff.isoformat(),),
            )
            await conn.commit()
            return cursor.rowcount
        except Exception:
            return 0
        finally:
            await conn.close()

    # ---------------------------------------------------------------------------
    # Stats
    # ---------------------------------------------------------------------------

    def get_stats(self) -> list[dict[str, Any]]:
        """Získá statistiky DLQ podle zdroje a typu chyby."""
        try:
            self._ensure_initialized()
        except Exception:
            return []

        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT source, error_type, count, last_occurrence
                FROM dlq_stats
                ORDER BY count DESC
                """
            )
            return [
                {
                    'source': row['source'],
                    'error_type': row['error_type'],
                    'count': row['count'],
                    'last_occurrence': row['last_occurrence'],
                }
                for row in cursor.fetchall()
            ]
        except Exception:
            return []
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_dlq_manager: Optional[DLQManager] = None
_dlq_lock = threading.RLock()


def get_dlq_manager() -> DLQManager:
    """Získá globální DLQ manager (thread-safe singleton)."""
    global _dlq_manager

    if _dlq_manager is None:
        with _dlq_lock:
            if _dlq_manager is None:
                _dlq_manager = DLQManager()
    return _dlq_manager


# ---------------------------------------------------------------------------
# Decorator pro automatické ukládání do DLQ
# ---------------------------------------------------------------------------


def dlq_catch(
    source: str,
    serialize_payload: bool = True,
    metadata_extractor: Optional[Callable[..., dict[str, Any]]] = None,
) -> Callable:
    """Dekorátor pro automatické zachycení výjimek a ukládání do DLQ.

    Args:
        source: Identifikátor zdroje (např. "synthesis_runner.synthesize")
        serialize_payload: Pokud True, serializuje args[0] (první argument) jako payload
        metadata_extractor: Volitelná funkce pro extrakci metadata z argumentů

    Použití:
        @dlq_catch(source="my_module.process")
        async def process(data):
            ...

        @dlq_catch(source="my_module.process", metadata_extractor=lambda self, data, **kw: {'id': data.get('id')})
        async def process(self, data):
            ...
    """
    def decorator(func: Callable) -> Callable:
        import functools
        import inspect

        # Determine if async
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    # Serialize payload
                    payload_data = b''
                    if serialize_payload:
                        try:
                            import msgspec as _msgspec
                            if args:
                                payload_data = _msgspec.json.encode(args[0]) if len(args) == 1 else _msgspec.json.encode({'args': args, 'kwargs': kwargs})
                            else:
                                payload_data = _msgspec.json.encode(kwargs) if kwargs else b''
                        except Exception:
                            payload_data = b'serialization_failed'

                    # Extract metadata
                    metadata: dict[str, Any] = {}
                    if metadata_extractor:
                        try:
                            metadata = metadata_extractor(*args, **kwargs)
                        except Exception:
                            pass

                    # Get sprint_id from ENV if available
                    sprint_id = "unknown"
                    try:
                        from hledac.universal.core.env_config import ENV
                        sprint_id = ENV.get('HLEDAC_SPRINT_ID', 'unknown')
                    except Exception:
                        pass

                    # Store in DLQ (fire-and-forget)
                    try:
                        dlq = get_dlq_manager()
                        dlq.store_payload(
                            payload_data=payload_data,
                            sprint_id=sprint_id,
                            source=source,
                            error=e,
                            metadata=metadata,
                        )
                    except Exception as dlq_error:
                        logger.debug("dlq_catch_store_failed: %s", dlq_error)

                    # Re-raise original exception
                    raise

            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    # Serialize payload
                    payload_data = b''
                    if serialize_payload:
                        try:
                            import msgspec as _msgspec
                            if args:
                                payload_data = _msgspec.json.encode(args[0]) if len(args) == 1 else _msgspec.json.encode({'args': args, 'kwargs': kwargs})
                            else:
                                payload_data = _msgspec.json.encode(kwargs) if kwargs else b''
                        except Exception:
                            payload_data = b'serialization_failed'

                    # Extract metadata
                    metadata = {}
                    if metadata_extractor:
                        try:
                            metadata = metadata_extractor(*args, **kwargs)
                        except Exception:
                            pass

                    # Get sprint_id
                    sprint_id = "unknown"
                    try:
                        from hledac.universal.core.env_config import ENV
                        sprint_id = ENV.get('HLEDAC_SPRINT_ID', 'unknown')
                    except Exception:
                        pass

                    # Store in DLQ
                    try:
                        dlq = get_dlq_manager()
                        dlq.store_payload(
                            payload_data=payload_data,
                            sprint_id=sprint_id,
                            source=source,
                            error=e,
                            metadata=metadata,
                        )
                    except Exception as dlq_error:
                        logger.debug("dlq_catch_store_failed: %s", dlq_error)

                    raise

            return sync_wrapper

    return decorator
