"""
Audit Forensics - Audit Trail pro Ultra Deep Research

Pro:
- Auditování výzkumných operací
- Forenzní analýza
- Compliance reporting
- Incident investigation
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AuditLevel(Enum):
    """Úrovně auditu"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditEventType(Enum):
    """Typy audit událostí"""
    QUERY = "query"
    DATA_ACCESS = "data_access"
    DATA_STORE = "data_store"
    DATA_DELETE = "data_delete"
    LOGIN = "login"
    LOGOUT = "logout"
    CONFIG_CHANGE = "config_change"
    SECURITY_ALERT = "security_alert"
    SYSTEM_EVENT = "system_event"


@dataclass
class AuditEvent:
    """Audit událost"""
    timestamp: datetime
    event_type: AuditEventType
    action: str
    resource: str
    user_id: str | None
    session_id: str | None
    details: dict[str, Any]
    level: AuditLevel
    hash: str = field(default="")
    _hmac_key: bytes | None = field(default=None, repr=False)

    def __post_init__(self):
        """Vypočítat hash pro integrity"""
        if not self.hash:
            self.hash = self._calculate_hash()

    def _calculate_hash(self) -> str:
        """Vypočítat HMAC hash pro integritu všech polí"""
        # Include all fields for complete integrity protection
        data = "|".join([
            self.timestamp.isoformat(),
            self.event_type.value,
            self.action,
            self.resource,
            self.user_id or "",
            self.session_id or "",
            json.dumps(self.details, sort_keys=True),
            self.level.value,
        ])
        if self._hmac_key:
            return hmac.new(self._hmac_key, data.encode(), hashlib.sha256).hexdigest()
        # Fallback: key not available, use SHA256 (should not happen when properly configured)
        return hashlib.sha256(data.encode()).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        """Export jako slovník"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "action": self.action,
            "resource": self.resource,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "details": self.details,
            "level": self.level.value,
            "hash": self.hash,
        }


@dataclass
class AuditConfig:
    """Konfigurace auditu"""
    db_path: str = "storage/audit.db"
    min_level: AuditLevel = AuditLevel.INFO
    log_to_console: bool = True
    log_to_file: bool = True
    retention_days: int = 90
    encrypt_logs: bool = True
    hmac_key: bytes | None = None


class AuditLogger:
    """
    Logger pro auditování s integrity protection.

    Ukládá audit trail pro:
    - Výzkumné dotazy
    - Přístup k datům
    - Bezpečnostní události
    - Compliance reporting

    Example:
        >>> audit = AuditLogger()
        >>> await audit.log(
        ...     event_type=AuditEventType.QUERY,
        ...     action="search",
        ...     resource="database_x",
        ...     details={"query": "sensitive_topic"},
        ... )
    """

    def __init__(self, config: AuditConfig = None):
        self.config = config or AuditConfig()
        self._db: sqlite3.Connection | None = None
        self._initialized = False
        # Generate HMAC key if not provided
        if self.config.hmac_key is None:
            self.config.hmac_key = os.urandom(32)
        self._hmac_key: bytes = self.config.hmac_key

    async def initialize(self) -> None:
        """Inicializovat databázi"""
        Path(self.config.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db = await asyncio.to_thread(lambda: sqlite3.connect(self.config.db_path))
        await asyncio.to_thread(lambda: self._db.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                action TEXT NOT NULL,
                resource TEXT NOT NULL,
                user_id TEXT,
                session_id TEXT,
                details TEXT,
                level TEXT NOT NULL,
                hash TEXT NOT NULL
            )
        """))

        # Indexy pro rychlé vyhledávání
        await asyncio.to_thread(lambda: self._db.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON audit_events(timestamp)"))
        await asyncio.to_thread(lambda: self._db.execute("CREATE INDEX IF NOT EXISTS idx_event_type ON audit_events(event_type)"))
        await asyncio.to_thread(lambda: self._db.execute("CREATE INDEX IF NOT EXISTS idx_resource ON audit_events(resource)"))

        await asyncio.to_thread(lambda: self._db.commit())
        self._initialized = True

        logger.info(f"AuditLogger initialized: {self.config.db_path}")

    async def log(
        self,
        event_type: AuditEventType,
        action: str,
        resource: str,
        details: dict[str, Any] = None,
        level: AuditLevel = AuditLevel.INFO,
        user_id: str = None,
        session_id: str = None,
    ) -> bool:
        """
        Zalogovat audit událost.

        Args:
            event_type: Typ události
            action: Provedená akce
            resource: Zdroj
            details: Detaily
            level: Úroveň
            user_id: ID uživatele
            session_id: ID relace

        Returns:
            True pokud úspěšné
        """
        if not self._initialized:
            return False

        # Kontrolovat úroveň
        if level.value < self.config.min_level.value:
            return True

        event = AuditEvent(
            timestamp=datetime.now(),
            event_type=event_type,
            action=action,
            resource=resource,
            user_id=user_id,
            session_id=session_id,
            details=details or {},
            level=level,
            _hmac_key=self._hmac_key,
        )

        try:
            # Uložit do databáze
            await asyncio.to_thread(lambda: self._db.execute("""
                INSERT INTO audit_events
                (timestamp, event_type, action, resource, user_id, session_id, details, level, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.timestamp.isoformat(),
                event.event_type.value,
                event.action,
                event.resource,
                event.user_id,
                event.session_id,
                json.dumps(event.details),
                event.level.value,
                event.hash,
            )))
            await asyncio.to_thread(lambda: self._db.commit())

            # Logovat do konzole pokud povoleno
            if self.config.log_to_console:
                logger.info(f"AUDIT: {event.event_type.value} - {event.action} on {event.resource}")

            return True

        except Exception as e:
            logger.error(f"Failed to log audit event: {e}")
            return False

    async def query(
        self,
        event_type: AuditEventType = None,
        resource: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = 100
    ) -> list[AuditEvent]:
        """
        Query audit log.

        Args:
            event_type: Filtrovat podle typu
            resource: Filtrovat podle zdroje
            start_time: Od
            end_time: Do
            limit: Limit výsledků

        Returns:
            Seznam audit událostí
        """
        if not self._initialized:
            return []

        query = "SELECT * FROM audit_events WHERE 1=1"
        params = []

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type.value)

        if resource:
            query += " AND resource = ?"
            params.append(resource)

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time.isoformat())

        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time.isoformat())

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await asyncio.to_thread(lambda: self._db.execute(query, params))
        events = []

        for row in cursor:
            events.append(AuditEvent(
                timestamp=datetime.fromisoformat(row[1]),
                event_type=AuditEventType(row[2]),
                action=row[3],
                resource=row[4],
                user_id=row[5],
                session_id=row[6],
                details=json.loads(row[7]) if row[7] else {},
                level=AuditLevel(row[8]),
                hash=row[9],
                _hmac_key=self._hmac_key,
            ))

        return events

    async def get_report(
        self,
        start_time: datetime = None,
        end_time: datetime = None
    ) -> dict[str, Any]:
        """
        Vygenerovat audit report.

        Args:
            start_time: Od
            end_time: Do

        Returns:
            Report statistiky
        """
        if not self._initialized:
            return {}

        query = "SELECT event_type, COUNT(*) FROM audit_events WHERE 1=1"
        params = []

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time.isoformat())

        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time.isoformat())

        query += " GROUP BY event_type"

        cursor = await asyncio.to_thread(lambda: self._db.execute(query, params))
        stats = {row[0]: row[1] for row in cursor}

        return {
            "period": {
                "start": start_time.isoformat() if start_time else "all",
                "end": end_time.isoformat() if end_time else "all",
            },
            "event_counts": stats,
            "total_events": sum(stats.values()),
        }

    async def close(self) -> None:
        """Zavřít databázi"""
        if self._db:
            await asyncio.to_thread(lambda: self._db.close())
            self._db = None
