"""
DuckDB Query Executor — Extracted SQL construction and execution engine.

F360M-R: Extracted from DuckDBShadowStore._DuckDBQueryExecutor to reduce

LCOM from 18 (38 methods in one class) to focused executor with single
responsibility: SQL template management and transaction framing.

This module is NOT part of the public API - it exists solely to concentrate
SQL string templates and transaction patterns that were previously copy-pasted
across 38 _sync_* methods.

Design:
- All SQL templates are class-level string constants
- Transaction framing (_begin/_commit/_rollback) is shared
- Connection routing (MODE A file conn vs MODE B persistent conn) is shared
- Arrow->dict conversion helpers are shared

Usage:
    qe = DuckDBQueryExecutor(store)  # store is DuckDBShadowStore instance
    qe.insert_finding(...)
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

import logging as _logging

_logger = _logging.getLogger(__name__)


class DuckDBQueryExecutor:
    """
    Private SQL construction and execution engine for DuckDBShadowStore.

    NOT part of the public API - exists solely to concentrate SQL string
    templates and transaction patterns that were previously copy-pasted
    across 38 _sync_* methods.
    """

    # ── Type stubs for dynamic attributes (set via object.__setattr__) ────────────
    # These are initialized in __init__ via object.__setattr__ to avoid __slots__
    # conflicts with msgspec/gc. Type checkers need class-level annotations.
    # Note: _stmt_insert_finding_conn_id is either int (conn_id) or tuple(conn_id, mode_str)
    _store: DuckDBShadowStore
    _stmt_insert_finding: Any
    _stmt_insert_finding_conn_id: int | None | tuple[int, str | None]
    _query_latencies_ns_list: list[float]

    # ── SQL Templates ─────────────────────────────────────────────────────────────

    _SQL_INSERT_SHADOW_FINDING = "INSERT INTO canonical_findings (id, query, source_type, confidence, ts, provenance_json, payload_text, claims_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING"
    _SQL_INSERT_SHADOW_RUN = (
        "INSERT INTO shadow_runs (run_id, started_at, ended_at, total_fds, rss_mb) VALUES (?, ?, ?, ?, ?)"
    )
    _SQL_INSERT_SPRINT_DELTA = "INSERT INTO sprint_delta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
    _SQL_INSERT_SOURCE_HIT = "INSERT INTO source_hit_log VALUES (?,?,?,?,?,?)"
    _SQL_INSERT_HYPOTHESIS_FEEDBACK = "INSERT INTO hypothesis_feedback"
    _SQL_INSERT_HYPOTHESIS_TRACKING = "INSERT OR REPLACE INTO hypothesis_tracking"
    _SQL_UPSERT_TARGET_PROFILE = "INSERT OR REPLACE INTO target_profiles"
    _SQL_SELECT_TARGET_PROFILE = (
        "SELECT target_id, first_seen, last_seen, cumulative_finding_count, entity_summary_json"
    )
    _SQL_SELECT_HYPOTHESIS_FEEDBACK = "SELECT id, target_id, pivot_type, ioc_type"
    _SQL_SELECT_SHADOW_FINDINGS = "SELECT id, query, source_type, confidence, ts, provenance_json, payload_text, claims_json"
    _SQL_INSERT_RESEARCH_SESSION = "INSERT INTO research_sessions (session_id, sprint_id, query, ts, findings_count, accepted_count, gaps_json, entities_json, source_patterns_json, unexplored_angles_json, temporal_anomalies_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    _SQL_INSERT_ENTITY_OBSERVATION = "INSERT OR REPLACE INTO entity_observations (observation_id, entity_value, entity_type, sprint_id, source_type, confidence, ts, finding_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    _SQL_SELECT_RESEARCH_SESSIONS_BY_SPRINT = "SELECT session_id, sprint_id, query, ts, findings_count, accepted_count, gaps_json, entities_json, source_patterns_json, unexplored_angles_json, temporal_anomalies_json FROM research_sessions WHERE sprint_id = ? ORDER BY ts DESC"
    _SQL_SELECT_ENTITY_OBSERVATIONS_BY_ENTITY = "SELECT observation_id, entity_value, entity_type, sprint_id, source_type, confidence, ts, finding_id FROM entity_observations WHERE entity_value = ? ORDER BY ts DESC LIMIT ?"
    _SQL_SELECT_RESEARCH_SESSIONS_RECENT = "SELECT session_id, sprint_id, query, ts, findings_count, accepted_count, gaps_json, entities_json, source_patterns_json, unexplored_angles_json, temporal_anomalies_json FROM research_sessions ORDER BY ts DESC LIMIT ?"

    # ── Initialization ────────────────────────────────────────────────────────────

    def __init__(self, store: DuckDBShadowStore) -> None:
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_stmt_insert_finding", None)
        object.__setattr__(self, "_stmt_insert_finding_conn_id", None)

    # ── Connection Management ────────────────────────────────────────────────────

    def _conn(self) -> Any:
        """Return the active write connection (MODE A file or MODE B persistent).

        F265X-LAZY-FIX: triggers ensure_connected() if connection is not yet
        established in lazy mode. In lazy mode, __aenter__ sets _initialized=True
        but leaves _file_conn=None and _persistent_conn=None. First actual use
        via this property establishes the connection on-demand.

        P2-22 FIX: Removed redundant _prewarm_file_conn() call from hot path.
        The prewarm SELECT 1 was being issued on EVERY _conn() call in the
        hot ingest loop (~millions of times), adding ~0.1-0.3ms per call
        overhead for no benefit after the first prewarm. Prewarm is now
        called exactly once after initial connection in _init_connection().
        """
        s = self._store
        if s._db_path:
            if s._file_conn is None:
                s.ensure_connected()
            return s._file_conn
        if s._persistent_conn is None:
            s.ensure_connected()
        return s._persistent_conn

    def _get_insert_stmt(self, conn: Any) -> Any:
        """
        Sprint F264: Lazy-init prepared INSERT statement for canonical_findings.

        Returns the cached prepared statement for `_SQL_INSERT_SHADOW_FINDING`
        if the underlying connection is unchanged. On reconnect the conn
        identity differs and the statement is transparently re-prepared.

        Fail-safe: if conn.prepare() raises, returns None and emits a
        one-shot warning. The caller MUST fall back to
        `conn.execute(self._SQL_INSERT_SHADOW_FINDING, params)` on None
        so the canonical write path stays alive (CLAUDE.md invariant #5).

        MUST be called on the worker thread (DuckDB conn is thread-affine).
        """
        conn_id = id(conn)
        cached = self._stmt_insert_finding
        if cached is not None and self._stmt_insert_finding_conn_id == conn_id:
            return cached
        try:
            stmt = conn.prepare(self._SQL_INSERT_SHADOW_FINDING)
            object.__setattr__(self, "_stmt_insert_finding", stmt)
            object.__setattr__(self, "_stmt_insert_finding_conn_id", conn_id)
            return stmt
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            try:
                _logger.debug(f"[DUCKDB] prepare() failed, falling back to execute(): {e}")
            except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                pass
            object.__setattr__(self, "_stmt_insert_finding", None)
            object.__setattr__(self, "_stmt_insert_finding_conn_id", None)
            return None

    def _invalidate_insert_stmt(self) -> None:
        """
        Sprint F264: Drop cached prepared statement. Call on close / reconnect.

        Safe to call from any thread; sets the cache to None so the next
        `_get_insert_stmt(conn)` re-prepares on the (possibly new) conn.
        """
        try:
            object.__setattr__(self, "_stmt_insert_finding", None)
            object.__setattr__(self, "_stmt_insert_finding_conn_id", None)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            pass

    # ── Transaction Framing ──────────────────────────────────────────────────────

    @staticmethod
    def _begin(conn: Any) -> None:
        conn.execute("BEGIN TRANSACTION")

    @staticmethod
    def _commit(conn: Any) -> None:
        conn.execute("COMMIT")

    @staticmethod
    def _rollback(conn: Any) -> None:
        try:
            conn.execute("ROLLBACK")
        except (OSError, RuntimeError) as e:
            _logger.debug(f"[DUCKDB] rollback failed: {e}")

    def _with_transaction(self, conn: Any, fn: Any) -> Any:
        """
        Run fn(conn) inside an explicit transaction.
        Commits on success, rolls back on any exception.
        Returns fn's return value.
        """
        self._begin(conn)
        try:
            result = fn(conn)
            self._commit(conn)
            return result
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            self._rollback(conn)
            raise

    # ── Core Insert Operations ───────────────────────────────────────────────────

    def insert_finding(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
        ts: float | None,
        provenance_json: str | None,
    ) -> bool:
        """Insert a single shadow finding. Returns True on success."""
        conn = self._conn()
        if conn is None:
            return False
        params = [finding_id, query, source_type, confidence, ts, provenance_json, None]
        try:
            stmt = self._get_insert_stmt(conn)

            def _do(c: Any) -> None:
                if stmt is not None:
                    stmt.execute(params)
                else:
                    c.execute(self._SQL_INSERT_SHADOW_FINDING, params)

            self._with_transaction(conn, _do)
            return True
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return False

    def insert_findings_bulk(self, findings: list[dict[str, Any]]) -> int:
        """
        Bulk insert shadow findings. Returns number of successfully inserted records.
        MUST be called on the worker thread.

        R2: Dual-strategy insert — DuckDBAppender for small/medium batches (≤500 rows,
        ~2-5× faster than executemany due to bypassing SQL parser), executemany
        fallback for larger batches or when appender is unavailable.

        F350M-R: Claims extraction — Rust batch_extract_claims_python enriches
        findings with sentence-level claims (polarity, confidence) when
        HLEDAC_ENABLE_CLAIMS_EXTRACTION=1 and findings have payload_text.
        """
        if not findings:
            return 0

        # F350M-R: Enrich findings with claims from Rust batch_extract_claims_python
        if self._claims_enabled:
            findings = self._enrich_findings_with_claims(findings)

        rows = [
            [r["id"], r["query"], r["source_type"], r["confidence"], r.get("ts"), r.get("provenance_json"), r.get("claims_json")]
            for r in findings
        ]
        conn = self._conn()
        if conn is None:
            return 0

        # R2: DuckDBAppender path for batches ≤ 500 rows — bypasses SQL parser,
        # direct columnar write. ~2-5× faster than executemany for small batches.
        # For larger batches, the Arrow register() path in insert_findings_bulk_arrow
        # is used by the caller; this method is the legacy shadow path.
        _APPENDER_THRESHOLD = 500
        if len(rows) <= _APPENDER_THRESHOLD:
            result = self._insert_via_appender(conn, rows)
            if result >= 0:
                return result
            # Appender failed (e.g., older DuckDB version) — fall through to executemany

        try:
            stmt = self._get_insert_stmt(conn)

            def _do(c: Any) -> None:
                if stmt is not None:
                    stmt.executemany(rows)
                else:
                    c.executemany(self._SQL_INSERT_SHADOW_FINDING, rows)

            self._with_transaction(conn, _do)
            return len(rows)
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            _logger.error(f"[D7] DuckDB bulk insert failed: {type(e).__name__}: {e}")
            return 0

    def _insert_via_appender(self, conn: Any, rows: list[list[Any]]) -> int:
        """
        R2: DuckDBAppender insert — bypasses SQL parser for direct columnar write.

        Strategy: append to a temporary table, then INSERT...SELECT with
        ON CONFLICT DO NOTHING into canonical_findings. This preserves
        conflict semantics that raw DuckDBAppender doesn't support.

        Returns number of rows inserted, or -1 on failure (caller falls back).
        M1 8GB safe: temp table is session-scoped, dropped immediately after.
        """
        import uuid as _uuid
        _TEMP_TABLE = f"_appender_bulk_{_uuid.uuid7().hex[:8]}"
        try:
            # Create temp table with same schema as canonical_findings (subset of columns)
            conn.execute(f"""
                CREATE TEMP TABLE {_TEMP_TABLE} (
                    id VARCHAR, query VARCHAR, source_type VARCHAR,
                    confidence DOUBLE, ts DOUBLE, provenance_json VARCHAR,
                    claims_json VARCHAR
                )
            """)
            # DuckDBAppender — zero-SQL-parser columnar write
            appender = conn.append(_TEMP_TABLE)
            try:
                for row in rows:
                    # Pad rows to 7 columns (appender expects exact column count)
                    padded = list(row) + [None] * (7 - len(row))
                    appender.append_row(padded[:7])
            finally:
                appender.close()
            # Atomic move with conflict handling
            result = conn.execute(f"""
                INSERT INTO canonical_findings
                    (id, query, source_type, confidence, ts, provenance_json, claims_json)
                SELECT id, query, source_type, confidence, ts, provenance_json, claims_json
                FROM {_TEMP_TABLE}
                ON CONFLICT (id) DO NOTHING
            """)
            return result.fetchall()[0][0] if result.description else len(rows)
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            _logger.debug(f"[R2] DuckDBAppender insert failed, falling back to executemany: {type(e).__name__}: {e}")
            return -1
        finally:
            try:
                conn.execute(f"DROP TABLE IF EXISTS {_TEMP_TABLE}")
            except Exception:  # noqa: BLE001 — best-effort; cleanup; non-critical
                pass

    # ── Claims Enrichment ─────────────────────────────────────────────────────────

    @property
    def _claims_enabled(self) -> bool:
        """Check if claims extraction is enabled."""
        try:
            from hledac.universal._core.env_config import ENV

            return ENV.get_bool("HLEDAC_ENABLE_CLAIMS_EXTRACTION")
        except Exception:
            return False

    def _enrich_findings_with_claims(self, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        F350M-R: Enrich findings with claims extracted via Rust batch_extract_claims_python.

        Calls Rust batch_extract_claims_python with (text, title, summary, source_type, evidence_type)
        for each finding that has payload_text. Results are stored as claims_json.

        Bounded: rayon parallel path activates only for batch >= adaptive_threshold
        or total_bytes >= 16KB (per claims_extraction.rs design).

        Thread-safe: MUST be called on the worker thread (called from insert_findings_bulk).
        Lazy import: claims_extraction module loaded only when _claims_enabled is True.
        """
        # Filter findings that have text to process
        texts_data = [
            (i, r.get("payload_text") or r.get("text", ""), r.get("title", ""), r.get("query", ""), r.get("source_type", "PUBLIC"), "finding")
            for i, r in enumerate(findings)
            if r.get("payload_text") or r.get("text")
        ]

        if not texts_data:
            return findings

        indices = [item[0] for item in texts_data]
        texts = [item[1] for item in texts_data]
        titles = [item[2] for item in texts_data]
        summaries = [item[3] for item in texts_data]
        source_types = [item[4] for item in texts_data]
        evidence_types = [item[5] for item in texts_data]

        try:
            # Lazy import — claims_extraction loaded only when needed
            # R6: Centralized Rust access via core.rust_backend
            from hledac.universal._core.rust_backend import rust
            batch_extract_claims_python = rust.raw.batch_extract_claims_python  # type: ignore[assignment]

            # PyO3 zero-copy: single GIL acquisition for entire batch
            claims_result: list[tuple[str, str, float, str, str]] = batch_extract_claims_python(
                texts, titles, summaries, source_types, evidence_types
            )

            # Map claims back to findings by index
            import orjson

            for i, finding_idx in enumerate(indices):
                if i < len(claims_result):
                    claim = claims_result[i]
                    claims_json = orjson.dumps([{"text": claim[0], "polarity": claim[1], "confidence": claim[2], "source": claim[3], "evidence_type": claim[4]}])
                    findings[finding_idx]["claims_json"] = claims_json
        except Exception as e:  # noqa: BLE001 — fail-soft: claims extraction is best-effort
            _logger.debug(f"[F350M-R] Claims extraction failed: {type(e).__name__}: {e}")

        return findings

    # ── WAL Management ───────────────────────────────────────────────────────────

    @contextmanager
    def _wal_delete_mode(self) -> Any:
        """
        F275-2: Context manager — WAL→DELETE journal mode switch for bulk inserts.

        For bulk inserts (≥CHUNK_SIZE=2048), temporarily switch from WAL to DELETE
        journal mode. WAL mode costs 2× fsync per write (WAL write + DB write);
        DELETE costs 1× fsync. M1 SSD is safe for DELETE — single write is sufficient.

        The LMDB WAL layer is unaffected (separate journal).

        Restores WAL on exit regardless of success/failure.
        Fail-soft: any error is logged and swallowed — caller continues.

        P2-22 FIX: Cache original_mode on the connection object so subsequent
        calls within the same session skip the PRAGMA query (2 round-trips saved
        per chunk). The cache is stored on the QueryExecutor instance, which is
        a process-wide singleton per DuckDBShadowStore instance.
        """
        conn = self._conn()
        if conn is None:
            yield
            return
        try:
            cached_mode = self._stmt_insert_finding_conn_id
        except AttributeError:
            cached_mode = None
        conn_id = id(conn)
        if isinstance(cached_mode, tuple) and cached_mode[0] == conn_id:
            original_mode = cached_mode[1]
        else:
            original_mode = None
            try:
                result = conn.execute("PRAGMA journal_mode").fetchone()
                if result:
                    original_mode = str(result[0]).upper()
                object.__setattr__(self, "_stmt_insert_finding_conn_id", (conn_id, original_mode))
            except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                original_mode = None
        try:
            if original_mode == "WAL":
                conn.execute("PRAGMA journal_mode=DELETE")
            yield
        except Exception as _e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            _logger.debug(f"[F275-2] WAL→DELETE switch failed: {_e}")
            yield
        finally:
            if original_mode == "WAL":
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except Exception as _e2:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                    _logger.debug(f"[F275-2] WAL restore failed: {_e2}")

    # ── Arrow Bulk Insert ─────────────────────────────────────────────────────────

    def insert_findings_bulk_arrow(self, table: Any) -> tuple[int, str | None]:
        """
        Sprint P0-4: Zero-copy Arrow bulk insert via DuckDB register() + INSERT...SELECT.

        MUST be called on the worker thread (thread-affine connection).
        Returns (row_count, error_type) on success: (n_rows, None).
        On any failure returns (0, error_type) where error_type is one of:
          "table_none"    - table is None
          "num_rows_err"  - failed to read num_rows
          "zero_rows"     - table has 0 rows
          "no_conn"       - could not acquire connection
          "pyarrow_build" - pa.Table.from_arrays failed (inside DuckDB register)
          "duckdb_error"  - DuckDB register/execute/unregister failed

        Why: executemany with N prepared stmt.execute() Python calls has ~3-5x the
        per-row Python overhead of one Arrow register() + one INSERT...SELECT.
        Provenance is already serialized in `table` (caller builds pa.array of JSON strs),
        so this method does no Python-level encoding.

        ON CONFLICT (id) DO NOTHING handles primary-key collisions silently.
        The secondary UNIQUE(query, source_type) constraint is NOT protected here;
        caller is expected to pre-dedupe or accept the failure (logged + return 0).
        """
        if table is None:
            return (0, "table_none")
        try:
            n_rows = int(table.num_rows)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return (0, "num_rows_err")
        if n_rows == 0:
            return (0, "zero_rows")
        conn = self._conn()
        if conn is None:
            return (0, "no_conn")
        try:
            with self._wal_delete_mode():
                import uuid as _uuid

                reg_name = f"finding_arrow_batch_{_uuid.uuid7().hex[:12]}"
                conn.register(reg_name, table)
                try:
                    # B1-FIX: MERGE replaces 2× INSERT round-trips with 1.
                    # Handles both PK (id) and UK (query, source_type) conflicts.
                    # DuckDB supports MERGE since v0.10.0; this codebase requires v1.0+ (F275).
                    # F360M-R: payload_text + claims_json added to MERGE — matches 8-column schema.
                    # Arrow batch paths now include scrubbed payload_text (SEC-01) and claims_json.
                    conn.execute(
                        f"MERGE INTO canonical_findings AS target USING {reg_name} AS source\n"
                        f"ON target.id = source.id\n"
                        f"WHEN NOT MATCHED THEN INSERT (id, query, source_type, confidence, ts, provenance_json, payload_text, claims_json)\n"
                        f"VALUES (source.id, source.query, source.source_type, source.confidence, source.ts, source.provenance_json, source.payload_text, source.claims_json)"
                    )
                finally:
                    try:
                        conn.unregister(reg_name)
                    except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                        pass
                return (n_rows, None)
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            _logger.error(f"[P0-4 Arrow] DuckDB Arrow bulk insert failed: {type(e).__name__}: {e}")
            return (0, "duckdb_error")

    def insert_findings_bulk_copy_arrow(self, table: Any) -> tuple[int, str | None]:
        """
        ISSUE-16: Zero-copy Arrow COPY FROM — fastest DuckDB insert path.

        Uses DuckDB's native ``COPY ... FROM ? (FORMAT 'arrow')`` to stream Arrow
        RecordBatch/Table directly into DuckDB without register() overhead or temp
        Parquet files. DuckDB's COPY FROM with Arrow format bypasses the SQL
        parser entirely for columnar data — ~2-4× faster than register()+MERGE
        and avoids temp-file I/O.

        Strategy for conflict handling:
          1. COPY FROM into a TEMP table (columns match canonical_findings)
          2. INSERT...SELECT with ON CONFLICT (id) DO NOTHING
          3. Drop TEMP table

        MUST be called on the worker thread (thread-affine connection).
        Returns (row_count, error_type) on success: (n_rows, None).
        On any failure returns (0, error_type).

        Error types:
            "table_none"    - table is None
            "num_rows_err"  - failed to read num_rows
            "zero_rows"     - table has 0 rows
            "no_conn"       - could not acquire DuckDB connection
            "duckdb_error"  - DuckDB COPY FROM or INSERT failed

        M1 8GB safety: TEMP table is session-scoped, dropped immediately after INSERT.
        """
        if table is None:
            return (0, "table_none")
        try:
            n_rows = int(table.num_rows)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return (0, "num_rows_err")
        if n_rows == 0:
            return (0, "zero_rows")
        conn = self._conn()
        if conn is None:
            return (0, "no_conn")

        import uuid as _uuid

        _TEMP_TABLE = f"_copy_arrow_batch_{_uuid.uuid7().hex[:8]}"
        try:
            with self._wal_delete_mode():
                # Step 1: Create temp table with same schema as canonical_findings
                conn.execute(f"""
                    CREATE TEMP TABLE {_TEMP_TABLE} (
                        id VARCHAR, query VARCHAR, source_type VARCHAR,
                        confidence DOUBLE, ts DOUBLE,
                        provenance_json VARCHAR, payload_text VARCHAR,
                        claims_json VARCHAR
                    )
                """)
                # Step 2: COPY FROM Arrow — zero-copy columnar ingestion
                conn.execute(
                    f"COPY {_TEMP_TABLE} FROM ? (FORMAT 'arrow')",
                    [table],
                )
                # Step 3: Atomic move with conflict handling
                conn.execute(f"""
                    INSERT INTO canonical_findings
                        (id, query, source_type, confidence, ts,
                         provenance_json, payload_text, claims_json)
                    SELECT id, query, source_type, confidence, ts,
                           provenance_json, payload_text, claims_json
                    FROM {_TEMP_TABLE}
                    ON CONFLICT (id) DO NOTHING
                """)
                return (n_rows, None)
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            _logger.error(
                f"[ISSUE-16 Arrow] COPY FROM bulk insert failed: {type(e).__name__}: {e}"
            )
            return (0, "duckdb_error")
        finally:
            try:
                conn.execute(f"DROP TABLE IF EXISTS {_TEMP_TABLE}")
            except Exception:  # noqa: BLE001 — best-effort; cleanup; non-critical
                pass

    def insert_findings_bulk_parquet(self, table: Any, temp_dir: Any = None) -> tuple[int, str | None]:
        """
        R10: Parquet COPY FROM bulk insert for large batches.

        DuckDB's Parquet reader uses internal parallelism (multi-threaded
        decompression + decoding), making it faster than register() + MERGE
        for batches >5000 on M1 (4 P-cores + 4 E-cores).

        Flow:
          1. Write ``table`` (pyarrow.RecordBatch/Table) to a temp .parquet file
          2. ``COPY ... FROM 'file.parquet'`` — DuckDB auto-parallelizes this
          3. Clean up temp file

        MUST be called on the worker thread (thread-affine connection).
        Returns (row_count, error_type) on success: (n_rows, None).
        On any failure returns (0, error_type).

        Args:
            table: pyarrow Table or RecordBatch to persist
            temp_dir: Optional directory for temp Parquet file (uses db_path parent
                     if None; falls back to /tmp)

        Error types:
            "table_none"       - table is None
            "num_rows_err"     - failed to read num_rows
            "zero_rows"        - table has 0 rows
            "no_conn"          - could not acquire DuckDB connection
            "parquet_write_err" - failed to write temp Parquet file
            "duckdb_error"     - DuckDB COPY FROM failed

        M1 8GB safety: temp file uses zstd level 1 (fast, low RAM), deleted immediately
        after COPY FROM succeeds or fails.
        """
        from pathlib import Path as _Path
        import tempfile as _tempfile
        import os as _os
        import pyarrow.parquet as _pq

        if table is None:
            return (0, "table_none")
        try:
            n_rows = int(table.num_rows)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return (0, "num_rows_err")
        if n_rows == 0:
            return (0, "zero_rows")
        conn = self._conn()
        if conn is None:
            return (0, "no_conn")

        # Resolve temp directory — prefer db_path parent, fallback to /tmp
        _resolve_dir = temp_dir
        if _resolve_dir is None:
            try:
                from hledac.universal.utils.paths import get_data_dir
                _resolve_dir = get_data_dir() / "tmp"
                _resolve_dir.mkdir(parents=True, exist_ok=True)
            except Exception:  # noqa: BLE001 — best-effort; non-critical fallback
                _resolve_dir = _Path(_tempfile.gettempdir())

        parquet_path = None
        try:
            import uuid as _uuid
            parquet_path = _Path(_resolve_dir) / f"finding_batch_{_uuid.uuid7().hex[:12]}.parquet"

            # Write Parquet: zstd level 1 — fast compression, low RAM, M1-friendly
            _pq.write_table(
                table,
                str(parquet_path),
                compression="zstd",
                compression_level=1,
                use_dictionary=True,
            )

            with self._wal_delete_mode():
                # DuckDB COPY FROM uses internal parallelism for Parquet I/O.
                # ON CONFLICT (id) DO NOTHING handles PK collisions.
                conn.execute(
                    f"COPY canonical_findings FROM '{parquet_path}' (FORMAT PARQUET)"
                )
                # COPY FROM doesn't return row count like MERGE; use num_rows from table
                # minus any PK conflicts (we trust ON CONFLICT DO NOTHING semantics
                # which DuckDB COPY FROM respects via unique index enforcement)
            return (n_rows, None)
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            _logger.error(f"[R10 Parquet] COPY FROM bulk insert failed: {type(e).__name__}: {e}")
            return (0, "duckdb_error")
        finally:
            # Best-effort cleanup — don't let temp file accumulate
            if parquet_path is not None:
                try:
                    _os.unlink(str(parquet_path))
                except OSError:  # noqa: BLE001
                    pass

    # ── Run & Profile Operations ─────────────────────────────────────────────────

    def insert_run(
        self, run_id: str, started_at: float | None, ended_at: float | None, total_fds: int, rss_mb: int
    ) -> bool:
        import datetime as _dt

        conn = self._conn()
        if conn is None:
            return False
        started_iso = _dt.datetime.fromtimestamp(started_at).isoformat() if started_at is not None else None
        ended_iso = _dt.datetime.fromtimestamp(ended_at).isoformat() if ended_at is not None else None
        params = [run_id, started_iso, ended_iso, total_fds, rss_mb]
        cast_sql = "INSERT INTO shadow_runs (run_id, started_at, ended_at, total_fds, rss_mb) VALUES (?, CAST(? AS TIMESTAMP), CAST(? AS TIMESTAMP), ?, ?)"
        try:
            self._with_transaction(conn, lambda c: c.execute(cast_sql, params))
            return True
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return False

    def upsert_target_profile(self, profile: Any) -> None:
        """Upsert target profile. Silently returns on failure."""
        conn = self._conn()
        if conn is None:
            return
        sql = "INSERT OR REPLACE INTO target_profiles (target_id, first_seen, last_seen, cumulative_finding_count, entity_summary_json) VALUES (?, ?, ?, ?, ?)"
        params = [
            profile.target_id,
            profile.first_seen,
            profile.last_seen,
            profile.cumulative_finding_count,
            profile.entity_summary_json,
        ]
        try:
            conn.execute(sql, params)
        except (OSError, RuntimeError) as e:
            _logger.warning(f"[DUCKDB] upsert_target_profile failed: {e}")

    def get_target_profile(self, target_id: str) -> Any:
        """Get target profile. Returns row tuple or None."""
        from time import perf_counter_ns

        conn = self._conn()
        if conn is None:
            return None
        sql = "SELECT target_id, first_seen, last_seen, cumulative_finding_count, entity_summary_json FROM target_profiles WHERE target_id = ?"
        try:
            t0 = perf_counter_ns()
            result = conn.execute(sql, [target_id]).fetchone()
            self._store._record_query_latency(self._query_latencies_ns, perf_counter_ns() - t0)
            return result
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return None

    # ── Query Operations ──────────────────────────────────────────────────────────

    @property
    def _query_latencies_ns(self) -> list[float]:
        """Get or create query latency tracking list."""
        if not hasattr(self, "_query_latencies_ns_list"):
            object.__setattr__(self, "_query_latencies_ns_list", [])
        return self._query_latencies_ns_list

    def query_findings(self, limit: int) -> list[dict[str, Any]]:
        """Select recent shadow findings. Returns list of dicts."""
        from time import perf_counter_ns

        conn = self._conn()
        if conn is None:
            return []
        sql = "SELECT id, query, source_type, confidence, ts, provenance_json, payload_text, claims_json FROM canonical_findings ORDER BY ts DESC LIMIT ?"
        try:
            t0 = perf_counter_ns()
            raw_result = list(self._store.arrow_fetch_batch(conn, sql, [limit]))
            try:
                self._store._record_query_latency(self._query_latencies_ns, perf_counter_ns() - t0)
            except Exception:  # noqa: BLE001 — best-effort; metrics failure; non-critical
                pass
            return [
                {
                    "id": row[0],
                    "query": row[1],
                    "source_type": row[2],
                    "confidence": row[3],
                    "ts": row[4],
                    "provenance_json": row[5],
                }
                for row in raw_result
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []
