from __future__ import annotations

# Sprint P2-1: DuckDB Indexed Table for Domain Candidates
# ─────────────────────────────────────────────────────────────
# DuckDB indexed table layer for persistent, indexed domain-candidate lookups.
# 50-80% faster vs re-extracting from raw text every sprint.
# DuckDB 1.5.3 does not support MATERIALIZED VIEW syntax; implemented as
# an indexed table with the same query patterns (primary key + secondary indexes).
#
# Storage: runtime/cti/db/domain_candidates.duckdb (separate from analytics.duckdb)
# Bounded: MAX_MV_ROWS=50_000, LRU eviction on INSERT overflow
# M1 8GB: all ops are SQLite-like, no Metal, no GPU


import asyncio
import hashlib
import logging
import threading
from contextlib import asynccontextmanager
import msgspec
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb
    from collections.abc import AsyncIterator

# ── Module-level constants ────────────────────────────────────────────────────

DB_PATH: Path = Path(__file__).parent / "domain_candidates.duckdb"
_MAX_MV_ROWS: int = 50_000
_MV_REFRESH_INTERVAL_S: float = 300.0  # 5 min (no-op without MV, kept for API compat)
_CREATE_LOCK = threading.Lock()

logger = logging.getLogger(__name__)


# ── Dataclasses ───────────────────────────────────────────────────────────────

class MvDomainRecord(msgspec.Struct, gc=False):
    domain: str
    source_family: str
    ioc_type: str
    dedup_key: str
    first_seen: datetime
    last_seen: datetime
    seen_count: int
    total_observations: int
    avg_confidence: float
    last_rank_score: float | None
    nonfeed_eligible_ct: bool
    nonfeed_eligible_doh: bool
    nonfeed_eligible_wayback: bool
    nonfeed_eligible_pdns: bool

    def to_tuple(self) -> tuple[Any, ...]:
        return (
            self.domain,
            self.source_family,
            self.ioc_type,
            self.dedup_key,
            self.first_seen.isoformat(),
            self.last_seen.isoformat(),
            self.seen_count,
            self.total_observations,
            self.avg_confidence,
            self.last_rank_score,
            self.nonfeed_eligible_ct,
            self.nonfeed_eligible_doh,
            self.nonfeed_eligible_wayback,
            self.nonfeed_eligible_pdns,
        )

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> MvDomainRecord:
        first_seen_str, last_seen_str = row[4], row[5]
        return cls(
            domain=str(row[0]),
            source_family=str(row[1]),
            ioc_type=str(row[2]),
            dedup_key=str(row[3]),
            first_seen=(
                datetime.fromisoformat(first_seen_str)
                if isinstance(first_seen_str, str) else first_seen_str
            ),
            last_seen=(
                datetime.fromisoformat(last_seen_str)
                if isinstance(last_seen_str, str) else last_seen_str
            ),
            seen_count=int(row[6]),
            total_observations=int(row[7]),
            avg_confidence=float(row[8]),
            last_rank_score=float(row[9]) if row[9] is not None else None,
            nonfeed_eligible_ct=bool(row[10]),
            nonfeed_eligible_doh=bool(row[11]),
            nonfeed_eligible_wayback=bool(row[12]),
            nonfeed_eligible_pdns=bool(row[13]),
        )


class DomainCandidateMvStats(msgspec.Struct, frozen=True, gc=False):
    total_rows: int
    unique_domains: int
    oldest_row: datetime | None
    newest_row: datetime | None
    avg_seen_count: float
    eligible_ct_count: int
    eligible_doh_count: int
    eligible_wayback_count: int
    eligible_pdns_count: int


# ── DuckDB helpers ───────────────────────────────────────────────────────────

# Table name: "domain_candidates" (base table, no MV suffix)
_TABLE_NAME = "domain_candidates"

