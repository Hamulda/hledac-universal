"""
tests/test_duckdb_migrator.py — P0-8: DuckDB SchemaMigrator tests
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from hledac.universal.knowledge.duckdb_migrator import (
from core import aclose
    SchemaMigrator,
    _CURRENT_SCHEMA_VERSION,
    _strip_leading_comments,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """A temporary DuckDB file path (auto-cleaned)."""
    return tmp_path / "test_migrations.duckdb"


@pytest.fixture
def duckdb_conn(temp_db_path: Path):
    """A raw DuckDB connection to the temp DB, auto-closed on teardown."""
    import duckdb

    conn = duckdb.connect(str(temp_db_path))
    yield conn
    try:
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStripLeadingComments:
    def test_strip_block_comment(self) -> None:
        sql = "/* block comment */ CREATE TABLE t (id TEXT);"
        assert _strip_leading_comments(sql) == "CREATE TABLE t (id TEXT);"

    def test_strip_line_comments(self) -> None:
        sql = "-- line comment\nCREATE TABLE t (id TEXT);"
        assert _strip_leading_comments(sql) == "CREATE TABLE t (id TEXT);"

    def test_strip_hash_comments(self) -> None:
        sql = "# hash comment\nCREATE TABLE t (id TEXT);"
        assert _strip_leading_comments(sql) == "CREATE TABLE t (id TEXT);"

    def test_preserves_inline_comments(self) -> None:
        sql = "CREATE TABLE t (id TEXT); -- inline comment stays"
        result = _strip_leading_comments(sql)
        assert "inline comment" in result


class TestSchemaMigratorFreshDB:
    """Tests on a brand-new in-memory DB (no existing tables)."""

    def test_migrate_applies_all_migrations(
        self, temp_db_path: Path, duckdb_conn
    ) -> None:
        import duckdb

        m = SchemaMigrator(duckdb_conn)

        applied = m.migrate()

        assert applied == _CURRENT_SCHEMA_VERSION, (
            f"Expected {_CURRENT_SCHEMA_VERSION} migrations, got {applied}"
        )

        # Verify schema_version was recorded
        rows = duckdb_conn.execute(
            "SELECT version, description FROM schema_version ORDER BY version"
        ).fetchall()
        versions = [r[0] for r in rows]
        assert versions == [1, 2, 3, 4], f"Expected [1, 2, 3, 4], got {versions}"

        # Verify init schema was applied (canonical_findings table)
        tables = duckdb_conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main'"
        ).fetchall()
        table_names = {r[0] for r in tables}
        assert "canonical_findings" in table_names
        assert "schema_version" in table_names

        # Verify indexes on canonical_findings (DuckDB uses pg_indexes, not information_schema.indexes)
        indexes = duckdb_conn.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'canonical_findings'"
        ).fetchall()
        index_names = {r[0] for r in indexes}
        assert "idx_canonical_findings_ts" in index_names

    def test_migrate_idempotent_second_call(
        self, temp_db_path: Path, duckdb_conn
    ) -> None:
        import duckdb

        m = SchemaMigrator(duckdb_conn)

        first = m.migrate()
        assert first == _CURRENT_SCHEMA_VERSION

        # Open a new connection to verify persistence
        conn2 = duckdb.connect(str(temp_db_path))
        m2 = SchemaMigrator(conn2)
        second = m2.migrate()
        assert second == 0, f"Expected 0 on second call, got {second}"
        conn2.close()


class TestSchemaMigratorLegacyDB:
    """Tests on a legacy DB that was created before the migration system."""

    def test_legacy_db_bootstraps_version_1(
        self, temp_db_path: Path, duckdb_conn
    ) -> None:
        import duckdb

        # Simulate a legacy DB: create tables but no schema_version
        duckdb_conn.execute(
            "CREATE TABLE IF NOT EXISTS canonical_findings ("
            "id VARCHAR PRIMARY KEY, query VARCHAR, source_type VARCHAR, "
            "confidence DOUBLE, ts DOUBLE, provenance_json TEXT, payload_text TEXT)"
        )
        duckdb_conn.execute(
            "CREATE TABLE IF NOT EXISTS sprint_delta ("
            "sprint_id TEXT PRIMARY KEY, ts DOUBLE)"
        )
        duckdb_conn.commit()

        # Re-open with a new connection (simulates cold start after code update)
        duckdb_conn.close()
        conn2 = duckdb.connect(str(temp_db_path))
        m = SchemaMigrator(conn2)

        # Should bootstrap version 1 (legacy tables exist)
        applied_versions = m._get_applied_versions()
        assert 1 in applied_versions, (
            f"Expected version 1 bootstrapped for legacy DB, got {applied_versions}"
        )

        # migrate() should only apply v2 (v1 is already "applied")
        count = m.migrate()
        assert count == 1, f"Expected 1 migration (v2 only), got {count}"

        rows = conn2.execute(
            "SELECT version FROM schema_version ORDER BY version"
        ).fetchall()
        assert [r[0] for r in rows] == [1, 2]
        conn2.close()

    def test_legacy_db_sprint_delta_alter_columns(
        self, temp_db_path: Path, duckdb_conn
    ) -> None:
        import duckdb

        # Simulate a legacy DB with bare sprint_delta (missing 3 columns added in v2)
        duckdb_conn.execute(
            "CREATE TABLE IF NOT EXISTS sprint_delta ("
            "sprint_id TEXT PRIMARY KEY, ts DOUBLE)"
        )
        duckdb_conn.execute(
            "CREATE TABLE IF NOT EXISTS canonical_findings ("
            "id VARCHAR PRIMARY KEY, query VARCHAR, source_type VARCHAR, "
            "confidence DOUBLE, ts DOUBLE, provenance_json TEXT, payload_text TEXT)"
        )
        duckdb_conn.commit()
        duckdb_conn.close()

        # Run migration on a fresh connection
        conn2 = duckdb.connect(str(temp_db_path))
        m = SchemaMigrator(conn2)
        m.migrate()

        # Verify new columns exist
        cols = conn2.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'sprint_delta'"
        ).fetchall()
        col_names = {r[0] for r in cols}
        assert "findings_per_minute" in col_names
        assert "top_source_type" in col_names
        assert "synthesis_confidence" in col_names
        conn2.close()


class TestSchemaMigratorCurrentDB:
    """Tests on a DB that already has schema_version (already migrated)."""

    def test_current_db_no_migrations_applied(
        self, temp_db_path: Path, duckdb_conn
    ) -> None:
        import duckdb

        duckdb_conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "version INTEGER PRIMARY KEY, applied_at DOUBLE, description TEXT)"
        )
        duckdb_conn.execute(
            "INSERT INTO schema_version VALUES (1, 0.0, 'legacy')"
        )
        duckdb_conn.execute(
            "INSERT INTO schema_version VALUES (2, 0.0, 'legacy')"
        )
        duckdb_conn.execute(
            "CREATE TABLE IF NOT EXISTS canonical_findings ("
            "id VARCHAR PRIMARY KEY)"
        )
        duckdb_conn.commit()
        duckdb_conn.close()

        conn2 = duckdb.connect(str(temp_db_path))
        m = SchemaMigrator(conn2)

        applied = m._get_applied_versions()
        assert applied == {1, 2}

        count = m.migrate()
        assert count == 0
        conn2.close()
