"""
Schema Migration Runner — Sprint Facts
=====================================

Manages DuckDB schema migrations for the sprint facts store.
Handles CREATE TABLE IF NOT EXISTS for all canonical tables.

MIGRATION NOTE (Issue #2):
    Schema management extracted from DuckDBShadowStore.async_initialize_schema()
    into a standalone module for independent testing and migration tracking.
"""

from typing import TYPE_CHECKING
from core import aclose

if TYPE_CHECKING:
    pass


# Schema version tracking
SCHEMA_VERSION = 1


def get_canonical_findings_schema_sql() -> str:
    """
    Canonical findings table schema.

    CREATE TABLE IF NOT EXISTS canonical_findings (
        id TEXT PRIMARY KEY,
        query TEXT NOT NULL,
        source_type TEXT NOT NULL,
        confidence REAL NOT NULL,
        ts REAL NOT NULL,
        provenance_json TEXT NOT NULL,
        payload_text TEXT,
        UNIQUE(query, source_type, provenance_json)
    );
    """
    return """
    CREATE TABLE IF NOT EXISTS canonical_findings (
        id TEXT PRIMARY KEY,
        query TEXT NOT NULL,
        source_type TEXT NOT NULL,
        confidence REAL NOT NULL,
        ts REAL NOT NULL,
        provenance_json TEXT NOT NULL,
        payload_text TEXT,
        UNIQUE(query, source_type, provenance_json)
    );
    """


def get_sprint_delta_schema_sql() -> str:
    """
    Sprint delta metrics table.

    CREATE TABLE IF NOT EXISTS sprint_delta (
        sprint_id TEXT PRIMARY KEY,
        query TEXT NOT NULL,
        duration_s REAL NOT NULL,
        new_findings INTEGER NOT NULL,
        dedup_hits INTEGER NOT NULL,
        ioc_nodes INTEGER NOT NULL,
        ts REAL NOT NULL
    );
    """
    return """
    CREATE TABLE IF NOT EXISTS sprint_delta (
        sprint_id TEXT PRIMARY KEY,
        query TEXT NOT NULL,
        duration_s REAL NOT NULL,
        new_findings INTEGER NOT NULL,
        dedup_hits INTEGER NOT NULL,
        ioc_nodes INTEGER NOT NULL,
        ts REAL NOT NULL
    );
    """


def get_sprint_scorecard_schema_sql() -> str:
    """
    Sprint scorecard table.

    CREATE TABLE IF NOT EXISTS sprint_scorecard (
        sprint_id TEXT PRIMARY KEY,
        query TEXT NOT NULL,
        duration_s REAL NOT NULL,
        fpm REAL NOT NULL,
        ioc_density REAL NOT NULL,
        synthesis_confidence REAL NOT NULL,
        new_findings INTEGER NOT NULL,
        dedup_hits INTEGER NOT NULL,
        ioc_nodes INTEGER NOT NULL,
        ts REAL NOT NULL
    );
    """
    return """
    CREATE TABLE IF NOT EXISTS sprint_scorecard (
        sprint_id TEXT PRIMARY KEY,
        query TEXT NOT NULL,
        duration_s REAL NOT NULL,
        fpm REAL NOT NULL,
        ioc_density REAL NOT NULL,
        synthesis_confidence REAL NOT NULL,
        new_findings INTEGER NOT NULL,
        dedup_hits INTEGER NOT NULL,
        ioc_nodes INTEGER NOT NULL,
        ts REAL NOT NULL
    );
    """


def get_source_hit_log_schema_sql() -> str:
    """
    Source hit log table.

    CREATE TABLE IF NOT EXISTS source_hit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sprint_id TEXT NOT NULL,
        source_type TEXT NOT NULL,
        hit_rate REAL NOT NULL,
        total_queries INTEGER NOT NULL,
        findings_count INTEGER NOT NULL,
        ts REAL NOT NULL,
        UNIQUE(sprint_id, source_type)
    );
    """
    return """
    CREATE TABLE IF NOT EXISTS source_hit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sprint_id TEXT NOT NULL,
        source_type TEXT NOT NULL,
        hit_rate REAL NOT NULL,
        total_queries INTEGER NOT NULL,
        findings_count INTEGER NOT NULL,
        ts REAL NOT NULL,
        UNIQUE(sprint_id, source_type)
    );
    """


def get_temporal_events_schema_sql() -> str:
    """
    Temporal events table for cross-sprint archaeology.

    CREATE TABLE IF NOT EXISTS temporal_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sprint_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        ts REAL NOT NULL,
        payload_json TEXT NOT NULL,
        source_type TEXT
    );
    """
    return """
    CREATE TABLE IF NOT EXISTS temporal_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sprint_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        ts REAL NOT NULL,
        payload_json TEXT NOT NULL,
        source_type TEXT
    );
    """


def get_shadow_runs_schema_sql() -> str:
    """
    Shadow runs metadata table.

    CREATE TABLE IF NOT EXISTS shadow_runs (
        run_id TEXT PRIMARY KEY,
        started_at REAL NOT NULL,
        ended_at REAL,
        total_fds INTEGER,
        rss_mb REAL,
        sprint_id TEXT
    );
    """
    return """
    CREATE TABLE IF NOT EXISTS shadow_runs (
        run_id TEXT PRIMARY KEY,
        started_at REAL NOT NULL,
        ended_at REAL,
        total_fds INTEGER,
        rss_mb REAL,
        sprint_id TEXT
    );
    """


def get_all_schema_sql() -> list[tuple[str, str]]:
    """
    Return all schema definitions as (name, sql) pairs.

    Used by DuckDBShadowStore.async_initialize_schema() to apply
    all schemas in order.
    """
    return [
        ("canonical_findings", get_canonical_findings_schema_sql()),
        ("sprint_delta", get_sprint_delta_schema_sql()),
        ("sprint_scorecard", get_sprint_scorecard_schema_sql()),
        ("source_hit_log", get_source_hit_log_schema_sql()),
        ("temporal_events", get_temporal_events_schema_sql()),
        ("shadow_runs", get_shadow_runs_schema_sql()),
    ]


def apply_schema(conn, schema_sql: str) -> None:
    """
    Apply a single schema SQL statement to a DuckDB connection.

    Args:
        conn: DuckDB connection object
        schema_sql: SQL statement to execute
    """
    conn.execute(schema_sql)