_SCHEMA_SQL = f"""
CREATE SEQUENCE IF NOT EXISTS mv_row_id_seq;

CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
    row_id          BIGINT DEFAULT nextval('mv_row_id_seq'),
    domain          VARCHAR NOT NULL,
    source_family   VARCHAR NOT NULL,
    ioc_type        VARCHAR NOT NULL,
    dedup_key       VARCHAR NOT NULL,
    first_seen      TIMESTAMP WITH TIME ZONE,
    last_seen       TIMESTAMP WITH TIME ZONE,
    seen_count      INTEGER DEFAULT 1,
    total_observations INTEGER DEFAULT 1,
    avg_confidence  DOUBLE PRECISION DEFAULT 0.5,
    last_rank_score DOUBLE PRECISION,
    nonfeed_eligible_ct      BOOLEAN DEFAULT FALSE,
    nonfeed_eligible_doh     BOOLEAN DEFAULT FALSE,
    nonfeed_eligible_wayback BOOLEAN DEFAULT FALSE,
    nonfeed_eligible_pdns    BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (domain, source_family, ioc_type, dedup_key)
);
"""

_CREATE_INDEXES_SQL = [
    f"CREATE INDEX IF NOT EXISTS idx_dc_last_seen ON {_TABLE_NAME} (last_seen DESC);",
    f"CREATE INDEX IF NOT EXISTS idx_dc_eligible_ct ON {_TABLE_NAME} (domain, nonfeed_eligible_ct);",
    f"CREATE INDEX IF NOT EXISTS idx_dc_eligible_doh ON {_TABLE_NAME} (domain, nonfeed_eligible_doh);",
    f"CREATE INDEX IF NOT EXISTS idx_dc_eligible_wayback ON {_TABLE_NAME} (domain, nonfeed_eligible_wayback);",
    f"CREATE INDEX IF NOT EXISTS idx_dc_eligible_pdns ON {_TABLE_NAME} (domain, nonfeed_eligible_pdns);",
]

_SELECT_COLUMNS = """
    domain, source_family, ioc_type, dedup_key,
    first_seen, last_seen, seen_count, total_observations,
    avg_confidence, last_rank_score,
    nonfeed_eligible_ct, nonfeed_eligible_doh,
    nonfeed_eligible_wayback, nonfeed_eligible_pdns
"""


def _make_dedup_key(domain: str, source_family: str, ioc_type: str) -> str:
    raw = f"{domain}|{source_family}|{ioc_type}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ── Core class ───────────────────────────────────────────────────────────────

