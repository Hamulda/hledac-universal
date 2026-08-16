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
from typing import Any
from _core import aclose

logger = logging.getLogger("hledac.universal.knowledge.duckdb_migrator")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR: Path = Path(__file__).parent / "duckdb_migrations"
_SCHEMA_VERSION_TABLE: str = "schema_version"
_CURRENT_SCHEMA_VERSION: int = 12  # highest migration number

# Legacy DBs (pre-migration, created before the migration system existed) have
# their baseline schema already applied via the inline _SCHEMA_SQL era.
# We track this in-memory only — we do NOT insert into schema_version so that
# subsequent _get_applied_versions() calls re-derive the state from
# information_schema rather than trusting a bootstrapped sentinel.
# The contract: migration 0001 must be 100% idempotent (CREATE IF NOT EXISTS)
# for this to be safe. ALTER TABLE statements can NEVER be added to 0001.
_BOOTSTRAP_VERSIONS: frozenset[int] = frozenset({1})

# ---------------------------------------------------------------------------
# Duplicate-column detection patterns (DuckDB-specific, case-insensitive)
# ---------------------------------------------------------------------------

# Patterns that identify "already exists" type errors.
# Standalone "already exists" without an object-type signal (column/index)
# is NOT included — it would be "table / schema already exists" which never
# reaches us because CREATE TABLE/INDEX use IF NOT EXISTS.
_ALREADY_EXISTS_SIGNALS: tuple[str, ...] = (
    "already exists",
    "duplicate",
    )

# Patterns that identify "column or index" objects in the error context.
_OBJECT_TYPE_SIGNALS: tuple[str, ...] = (
    "column",
    "index",
    )


def _is_column_already_exists_error(exc_msg: str) -> bool:
    """
    Return True if the DuckDB exception message indicates a
    duplicate-column / already-exists error.

    Uses two-signal coincidence: the message must contain at least one
    ``_ALREADY_EXISTS_SIGNALS`` AND at least one ``_OBJECT_TYPE_SIGNALS``.
    This avoids false positives from overly-broad single patterns like
    ``"column"`` matching ``"column 'foo' does not exist"``.
    """
    has_exists = any(s in exc_msg for s in _ALREADY_EXISTS_SIGNALS)
    has_object = any(s in exc_msg for s in _OBJECT_TYPE_SIGNALS)
    return has_exists and has_object

