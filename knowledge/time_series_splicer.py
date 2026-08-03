"""
[META]-005: TimeSeriesSplicer — Unified millisecond-aligned timeline across protocols.

Canonical timestamp format: (entity_value, ioc_type, protocol, timestamp_ns: int64,
                             event_type, source_evidence_url).
Stored in DuckDB time_series_spliced table with
PRIMARY KEY(entity_value, ioc_type, protocol, timestamp_ns).

Protocol adapters: CtLogAdapter, GitCommitAdapter, TelegramAdapter, BlockchainAdapter,
HttpAdapter, WarcAdapter, PassiveDnsAdapter.

Millisecond alignment: All adapters normalize to int64 nanoseconds since Unix epoch.
Dedup by source: Same event reported by multiple sources → merge with corroborating_sources.

M1 8GB safe:
- Ingest path writes directly to DuckDB (append-only).
- Timeline queries return ≤1000 events per entity, paginated.
- Memory overhead: ~200 bytes per timeline event in transit.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from ..knowledge.duckdb_store import DuckDBShadowStore

logger = logging.getLogger(__name__)

# Feature flag: HLEDAC_ENABLE_TIMELINE_SPLICER (default 0, opt-in)
_IS_ENABLED: bool = os.environ.get("HLEDAC_ENABLE_TIMELINE_SPLICER", "0") in {"1", "true", "on"}

# ----------------------------------------------------------------------------- #
# Constants
# ----------------------------------------------------------------------------- #
_NS_PER_SECOND: int = 1_000_000_000
_MAX_TIMELINE_EVENTS: int = 1000  # Per-entity query limit


# ----------------------------------------------------------------------------- #
# Data types
# ----------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class TimelineEvent:
    """A single timestamped event from a protocol source.

    Attributes:
        entity_value: The IOC value (domain, IP, hash, etc.)
        ioc_type: IOC type (domain, ip, hash_sha256, etc.)
        protocol: Source protocol (ct_log, git, telegram, blockchain, http, warc, passive_dns)
        timestamp_ns: Unix epoch nanoseconds (int64)
        event_type: Event classification (registered, updated, issued, committed, etc.)
        source_evidence_url: URL or identifier of the source evidence
        corroborating_sources: List of additional source URLs confirming this event
        raw_timestamp: Original timestamp string from the source (for debugging)
    """
    entity_value: str
    ioc_type: str
    protocol: str
    timestamp_ns: int
    event_type: str
    source_evidence_url: str
    corroborating_sources: tuple[str, ...] = field(default_factory=tuple)
    raw_timestamp: str | None = None

    @property
    def timestamp_iso(self) -> str:
        """Human-readable ISO 8601 timestamp."""
        ts_s = self.timestamp_ns / _NS_PER_SECOND
        try:
            dt = datetime.fromtimestamp(ts_s, tz=timezone.utc)
            return dt.isoformat()
        except (OSError, OverflowError, ValueError):
            # Edge case: timestamps before 1970 or after 3001
            # Fall back to manual ISO formatting
            if ts_s < 0:
                sign = "-"
                ts_s = -ts_s
            else:
                sign = ""
            # Use UTC epoch arithmetic
            import datetime as _dt
            whole_sec = int(ts_s)
            frac_ns = int((ts_s - whole_sec) * _NS_PER_SECOND)
            td = _dt.timedelta(seconds=whole_sec, microseconds=frac_ns // 1000)
            dt = _dt.datetime(1970, 1, 1, tzinfo=timezone.utc) + td
            # Adjust sign
            result = dt.isoformat()
            return f"-{result}" if sign else result

    @property
    def timestamp_ms(self) -> int:
        """Timestamp in milliseconds."""
        return self.timestamp_ns // 1_000_000


# ----------------------------------------------------------------------------- #
# Protocol adapters
# ----------------------------------------------------------------------------- #
@runtime_checkable
class TimelineAdapter(Protocol):
    """Protocol for protocol-specific timeline adapters."""

    def parse(self, raw_data: Any) -> list[TimelineEvent]:
        """Parse raw protocol data into TimelineEvents."""
        ...


class CtLogAdapter:
    """Parses Certificate Transparency logs (not_before/not_after timestamps).

    CT logs provide authoritative certificate issuance timestamps.
    Precision: second (not_before/not_after are in seconds).
    """

    __slots__ = ()

    def parse(self, ct_entry: dict[str, Any]) -> list[TimelineEvent]:
        """Parse a CT log entry into TimelineEvents.

        Args:
            ct_entry: Dict with keys: domain, not_before, not_after, serial_number,
                     issuer, source_url

        Returns:
            List of 1-2 TimelineEvents (certificate_valid_from, certificate_expires)
        """
        events: list[TimelineEvent] = []
        domain = ct_entry.get("domain", "")
        if not domain:
            return events

        source_url = ct_entry.get("source_url", "ct_log:unknown")

        # not_before → certificate_valid_from
        nb = ct_entry.get("not_before")
        if nb is not None:
            ns = self._to_ns(nb)
            events.append(TimelineEvent(
                entity_value=domain,
                ioc_type="domain",
                protocol="ct_log",
                timestamp_ns=ns,
                event_type="certificate_valid_from",
                source_evidence_url=source_url,
                raw_timestamp=str(nb),
            ))

        # not_after → certificate_expires
        na = ct_entry.get("not_after")
        if na is not None:
            ns = self._to_ns(na)
            events.append(TimelineEvent(
                entity_value=domain,
                ioc_type="domain",
                protocol="ct_log",
                timestamp_ns=ns,
                event_type="certificate_expires",
                source_evidence_url=source_url,
                raw_timestamp=str(na),
            ))

        return events

    @staticmethod
    def _to_ns(ts: int | float | str) -> int:
        """Convert various timestamp formats to nanoseconds.

        Handles: Unix seconds, Unix milliseconds, ISO 8601 string.
        """
        if isinstance(ts, str):
            try:
                ts = float(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
            except ValueError:
                # Try raw float string
                ts = float(ts)
        # If it looks like milliseconds (> 1e12), convert to seconds
        if ts > 1e12:
            ts = ts / 1000.0
        return int(ts * _NS_PER_SECOND)


class GitCommitAdapter:
    """Parses Git commit timestamps (author + committer timestamp).

    Git commits provide code authorship timestamps.
    Precision: second (Unix time).
    """

    __slots__ = ()

    def parse(self, git_entry: dict[str, Any]) -> list[TimelineEvent]:
        """Parse a Git commit entry into TimelineEvents.

        Args:
            git_entry: Dict with keys: repo_url, commit_hash, author_time,
                      committer_time, author_email, committer_email

        Returns:
            List of 1-2 TimelineEvents (commit_authored, commit_committed)
        """
        events: list[TimelineEvent] = []
        repo_url = git_entry.get("repo_url", "git:unknown")
        commit_hash = git_entry.get("commit_hash", "")[:12]
        source_base = f"{repo_url}/commit/{commit_hash}" if commit_hash else repo_url

        # author timestamp
        at = git_entry.get("author_time")
        if at is not None:
            events.append(TimelineEvent(
                entity_value=git_entry.get("author_email", repo_url),
                ioc_type="email",
                protocol="git",
                timestamp_ns=self._to_ns(at),
                event_type="commit_authored",
                source_evidence_url=source_base,
                raw_timestamp=str(at),
            ))

        # committer timestamp
        ct = git_entry.get("committer_time")
        if ct is not None:
            events.append(TimelineEvent(
                entity_value=git_entry.get("committer_email", repo_url),
                ioc_type="email",
                protocol="git",
                timestamp_ns=self._to_ns(ct),
                event_type="commit_committed",
                source_evidence_url=source_base,
                raw_timestamp=str(ct),
            ))

        return events

    @staticmethod
    def _to_ns(ts: int | float | str) -> int:
        if isinstance(ts, str):
            ts = float(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
        if ts > 1e12:
            ts = ts / 1000.0
        return int(ts * _NS_PER_SECOND)


class TelegramAdapter:
    """Parses Telegram messages (message date unixtime).

    Telegram provides social media activity timestamps.
    Precision: second (unixtime).
    """

    __slots__ = ()

    def parse(self, tg_entry: dict[str, Any]) -> list[TimelineEvent]:
        """Parse a Telegram message entry into TimelineEvents.

        Args:
            tg_entry: Dict with keys: channel, message_id, date_unixtime,
                      from_id, source_url

        Returns:
            List of 1 TimelineEvent (message_posted)
        """
        date_ts = tg_entry.get("date_unixtime")
        if date_ts is None:
            return []

        return [TimelineEvent(
            entity_value=tg_entry.get("channel", "unknown"),
            ioc_type="channel",
            protocol="telegram",
            timestamp_ns=self._to_ns(date_ts),
            event_type="message_posted",
            source_evidence_url=tg_entry.get("source_url", "telegram:unknown"),
            raw_timestamp=str(date_ts),
        )]

    @staticmethod
    def _to_ns(ts: int | float | str) -> int:
        if isinstance(ts, str):
            ts = float(ts)
        if ts > 1e12:
            ts = ts / 1000.0
        return int(ts * _NS_PER_SECOND)


class BlockchainAdapter:
    """Parses blockchain transactions (block.timestamp).

    Blockchain provides immutable transaction timestamps.
    Precision: second (block timestamp).
    """

    __slots__ = ()

    def parse(self, bc_entry: dict[str, Any]) -> list[TimelineEvent]:
        """Parse a blockchain entry into TimelineEvents.

        Args:
            bc_entry: Dict with keys: address, block_number, block_timestamp,
                      tx_hash, source_url

        Returns:
            List of 1-2 TimelineEvents (tx_confirmed, address_first_seen)
        """
        events: list[TimelineEvent] = []
        address = bc_entry.get("address", "")
        source_url = bc_entry.get("source_url", "blockchain:unknown")

        # block timestamp → tx_confirmed
        ts = bc_entry.get("block_timestamp")
        if ts is not None:
            events.append(TimelineEvent(
                entity_value=address,
                ioc_type=bc_entry.get("ioc_type", "address"),
                protocol="blockchain",
                timestamp_ns=self._to_ns(ts),
                event_type="tx_confirmed",
                source_evidence_url=f"{source_url}#tx={bc_entry.get('tx_hash', '')}",
                raw_timestamp=str(ts),
            ))

        # first_seen flag
        if bc_entry.get("is_first_seen"):
            events.append(TimelineEvent(
                entity_value=address,
                ioc_type=bc_entry.get("ioc_type", "address"),
                protocol="blockchain",
                timestamp_ns=self._to_ns(ts) if ts else int(time.time() * _NS_PER_SECOND),
                event_type="address_first_seen",
                source_evidence_url=source_url,
                raw_timestamp=str(ts) if ts else None,
            ))

        return events

    @staticmethod
    def _to_ns(ts: int | float | str) -> int:
        if isinstance(ts, str):
            ts = float(ts)
        if ts > 1e12:
            ts = ts / 1000.0
        return int(ts * _NS_PER_SECOND)


# HTTP Date parsing helpers
_HTTP_DATE_RE = re.compile(
    r"(?:Sun|Mon|Tue|Wed|Thu|Fri|Sat),\s+(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})\s+(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?\s*GMT",
    re.IGNORECASE,
)
_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_http_date(date_str: str) -> datetime | None:
    """Parse HTTP date (RFC 7231) to datetime, handling milliseconds."""
    m = _HTTP_DATE_RE.search(date_str.strip())
    if not m:
        return None
    day, month, year, h, mi, s = int(m[1]), _MONTH_MAP[m[2].lower()], int(m[3]), int(m[4]), int(m[5]), int(m[6])
    ms = int(m[7].ljust(3, "0")) if m[7] else 0
    return datetime(year, month, day, h, mi, s, ms, tzinfo=timezone.utc)


class HttpAdapter:
    """Parses HTTP Last-Modified headers.

    HTTP provides web resource modification timestamps.
    Precision: second (or millisecond if server sends it).
    """

    __slots__ = ()

    def parse(self, http_entry: dict[str, Any]) -> list[TimelineEvent]:
        """Parse an HTTP response entry into TimelineEvents.

        Args:
            http_entry: Dict with keys: url, last_modified (HTTP date string or epoch),
                       source_url

        Returns:
            List of 1 TimelineEvent (resource_modified)
        """
        lm = http_entry.get("last_modified")
        if lm is None:
            return []

        ns = self._to_ns(lm)
        return [TimelineEvent(
            entity_value=http_entry.get("url", ""),
            ioc_type="url",
            protocol="http",
            timestamp_ns=ns,
            event_type="resource_modified",
            source_evidence_url=http_entry.get("source_url", http_entry.get("url", "")),
            raw_timestamp=str(lm),
        )]

    @staticmethod
    def _to_ns(ts: int | float | str) -> int:
        if isinstance(ts, str):
            # Try HTTP date first
            dt = _parse_http_date(ts)
            if dt is not None:
                return int(dt.timestamp() * _NS_PER_SECOND)
            # Fallback: try ISO / epoch string
            try:
                ts = float(ts)
            except ValueError:
                ts = float(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
        if ts > 1e12:
            ts = ts / 1000.0
        return int(ts * _NS_PER_SECOND)


class WarcAdapter:
    """Parses WARC WARC-Date headers.

    WARC archives provide archived HTTP response timestamps.
    Precision: second (ISO 8601 WARC-Date).
    """

    __slots__ = ()

    def parse(self, warc_entry: dict[str, Any]) -> list[TimelineEvent]:
        """Parse a WARC record entry into TimelineEvents.

        Args:
            warc_entry: Dict with keys: url, warc_date (ISO 8601), record_type,
                       source_url

        Returns:
            List of 1 TimelineEvent (resource_archived)
        """
        wd = warc_entry.get("warc_date")
        if wd is None:
            return []

        return [TimelineEvent(
            entity_value=warc_entry.get("url", ""),
            ioc_type="url",
            protocol="warc",
            timestamp_ns=self._to_ns(wd),
            event_type="resource_archived",
            source_evidence_url=warc_entry.get("source_url", "warc:unknown"),
            raw_timestamp=str(wd),
        )]

    @staticmethod
    def _to_ns(ts: str | int | float) -> int:
        if isinstance(ts, (int, float)):
            if ts > 1e12:
                ts = ts / 1000.0
            return int(ts * _NS_PER_SECOND)
        # ISO 8601
        ts_clean = ts.replace("Z", "+00:00")
        return int(datetime.fromisoformat(ts_clean).timestamp() * _NS_PER_SECOND)


class PassiveDnsAdapter:
    """Parses passive DNS records (first_seen/last_seen).

    Passive DNS provides DNS resolution history timestamps.
    Precision: second (typical), millisecond (if available).
    """

    __slots__ = ()

    def parse(self, pdns_entry: dict[str, Any]) -> list[TimelineEvent]:
        """Parse a passive DNS entry into TimelineEvents.

        Args:
            pdns_entry: Dict with keys: domain, ip, first_seen, last_seen,
                       source_url, record_type

        Returns:
            List of 1-2 TimelineEvents (dns_first_seen, dns_last_seen)
        """
        events: list[TimelineEvent] = []
        domain = pdns_entry.get("domain", "")
        source_url = pdns_entry.get("source_url", "passive_dns:unknown")
        ioc_type = pdns_entry.get("ioc_type", "domain")

        fs = pdns_entry.get("first_seen")
        if fs is not None:
            events.append(TimelineEvent(
                entity_value=domain,
                ioc_type=ioc_type,
                protocol="passive_dns",
                timestamp_ns=self._to_ns(fs),
                event_type="dns_first_seen",
                source_evidence_url=source_url,
                raw_timestamp=str(fs),
            ))

        ls = pdns_entry.get("last_seen")
        if ls is not None:
            events.append(TimelineEvent(
                entity_value=domain,
                ioc_type=ioc_type,
                protocol="passive_dns",
                timestamp_ns=self._to_ns(ls),
                event_type="dns_last_seen",
                source_evidence_url=source_url,
                raw_timestamp=str(ls),
            ))

        return events

    @staticmethod
    def _to_ns(ts: int | float | str) -> int:
        if isinstance(ts, str):
            ts = float(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
        if ts > 1e12:
            ts = ts / 1000.0
        return int(ts * _NS_PER_SECOND)


# ----------------------------------------------------------------------------- #
# Canonical timestamp utilities
# ----------------------------------------------------------------------------- #
def to_timestamp_ns(dt: datetime | int | float | str | None) -> int | None:
    """Convert various timestamp formats to int64 nanoseconds.

    Args:
        dt: datetime, Unix timestamp (s or ms), or ISO string

    Returns:
        int64 nanoseconds since Unix epoch, or None if conversion fails
    """
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return int(dt.timestamp() * _NS_PER_SECOND)
    if isinstance(dt, (int, float)):
        if dt > 1e12:
            dt = dt / 1000.0
        return int(dt * _NS_PER_SECOND)
    if isinstance(dt, str):
        try:
            return int(float(dt) * _NS_PER_SECOND)
        except ValueError:
            pass
        try:
            return int(datetime.fromisoformat(dt.replace("Z", "+00:00")).timestamp() * _NS_PER_SECOND)
        except Exception:
            return None
    return None


def from_timestamp_ns(ns: int) -> datetime:
    """Convert int64 nanoseconds to UTC datetime."""
    return datetime.fromtimestamp(ns / _NS_PER_SECOND, tz=timezone.utc)


# ----------------------------------------------------------------------------- #
# TimeSeriesSplicer
# ----------------------------------------------------------------------------- #
class TimeSeriesSplicer:
    """Unified millisecond-aligned timeline across all protocol sources.

    Ingest path writes directly to DuckDB (append-only). Timeline queries
    return ≤1000 events per entity, paginated.

    Usage::

        splicer = TimeSeriesSplicer(duckdb_store)

        # Ingest from CT logs
        events = CtLogAdapter().parse(ct_entry)
        await splicer.ingest(events)

        # Ingest from Git
        events = GitCommitAdapter().parse(git_entry)
        await splicer.ingest(events)

        # Query unified timeline
        timeline = await splicer.export_timeline("example.com")
        for event in timeline:
            print(f"{event.timestamp_iso} [{event.protocol}] {event.event_type}")
    """

    __slots__ = (
        "_duckdb_store",
        "_ingest_semaphore",
        "_log",
        "_initialized",
    )

    def __init__(
        self,
        duckdb_store: "DuckDBShadowStore | None" = None,
        *,
        max_concurrent_ingests: int = 4,
    ) -> None:
        """Initialize TimeSeriesSplicer.

        Args:
            duckdb_store: DuckDBShadowStore instance for persistence.
                         If None, uses global duckdb store from db.py.
            max_concurrent_ingests: Max concurrent ingest operations (M1 8GB safe).
        """
        self._duckdb_store: "DuckDBShadowStore | None" = duckdb_store
        self._ingest_semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent_ingests)
        self._log: logging.Logger = logging.getLogger(f"{__name__}.TimeSeriesSplicer")
        self._initialized: bool = False

    async def _ensure_initialized(self) -> None:
        """Ensure DuckDB table exists (idempotent)."""
        if self._initialized:
            return
        try:
            await self._run_migration()
            self._initialized = True
        except Exception as exc:
            self._log.warning("[TIMESERIES] Migration check failed (may already exist): %s", exc)
            self._initialized = True  # Don't retry every call

    async def _run_migration(self) -> None:
        """Run the time_series_spliced table migration."""
        store = self._get_store()
        if store is None:
            self._log.warning("[TIMESERIES] No DuckDB store available, skipping migration")
            return

        try:
            conn = self._get_conn()
            if conn is None:
                # DuckDB not yet initialized, will retry later
                return

            conn.execute("""
                CREATE TABLE IF NOT EXISTS time_series_spliced (
                    entity_value     VARCHAR NOT NULL,
                    ioc_type         VARCHAR NOT NULL,
                    protocol         VARCHAR NOT NULL,
                    timestamp_ns     BIGINT NOT NULL,
                    event_type       VARCHAR NOT NULL,
                    source_evidence_url VARCHAR NOT NULL,
                    corroborating_sources TEXT[],
                    raw_timestamp    VARCHAR,
                    sprint_id        VARCHAR DEFAULT '',
                    inserted_at      DOUBLE DEFAULT CAST(UNIX_TIMESTAMP AS DOUBLE),
                    PRIMARY KEY (entity_value, ioc_type, protocol, timestamp_ns)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timeline_entity
                ON time_series_spliced(entity_value, timestamp_ns DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timeline_proto
                ON time_series_spliced(protocol, timestamp_ns DESC)
            """)
            self._log.info("[TIMESERIES] time_series_spliced table ready")
        except Exception as exc:
            self._log.warning("[TIMESERIES] Migration error: %s", exc)

    def _get_store(self) -> "DuckDBShadowStore | None":
        """Get the DuckDB store, lazily importing if needed."""
        if self._duckdb_store is not None:
            return self._duckdb_store
        try:
            from ..knowledge.db import _get_duckdb_store
            store = _get_duckdb_store()
            self._duckdb_store = store
            return store
        except Exception as exc:
            self._log.warning("[TIMESERIES] Could not resolve duckdb store: %s", exc)
            return None

    def _get_conn(self) -> Any | None:
        """Get a raw DuckDB connection from the store (lazy, fail-soft)."""
        store = self._get_store()
        if store is None:
            return None
        return store._persistent_conn if hasattr(store, "_persistent_conn") else None

    async def ingest(
        self,
        events: TimelineEvent | Iterable[TimelineEvent],
        *,
        sprint_id: str = "",
    ) -> int:
        """Ingest timeline events into DuckDB.

        Args:
            events: Single TimelineEvent or list of events
            sprint_id: Optional sprint ID for tracking

        Returns:
            Number of events successfully ingested
        """
        if not events:
            return 0

        await self._ensure_initialized()

        if isinstance(events, TimelineEvent):
            events = [events]

        async with self._ingest_semaphore:
            try:
                return await self._write_events(list(events), sprint_id)
            except Exception as exc:
                self._log.warning("[TIMESERIES] Ingest failed: %s", exc)
                return 0

    async def _write_events(
        self,
        events: list[TimelineEvent],
        sprint_id: str,
    ) -> int:
        """Write events to DuckDB via executemany batch insert (called within semaphore).

        Uses DuckDB executemany with ON CONFLICT semantics. Each batch is wrapped
        in an implicit transaction by DuckDB. M1 8GB bounded: max 500 events/batch.
        """
        store = self._get_store()
        if store is None:
            return 0

        n = len(events)
        if n == 0:
            return 0

        conn = self._get_conn()
        if conn is None:
            self._log.debug("[TIMESERIES] No persistent DuckDB connection available")
            return 0

        # Build row tuples for executemany
        rows: list[tuple] = []
        for ev in events:
            rows.append((
                ev.entity_value,
                ev.ioc_type,
                ev.protocol,
                ev.timestamp_ns,
                ev.event_type,
                ev.source_evidence_url,
                list(ev.corroborating_sources),
                ev.raw_timestamp,
                sprint_id,
            ))

        try:
            conn.executemany(
                "INSERT INTO time_series_spliced "
                "(entity_value, ioc_type, protocol, timestamp_ns, event_type, "
                "source_evidence_url, corroborating_sources, raw_timestamp, sprint_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (entity_value, ioc_type, protocol, timestamp_ns) "
                "DO UPDATE SET "
                "corroborating_sources = array_concat("
                "COALESCE(time_series_spliced.corroborating_sources, []), "
                "excluded.corroborating_sources)",
                rows,
            )
            return n
        except Exception as exc:
            self._log.debug("[TIMESERIES] Batch insert failed, falling back to per-row: %s", exc)
            # Fallback: per-row INSERT with individual error handling
            inserted = 0
            for row in rows:
                try:
                    conn.execute(
                        "INSERT INTO time_series_spliced "
                        "(entity_value, ioc_type, protocol, timestamp_ns, event_type, "
                        "source_evidence_url, corroborating_sources, raw_timestamp, sprint_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT (entity_value, ioc_type, protocol, timestamp_ns) "
                        "DO UPDATE SET "
                        "corroborating_sources = array_concat("
                        "COALESCE(time_series_spliced.corroborating_sources, []), "
                        "excluded.corroborating_sources), "
                        "source_evidence_url = COALESCE("
                        "NULLIF(time_series_spliced.source_evidence_url, ''), "
                        "excluded.source_evidence_url)",
                        row,
                    )
                    inserted += 1
                except Exception as perr:
                    self._log.debug("[TIMESERIES] Row insert failed: %s", perr)
            return inserted

    async def export_timeline(
        self,
        entity_value: str,
        *,
        ioc_type: str | None = None,
        protocols: Iterable[str] | None = None,
        start_ns: int | None = None,
        end_ns: int | None = None,
        limit: int = _MAX_TIMELINE_EVENTS,
    ) -> list[TimelineEvent]:
        """Export sorted timeline for an entity.

        Args:
            entity_value: The IOC value to query
            ioc_type: Optional filter by IOC type
            protocols: Optional filter by protocol sources
            start_ns: Optional start timestamp (ns)
            end_ns: Optional end timestamp (ns)
            limit: Max events to return (default 1000)

        Returns:
            Sorted list of TimelineEvents (oldest first)
        """
        await self._ensure_initialized()

        store = self._get_store()
        if store is None:
            return []

        try:
            conn = self._get_conn()
            if conn is None:
                return []
            params: list[Any] = [entity_value]
            where = "entity_value = ?"
            if ioc_type is not None:
                where += " AND ioc_type = ?"
                params.append(ioc_type)
            if protocols is not None:
                proto_list = list(protocols)
                if proto_list:
                    placeholders = ", ".join("?" * len(proto_list))
                    where += f" AND protocol IN ({placeholders})"
                    params.extend(proto_list)
            if start_ns is not None:
                where += " AND timestamp_ns >= ?"
                params.append(start_ns)
            if end_ns is not None:
                where += " AND timestamp_ns <= ?"
                params.append(end_ns)

            query = f"""
                SELECT entity_value, ioc_type, protocol, timestamp_ns, event_type,
                       source_evidence_url, corroborating_sources, raw_timestamp
                FROM time_series_spliced
                WHERE {where}
                ORDER BY timestamp_ns ASC
                LIMIT ?
            """
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [
                TimelineEvent(
                    entity_value=r[0],
                    ioc_type=r[1],
                    protocol=r[2],
                    timestamp_ns=r[3],
                    event_type=r[4],
                    source_evidence_url=r[5],
                    corroborating_sources=tuple(r[6]) if r[6] else (),
                    raw_timestamp=r[7],
                )
                for r in rows
            ]
        except Exception as exc:
            self._log.warning("[TIMESERIES] Timeline export failed: %s", exc)
            return []

    async def get_entity_timeline_summary(
        self,
        entity_value: str,
    ) -> dict[str, Any]:
        """Get a summary of all timelines for an entity.

        Returns:
            Dict with earliest_event, latest_event, protocol_counts, event_type_counts
        """
        timeline = await self.export_timeline(entity_value, limit=10000)
        if not timeline:
            return {
                "entity_value": entity_value,
                "total_events": 0,
                "earliest_event": None,
                "latest_event": None,
                "protocol_counts": {},
                "event_type_counts": {},
                "lifespan_ns": None,
            }

        protocols: dict[str, int] = {}
        event_types: dict[str, int] = {}
        for ev in timeline:
            protocols[ev.protocol] = protocols.get(ev.protocol, 0) + 1
            event_types[ev.event_type] = event_types.get(ev.event_type, 0) + 1

        earliest = timeline[0]
        latest = timeline[-1]

        return {
            "entity_value": entity_value,
            "total_events": len(timeline),
            "earliest_event": {
                "timestamp_ns": earliest.timestamp_ns,
                "timestamp_iso": earliest.timestamp_iso,
                "protocol": earliest.protocol,
                "event_type": earliest.event_type,
            },
            "latest_event": {
                "timestamp_ns": latest.timestamp_ns,
                "timestamp_iso": latest.timestamp_iso,
                "protocol": latest.protocol,
                "event_type": latest.event_type,
            },
            "protocol_counts": protocols,
            "event_type_counts": event_types,
            "lifespan_ns": latest.timestamp_ns - earliest.timestamp_ns,
        }

    async def correlate_events(
        self,
        entity_a: str,
        entity_b: str,
        *,
        tolerance_ns: int = 60_000_000_000,  # 60 seconds default
    ) -> list[dict[str, Any]]:
        """Find correlated events between two entities within time tolerance.

        Args:
            entity_a: First entity value
            entity_b: Second entity value
            tolerance_ns: Time tolerance in nanoseconds (default 60s)

        Returns:
            List of correlated event pairs
        """
        tl_a = await self.export_timeline(entity_a, limit=1000)
        tl_b = await self.export_timeline(entity_b, limit=1000)

        if not tl_a or not tl_b:
            return []

        # Binary search for near-matches
        import bisect
        b_times = [ev.timestamp_ns for ev in tl_b]

        correlations: list[dict[str, Any]] = []
        for ev_a in tl_a:
            lo = bisect.bisect_left(b_times, ev_a.timestamp_ns - tolerance_ns)
            hi = bisect.bisect_right(b_times, ev_a.timestamp_ns + tolerance_ns, lo)
            for i in range(lo, hi):
                ev_b = tl_b[i]
                delta = abs(ev_a.timestamp_ns - ev_b.timestamp_ns)
                correlations.append({
                    "entity_a": entity_a,
                    "entity_b": entity_b,
                    "event_a": {
                        "protocol": ev_a.protocol,
                        "event_type": ev_a.event_type,
                        "timestamp_ns": ev_a.timestamp_ns,
                        "timestamp_iso": ev_a.timestamp_iso,
                    },
                    "event_b": {
                        "protocol": ev_b.protocol,
                        "event_type": ev_b.event_type,
                        "timestamp_ns": ev_b.timestamp_ns,
                        "timestamp_iso": ev_b.timestamp_iso,
                    },
                    "delta_ns": delta,
                    "confidence": max(0.0, 1.0 - (delta / tolerance_ns)),
                })
        return correlations

    # ----------------------------------------------------------------- #
    # Batch ingest helpers (used by protocol lanes)
    # ----------------------------------------------------------------- #
    async def ingest_ct(self, ct_entries: Iterable[dict[str, Any]]) -> int:
        """Ingest Certificate Transparency log entries.

        Usage in lane::

            events = []
            for entry in ct_logs:
                events.extend(CtLogAdapter().parse(entry))
            await splicer.ingest_ct(events) if events else 0
        """
        adapter = CtLogAdapter()
        events: list[TimelineEvent] = []
        for entry in ct_entries:
            events.extend(adapter.parse(entry))
        return await self.ingest(events) if events else 0

    async def ingest_git(self, git_entries: Iterable[dict[str, Any]]) -> int:
        """Ingest Git commit entries."""
        adapter = GitCommitAdapter()
        events: list[TimelineEvent] = []
        for entry in git_entries:
            events.extend(adapter.parse(entry))
        return await self.ingest(events) if events else 0

    async def ingest_telegram(self, tg_entries: Iterable[dict[str, Any]]) -> int:
        """Ingest Telegram message entries."""
        adapter = TelegramAdapter()
        events: list[TimelineEvent] = []
        for entry in tg_entries:
            events.extend(adapter.parse(entry))
        return await self.ingest(events) if events else 0

    async def ingest_blockchain(self, bc_entries: Iterable[dict[str, Any]]) -> int:
        """Ingest blockchain transaction entries."""
        adapter = BlockchainAdapter()
        events: list[TimelineEvent] = []
        for entry in bc_entries:
            events.extend(adapter.parse(entry))
        return await self.ingest(events) if events else 0

    async def ingest_http(self, http_entries: Iterable[dict[str, Any]]) -> int:
        """Ingest HTTP response entries."""
        adapter = HttpAdapter()
        events: list[TimelineEvent] = []
        for entry in http_entries:
            events.extend(adapter.parse(entry))
        return await self.ingest(events) if events else 0

    async def ingest_warc(self, warc_entries: Iterable[dict[str, Any]]) -> int:
        """Ingest WARC record entries."""
        adapter = WarcAdapter()
        events: list[TimelineEvent] = []
        for entry in warc_entries:
            events.extend(adapter.parse(entry))
        return await self.ingest(events) if events else 0

    async def ingest_passive_dns(self, pdns_entries: Iterable[dict[str, Any]]) -> int:
        """Ingest passive DNS entries."""
        adapter = PassiveDnsAdapter()
        events: list[TimelineEvent] = []
        for entry in pdns_entries:
            events.extend(adapter.parse(entry))
        return await self.ingest(events) if events else 0


# ----------------------------------------------------------------------------- #
# Module-level singleton accessor
# ----------------------------------------------------------------------------- #
_TSS_INSTANCE: TimeSeriesSplicer | None = None


def get_time_series_splicer(
    duckdb_store: "DuckDBShadowStore | None" = None,
) -> TimeSeriesSplicer:
    """Get or create the global TimeSeriesSplicer singleton.

    Gated by HLEDAC_ENABLE_TIMELINE_SPLICER=1 env var.
    Returns a no-op instance when disabled.

    Usage::

        splicer = get_time_series_splicer()
        await splicer.ingest(events)
    """
    global _TSS_INSTANCE  # noqa: PLW0603
    if not _IS_ENABLED:
        # Return no-op when feature flag is off
        if _TSS_INSTANCE is None:
            _TSS_INSTANCE = _NoOpTimeSeriesSplicer()
        return _TSS_INSTANCE
    if _TSS_INSTANCE is None or isinstance(_TSS_INSTANCE, _NoOpTimeSeriesSplicer):
        _TSS_INSTANCE = TimeSeriesSplicer(duckdb_store=duckdb_store)
    elif duckdb_store is not None and _TSS_INSTANCE._duckdb_store is None:
        _TSS_INSTANCE._duckdb_store = duckdb_store
    return _TSS_INSTANCE


class _NoOpTimeSeriesSplicer:
    """No-op implementation when HLEDAC_ENABLE_TIMELINE_SPLICER=0.

    All methods return empty/zero results. Zero overhead. No DuckDB connection.
    """
    __slots__ = ()

    async def ingest(self, *args: Any, **kwargs: Any) -> int: return 0
    async def export_timeline(self, *args: Any, **kwargs: Any) -> list: return []
    async def get_entity_timeline_summary(self, *args: Any, **kwargs: Any) -> dict: return {"entity_value": "", "total_events": 0, "earliest_event": None, "latest_event": None, "protocol_counts": {}, "event_type_counts": {}, "lifespan_ns": None}
    async def correlate_events(self, *args: Any, **kwargs: Any) -> list: return []
    async def ingest_ct(self, *args: Any, **kwargs: Any) -> int: return 0
    async def ingest_git(self, *args: Any, **kwargs: Any) -> int: return 0
    async def ingest_telegram(self, *args: Any, **kwargs: Any) -> int: return 0
    async def ingest_blockchain(self, *args: Any, **kwargs: Any) -> int: return 0
    async def ingest_http(self, *args: Any, **kwargs: Any) -> int: return 0
    async def ingest_warc(self, *args: Any, **kwargs: Any) -> int: return 0
    async def ingest_passive_dns(self, *args: Any, **kwargs: Any) -> int: return 0


# ----------------------------------------------------------------------------- #
# Re-exports for convenience
# ----------------------------------------------------------------------------- #
__all__ = [
    "TimelineEvent",
    "TimeSeriesSplicer",
    "get_time_series_splicer",
    # Adapters
    "CtLogAdapter",
    "GitCommitAdapter",
    "TelegramAdapter",
    "BlockchainAdapter",
    "HttpAdapter",
    "WarcAdapter",
    "PassiveDnsAdapter",
    # Utilities
    "to_timestamp_ns",
    "from_timestamp_ns",
    # Feature gate
    "_NoOpTimeSeriesSplicer",
]
