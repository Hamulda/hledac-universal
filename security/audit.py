"""
Audit Forensics - Audit Trail pro Ultra Deep Research

Pro:
- Auditování výzkumných operací
- Forenzní analýza
- Compliance reporting
- Incident investigation

ISSUE-001 Phase 2: SQLite3 → DuckDB Migration
- AuditLogger now uses DuckDB via DuckDBAuditStore
"""
import asyncio
import msgspec
import hashlib
import hmac
import logging
import msgspec.json as _json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
logger = logging.getLogger(__name__)

class AuditLevel(Enum):
    """Úrovně auditu"""
    DEBUG = 'debug'
    INFO = 'info'
    WARNING = 'warning'
    ERROR = 'error'
    CRITICAL = 'critical'

class AuditEventType(Enum):
    """Typy audit událostí"""
    QUERY = 'query'
    DATA_ACCESS = 'data_access'
    DATA_STORE = 'data_store'
    DATA_DELETE = 'data_delete'
    LOGIN = 'login'
    LOGOUT = 'logout'
    CONFIG_CHANGE = 'config_change'
    SECURITY_ALERT = 'security_alert'
    SYSTEM_EVENT = 'system_event'

class AuditEvent(msgspec.Struct):
    """Audit událost"""
    timestamp: datetime
    event_type: AuditEventType
    action: str
    resource: str
    user_id: str | None
    session_id: str | None
    details: dict[str, Any]
    level: AuditLevel
    hash: str = field(default='')
    _hmac_key: bytes | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Vypočítat hash pro integrity"""
        if not self.hash:
            self.hash = self._calculate_hash()

    def _calculate_hash(self) -> str:
        """Vypočítat HMAC hash pro integritu všech polí"""
        data = '|'.join([self.timestamp.isoformat(), self.event_type.value, self.action, self.resource, self.user_id or '', self.session_id or '', _json.encode(self.details).decode('utf-8'), self.level.value])
        if self._hmac_key:
            return hmac.new(self._hmac_key, data.encode(), hashlib.sha256).hexdigest()
        return hashlib.sha256(data.encode()).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        """Export jako slovník"""
        return {'timestamp': self.timestamp.isoformat(), 'event_type': self.event_type.value, 'action': self.action, 'resource': self.resource, 'user_id': self.user_id, 'session_id': self.session_id, 'details': self.details, 'level': self.level.value, 'hash': self.hash}

class AuditConfig(msgspec.Struct):
    """Konfigurace auditu"""
    db_path: str = 'storage/audit.db'
    min_level: AuditLevel = AuditLevel.INFO
    log_to_console: bool = True
    log_to_file: bool = True
    retention_days: int = 90
    encrypt_logs: bool = True
    hmac_key: bytes | None = None

class AuditLogger:
    """
    Logger pro auditování s integrity protection.

    ISSUE-001 Phase 2: SQLite3 → DuckDB Migration
    Uses DuckDBAuditStore internally for better analytics and M1 optimization.

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
    __slots__ = tuple(('_duckdb_store', '_hmac_key', '_initialized', 'config'))

    def __init__(self, config: AuditConfig | None=None) -> None:
        self.config = config or AuditConfig()
        self._duckdb_store: Any = None
        self._initialized = False
        if self.config.hmac_key is None:
            self.config.hmac_key = os.urandom(32)
        self._hmac_key: bytes = self.config.hmac_key

    async def initialize(self) -> None:
        """Initialize DuckDB-backed audit store."""
        if self._initialized:
            return
        from knowledge.duckdb_audit_store import DuckDBAuditStore
        self._duckdb_store = DuckDBAuditStore()
        await self._duckdb_store.initialize()
        self._initialized = True
        logger.info('[AUDIT] AuditLogger initialized (DuckDB)')

    async def log(self, event_type: AuditEventType, action: str, resource: str, details: dict[str, Any] | None=None, level: AuditLevel=AuditLevel.INFO, user_id: str | None=None, session_id: str | None=None) -> bool:
        """Log audit event to DuckDB."""
        if not self._initialized:
            await self.initialize()
        if level.value < self.config.min_level.value:
            return True
        event = AuditEvent(timestamp=datetime.now(UTC), event_type=event_type, action=action, resource=resource, user_id=user_id, session_id=session_id, details=details or {}, level=level, _hmac_key=self._hmac_key)
        try:
            await self._duckdb_store.log(event)
            if self.config.log_to_console:
                logger.info(f'AUDIT: {event.event_type.value} - {event.action} on {event.resource}')
            return True
        except Exception as e:
            logger.error(f'Failed to log audit event: {e}')
            return False

    async def query(self, event_type: AuditEventType | None=None, resource: str | None=None, start_time: datetime | None=None, end_time: datetime | None=None, limit: int=100) -> list[AuditEvent]:
        """Query audit events from DuckDB."""
        if not self._initialized:
            await self.initialize()
        if self._duckdb_store is None:
            return []
        return await self._duckdb_store.query(event_type=event_type, resource=resource, start_time=start_time, end_time=end_time, limit=limit)

    async def get_report(self, start_time: datetime | None=None, end_time: datetime | None=None) -> dict[str, Any]:
        """Generate audit report from DuckDB."""
        if not self._initialized:
            await self.initialize()
        if self._duckdb_store is None:
            return {}
        return await self._duckdb_store.get_report(start_time=start_time, end_time=end_time)

    async def close(self) -> None:
        """Close DuckDB connection."""
        self._duckdb_store = None
        self._initialized = False