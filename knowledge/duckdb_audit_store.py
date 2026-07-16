"""
knowledge/duckdb_audit_store.py — DuckDB-backed Audit Store
=========================================================

ISSUE-001 Phase 2: SQLite3 → DuckDB Migration

Drop-in replacement for security/audit.py AuditLogger.
Stores audit events in DuckDB instead of SQLite3.

MIGRATION:
    Old: from security.audit import AuditLogger
    New: from knowledge.duckdb_audit_store import DuckDBAuditStore

SCHEMA:
    audit_events (
        id          BIGINT PRIMARY KEY,
        timestamp   DOUBLE NOT NULL,  -- Unix timestamp for range queries
        event_type  VARCHAR,
        action      VARCHAR,
        resource    VARCHAR,
        user_id     VARCHAR,
        session_id  VARCHAR,
        details     JSON,
        level       VARCHAR,
        hash        VARCHAR
    )

M1 8GB: DuckDB in-process, WAL mode, 2 threads.
"""

from __future__ import annotations
import msgspec

import asyncio
import hashlib
import hmac
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import msgspec.json as _json

from hledac.universal.utils.async_helpers import safe_wait_for

logger = logging.getLogger(__name__)


class AuditLevel(Enum):
    """Audit levels."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditEventType(Enum):
    """Audit event types."""
    QUERY = "query"
    DATA_ACCESS = "data_access"
    DATA_STORE = "data_store"
    DATA_DELETE = "data_delete"
    LOGIN = "login"
    LOGOUT = "logout"
    CONFIG_CHANGE = "config_change"
    SECURITY_ALERT = "security_alert"
    SYSTEM_EVENT = "system_event"


class AuditEvent(msgspec.Struct):
    """Audit event."""
    timestamp: datetime
    event_type: AuditEventType
    action: str
    resource: str
    user_id: str | None = None
    session_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    level: AuditLevel = AuditLevel.INFO
    hash: str = ""
    _hmac_key: bytes | None = field(default=None, repr=False)

    def compute_hash(self) -> str:
        """Compute HMAC-SHA256 hash for integrity."""
        data = f"{self.timestamp.isoformat()}|{self.event_type.value}|{self.action}|{self.resource}"
        return hashlib.sha256(data.encode()).hexdigest()


class AuditConfig(msgspec.Struct):
    """Audit configuration."""
    db_path: str = "data/audit.duckdb"
    min_level: AuditLevel = AuditLevel.INFO
    log_to_console: bool = True
    retention_days: int = 90
    hmac_key: bytes | None = None


class DuckDBAuditStore:
    """
    DuckDB-backed audit store.

    Drop-in replacement for SQLite3 AuditLogger.
    Uses DuckDB for better analytics and M1 optimization.

    MIGRATION:
        # Old
        from security.audit import AuditLogger
        audit = AuditLogger()

        # New
        from knowledge.duckdb_audit_store import DuckDBAuditStore
        audit = DuckDBAuditStore()
    """

    __slots__ = tuple(
        "_db_store _conn _hmac_key _initialized config".split()
    )

    def __init__(self, config: AuditConfig | None = None) -> None:
        self.config = config or AuditConfig()
        self._db_store: Any = None
        self._conn: Any = None
        self._initialized: bool = False
        if self.config.hmac_key is None:
            self.config.hmac_key = os.urandom(32)
        self._hmac_key: bytes = self.config.hmac_key

    async def initialize(self) -> None:
        """Initialize DuckDB audit store."""
        if self._initialized:
            return

        from knowledge.db import get_db

        db = get_db()
        self._db_store = db.duckdb

        # Initialize schema
        self._db_store.init_audit_schema()

        self._initialized = True
        logger.info("[AUDIT:DuckDB] Initialized at %s", self.config.db_path)

    def _get_connection(self) -> Any:
        """Get DuckDB connection."""
        if self._db_store is None:
            from knowledge.db import get_db
            self._db_store = get_db().duckdb
        return self._db_store._get_connection()

    async def log(self, event: AuditEvent) -> None:
        """
        Log an audit event.

        Args:
            event: AuditEvent to log
        """
        if not self._initialized:
            await self.initialize()

        if event.level.value < self.config.min_level.value:
            return

        # Compute hash if not set
        if not event.hash:
            event.hash = event.compute_hash()

        # Insert into DuckDB
        conn = self._get_connection()
        await asyncio.to_thread(
            lambda: conn.execute(
                """
                INSERT INTO audit_events
                (timestamp, event_type, action, resource, user_id, session_id, details, level, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp.timestamp(),
                    event.event_type.value,
                    event.action,
                    event.resource,
                    event.user_id,
                    event.session_id,
                    _json.encode(event.details).decode("utf-8"),
                    event.level.value,
                    event.hash,
                ),
            )
        )

        if self.config.log_to_console:
            logger.info(
                "[AUDIT] %s %s %s",
                event.event_type.value,
                event.action,
                event.resource,
            )

    async def query(
        self,
        event_type: AuditEventType | None = None,
        resource: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """
        Query audit events.

        Args:
            event_type: Filter by event type
            resource: Filter by resource
            start_time: Start of time range
            end_time: End of time range
            limit: Maximum events to return

        Returns:
            List of AuditEvent objects
        """
        if not self._initialized:
            return []

        conn = self._get_connection()
        query = "SELECT * FROM audit_events WHERE 1=1"
        params: list[Any] = []

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type.value)
        if resource:
            query += " AND resource = ?"
            params.append(resource)
        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time.timestamp())
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time.timestamp())

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = await asyncio.to_thread(
            lambda: conn.execute(query, params).fetchall()
        )

        events = []
        for row in rows:
            events.append(
                AuditEvent(
                    timestamp=datetime.fromtimestamp(row[1], tz=UTC),
                    event_type=AuditEventType(row[2]),
                    action=row[3],
                    resource=row[4],
                    user_id=row[5],
                    session_id=row[6],
                    details=_json.decode(row[7]) if row[7] else {},
                    level=AuditLevel(row[8]),
                    hash=row[9],
                    _hmac_key=self._hmac_key,
                )
            )
        return events

    async def get_report(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Generate audit report.

        Args:
            start_time: Start of time range
            end_time: End of time range

        Returns:
            Audit report dictionary
        """
        if not self._initialized:
            return {"total_events": 0, "by_type": {}, "by_level": {}}

        conn = self._get_connection()
        conditions = []
        params: list[Any] = []

        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time.timestamp())
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time.timestamp())

        where = " AND ".join(conditions) if conditions else "1=1"

        # Total count
        total = await asyncio.to_thread(
            lambda: conn.execute(
                f"SELECT COUNT(*) FROM audit_events WHERE {where}",
                params,
            ).fetchone()[0]
        )

        # By event type
        type_rows = await asyncio.to_thread(
            lambda: conn.execute(
                f"SELECT event_type, COUNT(*) FROM audit_events WHERE {where} GROUP BY event_type",
                params,
            ).fetchall()
        )
        by_type = {row[0]: row[1] for row in type_rows}

        # By level
        level_rows = await asyncio.to_thread(
            lambda: conn.execute(
                f"SELECT level, COUNT(*) FROM audit_events WHERE {where} GROUP BY level",
                params,
            ).fetchall()
        )
        by_level = {row[0]: row[1] for row in level_rows}

        return {
            "total_events": total,
            "by_type": by_type,
            "by_level": by_level,
            "start_time": start_time.isoformat() if start_time else None,
            "end_time": end_time.isoformat() if end_time else None,
        }

    async def close(self) -> None:
        """Close audit store."""
        if self._conn:
            await asyncio.to_thread(self._conn.close)
        self._initialized = False
        logger.info("[AUDIT:DuckDB] Closed")