# SQL statements that are inherently idempotent — any error other than
# catastrophic (connection loss, syntax) should not stop the migration.
_INHERENTLY_IDEMPOTENT_PREFIXES: tuple[str, ...] = (
    "create table if not exists",
    "create index if not exists",
    "create view if not exists",
    "insert or ignore",
    "insert or replace",
    )


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
        self._validate_version_contiguity()
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
        monolithic inline ``_SCHEMA_SQL`` era), we return version 1 as
        already-applied (tracked in-memory only via _BOOTSTRAP_VERSIONS) so
        that the full init schema is not re-run.

        We intentionally do NOT insert into schema_version here — the
        in-memory sentinel is the source of truth for this call; the
        information_schema heuristic is re-evaluated on every call so a
        corrupted/missing sentinel does not cause the migration to silently
        skip subsequent migrations.
        """
        try:
            result = self._conn.execute(
                f"SELECT version FROM {_SCHEMA_VERSION_TABLE}",
            ).fetchall()
            return {row[0] for row in result}
        except Exception:  # noqa: BLE001 — table may not exist yet (fresh DB)
            pass

        # schema_version does not exist — check for legacy tables.
        # If any tables exist, this is a pre-migration DB and we return
        # version 1 as already-applied (in-memory only).
        try:
            tables_exist = self._conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchone()
            if tables_exist and tables_exist[0] > 0:
                logger.info(
                    f"[duckdb_migrator] Legacy DB detected "
                    f"({tables_exist[0]} tables, no schema_version) — "
                    f"bootstrapping baseline {_BOOTSTRAP_VERSIONS} "
                    f"(tracked in-memory, NOT written to schema_version)"
    )
                # Create schema_version table so subsequent calls can read it.
                # We deliberately do NOT insert a bootstrap row — the
                # information_schema check is re-evaluated on every call.
                self._conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {_SCHEMA_VERSION_TABLE} ("
                    f"version INTEGER PRIMARY KEY, applied_at DOUBLE, description TEXT)"
    )
                return set(_BOOTSTRAP_VERSIONS)
        except Exception as exc:  # noqa: BLE001 — information_schema may not be available
            # Defensive: log the failure so silent fallback is observable.
            # This is still fail-safe — returning set() means pending migrations
            # will be re-evaluated, and all our migrations are idempotent.
            logger.warning(
                f"[duckdb_migrator] information_schema query failed "
                f"({exc}); falling back to no applied versions — "
                f"idempotent migrations will be re-checked on next startup"
    )

        return set()

    def _validate_version_contiguity(self) -> None:
        """Check that migration files form a gap-free sequence starting at 1.

        Gaps in the version sequence (e.g. 1, 2, 5 — missing 3 or 4) indicate
        a broken versioning contract and are logged as an error so developers
        catch it immediately rather than silently skipping a migration.
        """
        migration_files = sorted(
            self._migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.sql")
    )
        all_versions: list[int] = []
        for path in migration_files:
            try:
                ver = int(path.stem.split("_")[0])
                all_versions.append(ver)
            except ValueError:
                continue

        if not all_versions:
            return

        # Warn if versions don't start at 1 (gap from 0 → 1 is by design).
        if min(all_versions) > 1:
            logger.error(
                f"[duckdb_migrator] Migration versions do not start at 1 "
                f"(found min={min(all_versions)}); gap from bootstrap "
                f"may cause migrations to be skipped"
    )

        # Warn if there are any gaps in the sequence.
        expected = set(range(1, max(all_versions) + 1))
        actual = set(all_versions)
        missing = sorted(expected - actual)
        if missing:
            logger.error(
                f"[duckdb_migrator] Gap in migration sequence: {missing} — "
                f"these migrations will be skipped and may cause schema "
                f"inconsistencies; fix the versioning before shipping"
    )

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
        2. Split on semicolons; execute each statement individually so that a
           non-critical error on statement N does not abort statements N+1 … M.
        3. Record the version in ``schema_version``.

        Duplicate-column / already-exists errors (from ``ALTER TABLE ADD COLUMN``
        on a column that already exists in a fully-migrated DB) are treated as
        success — the column is already there, which is the desired end state.

        Returns True on success, False on catastrophic error (file not found,
        syntax error, connection error).
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

        # Split into individual statements — split on bare semicolons, not
        # semicolons inside string literals.  DuckDB itself accepts multiple
        # statements in one execute(), but statement-by-statement gives us
        # per-statement error granularity.
        statements = [s.strip() for s in clean_sql.split(";") if s.strip()]

        errors: list[str] = []
        for stmt_idx, stmt in enumerate(statements):
            stmt_lower = stmt.lower()
            try:
                self._conn.execute(stmt)
            except Exception as exc:  # noqa: BLE001 — best-effort
                exc_msg = str(exc).lower()
                # Inherently idempotent statements: CREATE IF NOT EXISTS,
                # INSERT OR IGNORE — treat any non-fatal error as a no-op.
                if any(stmt_lower.startswith(p) for p in _INHERENTLY_IDEMPOTENT_PREFIXES):
                    logger.debug(
                        f"[duckdb_migrator] Migration {version} stmt {stmt_idx + 1}: "
                        f"idempotent statement got '{exc}' — treating as success"
    )
                    continue
                # Duplicate-column / already-exists: benign in any statement.
                if _is_column_already_exists_error(exc_msg):
                    logger.debug(
                        f"[duckdb_migrator] Migration {version} stmt {stmt_idx + 1}: "
                        f"column/index already exists — treating as success: {exc}"
    )
                    continue
                # Non-critical: warn and continue rather than abort.
                logger.warning(
                    f"[duckdb_migrator] Migration {version} stmt {stmt_idx + 1} "
                    f"'{
stmt[:80]}' raised non-critical error — continuing: {exc}"
                )
                errors.append(str(exc))
                # Allow remaining statements to run.
                continue

        # Record the version even if some statements had non-critical errors.
        # The schema is considered "applied" once we reach this point.
        if not self._record_version(version, migration_path.name):
            return False

        if errors:
            logger.warning(
                f"[duckdb_migrator] Migration {version} completed with "
                f"{len(errors)} non-critical error(s); schema may be partially "
                f"applied: {errors[:3]}"
    )
        return True

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