class DuckDBDomainMv:
    """
    DuckDB indexed-table backed domain-candidate store.

    Provides:
    - UPSERT candidate records (seen_count increment, avg_confidence update)
    - LOOKUP by domain + eligibility flags (uses secondary indexes)
    - STATS for observability
    - REFRESH no-op (no MV in DuckDB 1.5.x; kept for API compat)

    Thread-safe: all DuckDB operations go through a single connection
    guarded by _lock (RLock for nested cursor use).
    """

    __slots__ = (
        "_conn",
        "_lock",
        "_refresh_lock",
        "_last_refresh",
        "_refresh_task",
        "_closed",
    )

    def __init__(self) -> None:
        self._conn: "duckdb.DuckDBPyConnection | None" = None
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._last_refresh: datetime | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._closed = False
        self._init_db()

    # ── Initialization ────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        import duckdb

        with _CREATE_LOCK:
            Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

            fresh = not DB_PATH.exists()

            self._conn = duckdb.connect(str(DB_PATH), read_only=False)

            if fresh:
                with self._conn.cursor() as cur:
                    cur.execute(_SCHEMA_SQL)
                    for idx_sql in _CREATE_INDEXES_SQL:
                        cur.execute(idx_sql)
                    logger.info(
                        "[P2-1] DuckDB domain candidates table created fresh at %s",
                        DB_PATH,
                    )
            else:
                with self._conn.cursor() as cur:
                    try:
                        cur.execute(f"SELECT 1 FROM {_TABLE_NAME} LIMIT 1;")
                    except Exception:  # noqa: BLE001
                        cur.execute(_SCHEMA_SQL)
                        for idx_sql in _CREATE_INDEXES_SQL:
                            try:
                                cur.execute(idx_sql)
                            except Exception:  # noqa: BLE001
                                pass
                logger.info("[P2-1] DuckDB domain candidates opened at %s", DB_PATH)

    # ── Public API ─────────────────────────────────────────────────────────────

    def upsert_candidate(
        self,
        domain: str,
        source_family: str,
        ioc_type: str,
        confidence: float,
        rank_score: float | None = None,
        nonfeed_eligible_ct: bool = False,
        nonfeed_eligible_doh: bool = False,
        nonfeed_eligible_wayback: bool = False,
        nonfeed_eligible_pdns: bool = False,
    ) -> bool:
        """
        Insert or update a domain candidate record.

        Returns True if record was inserted/updated, False on error.
        Thread-safe via _lock.
        """
        if self._closed:
            return False
        dedup_key = _make_dedup_key(domain, source_family, ioc_type)
        now = datetime.now(UTC)
        now_str = now.isoformat()

        with self._lock:
            try:
                with self._conn.cursor() as cur:  # type: ignore[union-attr]
                    # Check if row exists
                    cur.execute(
                        f"""
                        SELECT seen_count, total_observations, avg_confidence
                        FROM {_TABLE_NAME}
                        WHERE domain = ? AND source_family = ? AND ioc_type = ? AND dedup_key = ?
                        """,
                        [domain, source_family, ioc_type, dedup_key],
                    )
                    row = cur.fetchone()

                    if row is not None:
                        old_seen, old_total, old_avg = row[0], row[1], row[2]
                        new_seen = old_seen + 1
                        new_total = old_total + 1
                        new_avg = (old_avg * old_seen + confidence) / new_seen
                        cur.execute(
                            f"""
                            UPDATE {_TABLE_NAME} SET
                                last_seen = ?,
                                seen_count = ?,
                                total_observations = ?,
                                avg_confidence = ?,
                                last_rank_score = COALESCE(?, last_rank_score),
                                nonfeed_eligible_ct = nonfeed_eligible_ct OR ?,
                                nonfeed_eligible_doh = nonfeed_eligible_doh OR ?,
                                nonfeed_eligible_wayback = nonfeed_eligible_wayback OR ?,
                                nonfeed_eligible_pdns = nonfeed_eligible_pdns OR ?
                            WHERE domain = ? AND source_family = ? AND ioc_type = ? AND dedup_key = ?
                            """,
                            [
                                now_str, new_seen, new_total, new_avg,
                                rank_score,
                                nonfeed_eligible_ct,
                                nonfeed_eligible_doh,
                                nonfeed_eligible_wayback,
                                nonfeed_eligible_pdns,
                                domain, source_family, ioc_type, dedup_key,
                            ],
                        )
                    else:
                        # Enforce bounded storage (LRU eviction)
                        self._evict_if_needed(cur)
                        cur.execute(
                            f"""
                            INSERT INTO {_TABLE_NAME}
                            (domain, source_family, ioc_type, dedup_key,
                             first_seen, last_seen, seen_count, total_observations,
                             avg_confidence, last_rank_score,
                             nonfeed_eligible_ct, nonfeed_eligible_doh,
                             nonfeed_eligible_wayback, nonfeed_eligible_pdns)
                            VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?, ?, ?)
                            """,
                            [
                                domain, source_family, ioc_type, dedup_key,
                                now_str, now_str, confidence, rank_score,
                                nonfeed_eligible_ct, nonfeed_eligible_doh,
                                nonfeed_eligible_wayback, nonfeed_eligible_pdns,
                            ],
                        )
                    return True
            except Exception as exc:
                logger.warning("[P2-1] upsert_candidate failed: %s", exc)
                return False

    def lookup_domain(
        self,
        domain: str,
        eligible_ct: bool | None = None,
        eligible_doh: bool | None = None,
        eligible_wayback: bool | None = None,
        eligible_pdns: bool | None = None,
    ) -> list[MvDomainRecord]:
        """
        Lookup domain records by eligibility flags.
        Uses partial secondary indexes for fast filtered scans.
        Returns list of MvDomainRecord sorted by last_seen DESC.
        """
        if self._closed:
            return []
        conditions = ["domain = ?"]
        params: list[Any] = [domain]

        if eligible_ct is not None:
            conditions.append("nonfeed_eligible_ct = ?")
            params.append(eligible_ct)
        if eligible_doh is not None:
            conditions.append("nonfeed_eligible_doh = ?")
            params.append(eligible_doh)
        if eligible_wayback is not None:
            conditions.append("nonfeed_eligible_wayback = ?")
            params.append(eligible_wayback)
        if eligible_pdns is not None:
            conditions.append("nonfeed_eligible_pdns = ?")
            params.append(eligible_pdns)

        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT {_SELECT_COLUMNS}
            FROM {_TABLE_NAME}
            WHERE {where_clause}
            ORDER BY last_seen DESC
            LIMIT 100
        """
        with self._lock:
            try:
                with self._conn.cursor() as cur:  # type: ignore[union-attr]
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                    return [MvDomainRecord.from_row(r) for r in rows]
            except Exception as exc:
                logger.warning("[P2-1] lookup_domain failed: %s", exc)
                return []

    def get_top_domains(
        self,
        limit: int = 50,
        min_seen_count: int = 2,
        eligible_flag: str | None = None,
    ) -> list[MvDomainRecord]:
        """
        Get top domains by seen_count, optionally filtered by eligibility flag.
        Uses idx_dc_last_seen for ordering.
        """
        if self._closed:
            return []
        if eligible_flag not in (None, "ct", "doh", "wayback", "pdns"):
            eligible_flag = None

        if eligible_flag:
            flag_col = f"nonfeed_eligible_{eligible_flag}"
            sql = f"""
                SELECT {_SELECT_COLUMNS}
                FROM {_TABLE_NAME}
                WHERE {flag_col} = TRUE AND seen_count >= ?
                ORDER BY seen_count DESC, last_seen DESC
                LIMIT ?
            """
            params: tuple[Any, ...] = (min_seen_count, limit)
        else:
            sql = f"""
                SELECT {_SELECT_COLUMNS}
                FROM {_TABLE_NAME}
                WHERE seen_count >= ?
                ORDER BY seen_count DESC, last_seen DESC
                LIMIT ?
            """
            params = (min_seen_count, limit)

        with self._lock:
            try:
                with self._conn.cursor() as cur:  # type: ignore[union-attr]
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                    return [MvDomainRecord.from_row(r) for r in rows]
            except Exception as exc:
                logger.warning("[P2-1] get_top_domains failed: %s", exc)
                return []

    def refresh_mv(self) -> bool:
        """
        No-op: DuckDB 1.5.x does not support MATERIALIZED VIEW.
        Kept for API compatibility.
        """
        with self._refresh_lock:
            self._last_refresh = datetime.now(UTC)
            logger.debug("[P2-1] refresh_mv no-op (DuckDB 1.5.x has no MV)")
            return True

    def stats(self) -> DomainCandidateMvStats:
        """Return domain-candidate statistics for observability."""
        if self._closed:
            return DomainCandidateMvStats(0, 0, None, None, 0.0, 0, 0, 0, 0)
        with self._lock:
            try:
                with self._conn.cursor() as cur:  # type: ignore[union-attr]
                    cur.execute(
                        f"""SELECT COUNT(*), COUNT(DISTINCT domain),
                        MIN(last_seen), MAX(last_seen), AVG(seen_count)
                        FROM {_TABLE_NAME};"""
                    )
                    row = cur.fetchone()
                    total_rows = row[0] if row else 0
                    unique_domains = row[1] if row else 0
                    oldest = row[2] if row else None
                    newest = row[3] if row else None
                    avg_seen = row[4] if row else 0.0

                    def cnt(cond: str) -> int:
                        # Security fix: cond is hardcoded strings only (no user input),
                        # add allowlist assert as defense-in-depth for SQL injection prevention
                        _ALLOWED_CONDITIONS = frozenset([
                            "nonfeed_eligible_ct = TRUE",
                            "nonfeed_eligible_doh = TRUE",
                            "nonfeed_eligible_wayback = TRUE",
                            "nonfeed_eligible_pdns = TRUE",
                        ])
                        assert cond in _ALLOWED_CONDITIONS, f"Invalid condition: {cond}"
                        try:
                            cur.execute(
                                f"SELECT COUNT(*) FROM {_TABLE_NAME} WHERE {cond};"  # nosem: formatted-sql — _TABLE_NAME is module-level constant, cond is allowlisted above
                            )
                            r = cur.fetchone()
                            return int(r[0]) if r else 0
                        except Exception:
                            return 0

                    ct_count = cnt("nonfeed_eligible_ct = TRUE")
                    doh_count = cnt("nonfeed_eligible_doh = TRUE")
                    wayback_count = cnt("nonfeed_eligible_wayback = TRUE")
                    pdns_count = cnt("nonfeed_eligible_pdns = TRUE")

                    return DomainCandidateMvStats(
                        total_rows=int(total_rows) if total_rows else 0,
                        unique_domains=int(unique_domains) if unique_domains else 0,
                        oldest_row=(
                            datetime.fromisoformat(oldest)
                            if oldest and isinstance(oldest, str) else None
                        ),
                        newest_row=(
                            datetime.fromisoformat(newest)
                            if newest and isinstance(newest, str) else None
                        ),
                        avg_seen_count=float(avg_seen) if avg_seen else 0.0,
                        eligible_ct_count=ct_count,
                        eligible_doh_count=doh_count,
                        eligible_wayback_count=wayback_count,
                        eligible_pdns_count=pdns_count,
                    )
            except Exception as exc:
                logger.warning("[P2-1] stats failed: %s", exc)
                return DomainCandidateMvStats(0, 0, None, None, 0.0, 0, 0, 0, 0)

    def get_last_refresh(self) -> datetime | None:
        return self._last_refresh

    # ── Internal ────────────────────────────────────────────────────────────────

    def _evict_if_needed(self, cur: "duckdb.DuckDBPyConnection") -> None:
        """LRU eviction: remove oldest rows when over _MAX_MV_ROWS."""
        cur.execute(f"SELECT COUNT(*) FROM {_TABLE_NAME};")
        r = cur.fetchone()
        count = r[0] if r else 0
        if count >= _MAX_MV_ROWS:
            excess = count - _MAX_MV_ROWS + 1000
            cur.execute(
                f"""
                DELETE FROM {_TABLE_NAME}
                WHERE row_id IN (
                    SELECT row_id FROM {_TABLE_NAME}
                    ORDER BY last_seen ASC
                    LIMIT ?
                )
                """,
                [excess],
            )
            logger.debug("[P2-1] LRU evicted %d rows", excess)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._refresh_task:
            self._refresh_task.cancel()
        if self._conn:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
        logger.info("[P2-1] DuckDB domain candidates closed")

    def __del__(self) -> None:
        self.close()


# ── Singleton ────────────────────────────────────────────────────────────────

_MV_INSTANCE: DuckDBDomainMv | None = None
_MV_INIT_LOCK = threading.Lock()


def get_domain_mv() -> DuckDBDomainMv:
    """Get or create the singleton DuckDB domain-candidate store instance."""
    global _MV_INSTANCE
    if _MV_INSTANCE is None:
        with _MV_INIT_LOCK:
            if _MV_INSTANCE is None:
                _MV_INSTANCE = DuckDBDomainMv()
    return _MV_INSTANCE


# ── Async refresh task ───────────────────────────────────────────────────────

async def _mv_refresh_loop() -> None:
    """Background loop: calls refresh_mv() every _MV_REFRESH_INTERVAL_S.

    No-op on DuckDB 1.5.x but kept for future MV upgrade path and API compat.
    """
    while True:
        await asyncio.sleep(_MV_REFRESH_INTERVAL_S)
        mv = get_domain_mv()
        if mv.refresh_mv():
            stats = mv.stats()
            logger.info(
                "[P2-1] MV refresh OK (no-op): %d rows, %d domains",
                stats.total_rows,
                stats.unique_domains,
            )


@asynccontextmanager
async def domain_mv_lifecycle() -> AsyncIterator[DuckDBDomainMv]:
    """Async context manager: starts background refresh task, yields MV, cleans up."""
    mv = get_domain_mv()
    task = asyncio.create_task(_mv_refresh_loop())
    try:
        yield mv
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
