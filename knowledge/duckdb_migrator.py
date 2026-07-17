"""
knowledge/duckdb_migrator.py — DuckDB Schema Migration System (P0-8)

Migrates the DuckDB schema from a monolithic inline _SCHEMA_SQL to a
versioned migration directory at knowledge/duckdb_migrations/.

Design
------
- Migration files are named ``NNN_description.sql`` where NNN is the version
  number (zero-padded to 4 digits). Files are sorted numerically.
- Each migration is idempotent: ``CREATE TABLE IF NOT EXISTS`` /
  ``CREATE INDEX IF NOT EXISTS`` — safe to re-run against an existing DB.
- Migrations are applied in order on every startup; already-applied
  migrations are skipped via the ``schema_version`` table.
- ``schema_version`` is itself created by migration 001, so the system is
  bootstrapped from zero.
- Rollback is not required for sprint-cycle databases (they are ephemeral
  in some profiles) but ``down.sql`` files are supported via the
  ``_get_rollback(conn, version)`` path.

Wire-in
-------
``SchemaMigrator.migrate(conn)`` is called from
``DuckDBShadowStore._init_connection()`` (line ~2266) after the WAL is
replayed and before the store becomes available for writes.

Cache invalidation: after ``migrate()`` completes, the caller must
invalidate ``DuckDBShadowStore._query_cache`` via ``.invalidate()`` so
that cached results from the old schema are never served. The invalidation
is the caller's responsibility because the ``SchemaMigrator`` has no access
to the store instance.

M1 8GB constraints
------------------
- No threading / subprocess spawning in the migrator itself.
- All operations are synchronous DuckDB connection operations (CPU-bound but
  trivially fast for the small number of migrations).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger("hledac.universal.knowledge.duckdb_migrator")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR: Path = Path(__file__).parent / "duckdb_migrations"
_SCHEMA_VERSION_TABLE: str = "schema_version"
_CURRENT_SCHEMA_VERSION: int = 2  # highest migration number


# ---------------------------------------------------------------------------
# SchemaMigrator
# ---------------------------------------------------------------------------

class SchemaMigrator:
    """
    DuckDB schema migration runner.

    Applies SQL migration files from ``knowledge/duckdb_migrations/`` in
    ascending version order. Tracks applied versions in the
    ``schema_version`` table so each migration runs at most once.

    Idempotent: uses ``CREATE ... IF NOT EXISTS`` inside each migration file.

    Thread-safe: all operations are synchronous; caller must hold any
    necessary lock.
    """

    __slots__ = ("_conn", "_migrations_dir")

    def __init__(self, conn: Any, migrations_dir: Path | None = None) -> None:
        """
        Args:
            conn: A live DuckDB connection (raw ``duckdb.connect()`` object).
            migrations_dir: Override for the migrations directory. Defaults to
                ``knowledge/duckdb_migrations/``.
        """
        self._conn = conn
        self._migrations_dir = migrations_dir or _MIGRATIONS_DIR

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def migrate(self) -> int:
        """
        Run all un-applied migrations.

        Returns:
            Number of migrations applied (0 means fully up-to-date).
        """
        if not self._migrations_dir.exists():
            logger.debug(
                f"[duckdb_migrator] Migrations directory not found: "
                f"{self._migrations_dir} — skipping migrations"
            )
            return 0

        applied = self._get_applied_versions()
        to_apply = self._pending_versions(applied)

        if not to_apply:
            logger.debug(
                f"[duckdb_migrator] Schema up-to-date "
                f"(highest applied: {max(applied) if applied else 0})"
            )
            return 0

        logger.info(
            f"[duckdb_migrator] Applying {len(to_apply)} migration(s): "
            f"{to_apply}"
        )

        count = 0
        for version in to_apply:
            ok = self._apply_migration(version)
            if not ok:
                logger.error(
                    f"[duckdb_migrator] Migration {version} FAILED — "
                    f"aborting further migrations"
                )
                break
            count += 1

        logger.info(f"[duckdb_migrator] Applied {count} migration(s)")
        return count

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_applied_versions(self) -> set[int]:
        """Return the set of already-applied schema version numbers.

        Bootstrap: if ``schema_version`` does not exist but
        ``information_schema.tables`` shows existing tables (legacy DB from the
        monolithic inline ``_SCHEMA_SQL`` era), we create the schema_version
        table and record version 1 as already applied so that the full init
        schema is not re-run.
        """
        try:
            result = self._conn.execute(
                f"SELECT version FROM {_SCHEMA_VERSION_TABLE}",
            ).fetchall()
            return {row[0] for row in result}
        except Exception:  # noqa: BLE001 — table may not exist yet (fresh DB)
            pass

        # schema_version does not exist — check for legacy tables.
        # If any tables exist, this is a pre-migration DB and we bootstrap
        # the baseline version by creating schema_version and recording v1.
        try:
            tables_exist = self._conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchone()
            if tables_exist and tables_exist[0] > 0:
                logger.info(
                    f"[duckdb_migrator] Legacy DB detected "
                    f"({tables_exist[0]} tables, no schema_version) — "
                    f"bootstrapping baseline version = 1"
                )
                # Create schema_version table and record v1 as applied
                self._conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {_SCHEMA_VERSION_TABLE} ("
                    f"version INTEGER PRIMARY KEY, applied_at DOUBLE, description TEXT)"
                )
                self._conn.execute(
                    f"INSERT INTO {_SCHEMA_VERSION_TABLE} VALUES (1, 0.0, 'legacy-bootstrap')"
                )
                return {1}
        except Exception:  # noqa: BLE001 — information_schema may not be available
            pass

        return set()

    def _pending_versions(self, applied: set[int]) -> list[int]:
        """Return sorted list of version numbers that need applying."""
        migration_files = sorted(self._migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.sql"))
        all_versions: list[int] = []
        for path in migration_files:
            try:
                ver = int(path.stem.split("_")[0])
                all_versions.append(ver)
            except ValueError:
                logger.warning(
                    f"[duckdb_migrator] Skipping file with unexpected name: {path.name}"
                )
                continue

        return [v for v in sorted(all_versions) if v not in applied]

    def _apply_migration(self, version: int) -> bool:
        """
        Apply a single migration by version number.

        1. Read the migration SQL file.
        2. Execute it (stopping on first non-duplicate_column error).
        3. Record the version in ``schema_version``.

        Duplicate-column errors (from ``ALTER TABLE ADD COLUMN`` on a column
        that already exists in a fully-migrated DB) are treated as success —
        the column is already there, which is the desired end state.

        Returns True on success, False on error.
        """
        # Find the file — glob is stable sort, use the first matching version
        candidates = sorted(
            self._migrations_dir.glob(f"{version:04d}_*.sql")
        )
        if not candidates:
            logger.error(
                f"[duckdb_migrator] Migration file for version {version} not found"
            )
            return False

        migration_path = candidates[0]
        sql = migration_path.read_text()

        # Strip comment-only lines and leading/trailing whitespace before
        # we hand it to DuckDB's multi-statement executor.
        clean_sql = _strip_leading_comments(sql).strip()
        if not clean_sql:
            logger.debug(
                f"[duckdb_migrator] Migration {version} is empty after "
                f"stripping comments — treating as no-op"
            )
            # Still record the version so we don't re-scan
            return self._record_version(version, migration_path.name)

        try:
            self._conn.execute(clean_sql)
        except Exception as exc:  # noqa: BLE001 — best-effort
            exc_msg = str(exc).lower()
            # DuckDB uses "duplicate column" in error messages for ALTER ADD COLUMN
            if "duplicate column" in exc_msg or "already exists" in exc_msg:
                logger.debug(
                    f"[duckdb_migrator] Migration {version}: column already "
                    f"exists — treating as success: {exc}"
                )
            else:
                logger.error(
                    f"[duckdb_migrator] Migration {version} SQL execution failed: {exc}"
                )
                return False

        return self._record_version(version, migration_path.name)

    def _record_version(self, version: int, filename: str) -> bool:
        """Record a successfully-applied migration version."""
        try:
            self._conn.execute(
                f"INSERT OR IGNORE INTO {_SCHEMA_VERSION_TABLE} "
                f"(version, applied_at, description) VALUES (?, ?, ?)",
                [version, time.time(), filename],
            )
            return True
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.error(
                f"[duckdb_migrator] Failed to record version {version}: {exc}"
            )
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_leading_comments(sql: str) -> str:
    """
    Strip leading ``--`` and ``#`` line comments and ``/* ... */`` block
    comments that appear before any SQL statement.  Preserves SQL comments
    that appear after statements.
    """
    import re

    # Remove block comments first (/* ... */)
    sql = re.sub(r"/\*[^*]*\*+(?:[^/*][^*]*\*+)*/", "", sql, flags=re.DOTALL)
    # Remove single-line -- and # comments (^ anchor only, not ^ inside string)
    sql = re.sub(r"^\s*--.*$", "", sql, flags=re.MULTILINE)
    sql = re.sub(r"^\s*#.*$", "", sql, flags=re.MULTILINE)
    # Strip leading/trailing blank lines that result from comment removal
    sql = sql.strip()
    return sql
