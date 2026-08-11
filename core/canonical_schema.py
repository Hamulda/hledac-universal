"""
core/canonical_schema.py — Single Source of Truth for DuckDB canonical_findings schema.

MODERN-20: Unified schema for Arrow batch builder, DuckDB temp tables, and appender.
FIXES: Schema arity mismatch (7 vs 8 columns) that broke DuckDB COPY FROM Arrow.
ISSUE F5-FIX: Extended to 13 columns to include WARC provenance for court-admissible evidence replay.

Canonical Schema (13 columns)
-----------------------------
id              VARCHAR PRIMARY KEY
query           VARCHAR
source_type     VARCHAR
confidence      DOUBLE
ts              DOUBLE
provenance_json TEXT
payload_text    TEXT
claims_json     TEXT   ← Added in migration 0004 (F350M-R)
warc_record_id  VARCHAR  ← ISSUE F5-FIX: WARC Record-ID for replay
warc_path       VARCHAR  ← ISSUE F5-FIX: WARC file path
compressed_offset BIGINT DEFAULT 0  ← ISSUE F5-FIX: Compressed byte offset
compressed_size BIGINT DEFAULT 0    ← ISSUE F5-FIX: Compressed record size
warc_url        VARCHAR  ← ISSUE F5-FIX: Archived URL

Schema Sources
-------------
- DuckDB DDL: knowledge/duckdb_migrations/0001_init_canonical_schema.sql
- Migration:  knowledge/duckdb_migrations/0004_add_claims_json.sql
- Migration:  knowledge/duckdb_migrations/0012_warc_provenance.sql (ISSUE F5-FIX)
- Rust Arrow: rust_extensions/src/arrow_batch_builder.rs

Design
------
This module is the single source of truth. All code paths (Rust builder,
Python appender, COPY FROM Arrow) derive their schema from here.

- Arrow schema uses PyArrow types for Python-side operations
- DuckDB DDL uses DuckDB VARCHAR/DOUBLE/TEXT types
- Rust builder uses Arrow DataType enum (same semantics as PyArrow)

Usage
-----
    from core.canonical_schema import (
        CANONICAL_FINDINGS_COLUMNS,
        CANONICAL_FINDINGS_ARROW_SCHEMA,
        CANONICAL_FINDINGS_DUCKDB_DDL,
        get_canonical_arrow_schema,
        get_duckdb_temp_table_ddl,
    )

M1 8GB Notes
------------
- Schema is a frozenset of strings + lightweight PyArrow schema object
- No memory allocation on import; schema is module-level constant
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Column Definitions
# ---------------------------------------------------------------------------

# Ordered list of column names (matches DuckDB canonical_findings table)
# ISSUE F5-FIX: Extended to 13 columns for WARC provenance
CANONICAL_FINDINGS_COLUMNS: tuple[str, ...] = (
    "id",
    "query",
    "source_type",
    "confidence",
    "ts",
    "provenance_json",
    "payload_text",
    "claims_json",
    # ISSUE F5-FIX: WARC provenance columns for court-admissible evidence replay
    "warc_record_id",
    "warc_path",
    "compressed_offset",
    "compressed_size",
    "warc_url",
)

# Number of columns (13)
CANONICAL_FINDINGS_ARITY: int = len(CANONICAL_FINDINGS_COLUMNS)

# ---------------------------------------------------------------------------
# DuckDB DDL (for temp table creation)
# ---------------------------------------------------------------------------

# DuckDB DDL for creating temp tables with the canonical schema.
# Used by:
#   - query_executor._insert_via_appender()  (appender path)
#   - query_executor.insert_findings_bulk_copy_arrow()  (COPY FROM Arrow)
# ISSUE F5-FIX: Extended to 13 columns for WARC provenance
_CANONICAL_FINDINGS_DUCKDB_DDL_TEMPLATE: str = """
    {table_name} (
        id VARCHAR,
        query VARCHAR,
        source_type VARCHAR,
        confidence DOUBLE,
        ts DOUBLE,
        provenance_json VARCHAR,
        payload_text VARCHAR,
        claims_json VARCHAR,
        -- ISSUE F5-FIX: WARC provenance columns
        warc_record_id VARCHAR,
        warc_path VARCHAR,
        compressed_offset BIGINT DEFAULT 0,
        compressed_size BIGINT DEFAULT 0,
        warc_url VARCHAR
    )
"""


def get_duckdb_temp_table_ddl(table_name: str) -> str:
    """
    Generate DDL for a temp table with canonical_findings schema.

    Args:
        table_name: Name for the temp table (e.g., "_appender_bulk_abc123")

    Returns:
        DDL string for CREATE TEMP TABLE statement
    """
    return _CANONICAL_FINDINGS_DUCKDB_DDL_TEMPLATE.format(table_name=table_name)


# ---------------------------------------------------------------------------
# Arrow Schema (for PyArrow operations)
# ---------------------------------------------------------------------------

try:
    import pyarrow as pa

    # ISSUE F5-FIX: Extended to 13 columns for WARC provenance
    _ARROW_SCHEMA_FIELDS = [
        pa.field("id", pa.string(), nullable=True),
        pa.field("query", pa.string(), nullable=True),
        pa.field("source_type", pa.string(), nullable=True),
        pa.field("confidence", pa.float64(), nullable=True),
        pa.field("ts", pa.float64(), nullable=True),
        pa.field("provenance_json", pa.string(), nullable=True),
        pa.field("payload_text", pa.string(), nullable=True),
        pa.field("claims_json", pa.string(), nullable=True),
        # ISSUE F5-FIX: WARC provenance columns
        pa.field("warc_record_id", pa.string(), nullable=True),
        pa.field("warc_path", pa.string(), nullable=True),
        pa.field("compressed_offset", pa.int64(), nullable=True),
        pa.field("compressed_size", pa.int64(), nullable=True),
        pa.field("warc_url", pa.string(), nullable=True),
    ]

    CANONICAL_FINDINGS_ARROW_SCHEMA: pa.Schema = pa.schema(_ARROW_SCHEMA_FIELDS)

    def get_canonical_arrow_schema() -> pa.Schema:
        """
        Get PyArrow schema for canonical_findings.

        Returns:
            PyArrow Schema with 8 fields matching canonical_findings table

        Note:
            Returns the module-level schema directly. This function exists
            for API symmetry with Rust-side schema access.
        """
        return CANONICAL_FINDINGS_ARROW_SCHEMA

except ImportError:
    # PyArrow not available — provide schema as dict for lazy initialization
    # ISSUE F5-FIX: Extended to 13 columns for WARC provenance
    CANONICAL_FINDINGS_ARROW_SCHEMA: dict = {
        "id": "utf8",
        "query": "utf8",
        "source_type": "utf8",
        "confidence": "float64",
        "ts": "float64",
        "provenance_json": "utf8",
        "payload_text": "utf8",
        "claims_json": "utf8",
        # ISSUE F5-FIX: WARC provenance columns
        "warc_record_id": "utf8",
        "warc_path": "utf8",
        "compressed_offset": "int64",
        "compressed_size": "int64",
        "warc_url": "utf8",
    }

    def get_canonical_arrow_schema() -> dict:
        """Fallback when PyArrow not available."""
        return CANONICAL_FINDINGS_ARROW_SCHEMA


# ---------------------------------------------------------------------------
# Rust Arrow Schema (as Rust-compatible representation)
# ---------------------------------------------------------------------------

# Field definitions for Rust Arrow builder (arrow_batch_builder.rs).
# This is a Python-side representation that documents the Rust schema.
# The Rust code uses these same semantics via arrow crate DataType::Utf8/Float64.
CANONICAL_FINDINGS_RUST_FIELDS: tuple[tuple[str, str], ...] = (
    # (column_name, arrow_type_string)
    # ISSUE F5-FIX: Extended to 13 columns including WARC provenance
    ("id", "utf8"),
    ("query", "utf8"),
    ("source_type", "utf8"),
    ("confidence", "float64"),
    ("ts", "float64"),
    ("provenance_json", "utf8"),
    ("payload_text", "utf8"),
    ("claims_json", "utf8"),
    # ISSUE F5-FIX: WARC provenance columns
    ("warc_record_id", "utf8"),
    ("warc_path", "utf8"),
    ("compressed_offset", "int64"),
    ("compressed_size", "int64"),
    ("warc_url", "utf8"),
)


# ---------------------------------------------------------------------------
# Column Index Constants (for positional access)
# ---------------------------------------------------------------------------

# Zero-based column indices (matching CANONICAL_FINDINGS_COLUMNS order)
# ISSUE F5-FIX: Extended to 13 columns including WARC provenance
_COL_ID: int = 0
_COL_QUERY: int = 1
_COL_SOURCE_TYPE: int = 2
_COL_CONFIDENCE: int = 3
_COL_TS: int = 4
_COL_PROVENANCE_JSON: int = 5
_COL_PAYLOAD_TEXT: int = 6
_COL_CLAIMS_JSON: int = 7
_COL_WARC_RECORD_ID: int = 8
_COL_WARC_PATH: int = 9
_COL_COMPRESSED_OFFSET: int = 10
_COL_COMPRESSED_SIZE: int = 11
_COL_WARC_URL: int = 12


def get_column_index(column_name: str) -> int:
    """
    Get zero-based index for a column name.

    Args:
        column_name: One of CANONICAL_FINDINGS_COLUMNS

    Returns:
        Zero-based column index

    Raises:
        ValueError: If column_name is not in canonical schema
    """
    try:
        return CANONICAL_FINDINGS_COLUMNS.index(column_name)
    except ValueError:
        raise ValueError(
            f"Unknown column '{column_name}'. "
            f"Valid columns: {list(CANONICAL_FINDINGS_COLUMNS)}"
        )


# ---------------------------------------------------------------------------
# Payload Padding Helpers
# ---------------------------------------------------------------------------

def pad_row_to_schema(row: list) -> list:
    """
    Pad a finding row to the canonical 8-column schema.

    Used by appender path where rows may have fewer columns (e.g., 7 columns
    without claims_json). This ensures compatibility with DuckDB temp tables.

    Args:
        row: List representing a finding row (may have 7 or 8 columns)

    Returns:
        8-element list padded with None for missing columns

    Example:
        >>> pad_row_to_schema([id, query, source_type, confidence, ts, prov, payload])
        [id, query, source_type, confidence, ts, prov, payload, None]

        >>> pad_row_to_schema([id, query, source_type, confidence, ts, prov, payload, claims])
        [id, query, source_type, confidence, ts, prov, payload, claims]
    """
    missing = CANONICAL_FINDINGS_ARITY - len(row)
    if missing > 0:
        return row + [None] * missing
    elif missing < 0:
        return row[:CANONICAL_FINDINGS_ARITY]
    return row


# ---------------------------------------------------------------------------
# SQL Templates (for reference and generation)
# ---------------------------------------------------------------------------

# INSERT statement with all 8 columns
CANONICAL_FINDINGS_INSERT_SQL: str = (
    "INSERT INTO canonical_findings "
    "(id, query, source_type, confidence, ts, provenance_json, payload_text, claims_json) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT (id) DO NOTHING"
)

# MERGE statement for Arrow bulk insert
# ISSUE F5-FIX: Extended to 13 columns for WARC provenance
CANONICAL_FINDINGS_MERGE_SQL_TEMPLATE: str = """
MERGE INTO canonical_findings AS target
USING {reg_name} AS source
ON target.id = source.id
WHEN NOT MATCHED THEN INSERT
    (id, query, source_type, confidence, ts, provenance_json, payload_text, claims_json,
     warc_record_id, warc_path, compressed_offset, compressed_size, warc_url)
VALUES
    (source.id, source.query, source.source_type, source.confidence,
     source.ts, source.provenance_json, source.payload_text, source.claims_json,
     source.warc_record_id, source.warc_path, source.compressed_offset,
     source.compressed_size, source.warc_url)
"""


def get_merge_sql(reg_name: str) -> str:
    """
    Generate MERGE SQL for Arrow bulk insert with the given registration name.

    Args:
        reg_name: DuckDB table registration name (from conn.register())

    Returns:
        MERGE SQL statement string
    """
    return CANONICAL_FINDINGS_MERGE_SQL_TEMPLATE.format(reg_name=reg_name)


# ---------------------------------------------------------------------------
# Module Self-Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # ISSUE F5-FIX: Verify arity is now 13 columns (8 original + 5 WARC)
    assert CANONICAL_FINDINGS_ARITY == 13, f"Expected 13 columns, got {CANONICAL_FINDINGS_ARITY}"

    # Verify all expected columns present
    # ISSUE F5-FIX: Expected columns now include WARC provenance
    expected = {
        "id", "query", "source_type", "confidence", "ts", 
        "provenance_json", "payload_text", "claims_json",
        # ISSUE F5-FIX: WARC provenance columns
        "warc_record_id", "warc_path", "compressed_offset", "compressed_size", "warc_url"
    }
    actual = set(CANONICAL_FINDINGS_COLUMNS)
    assert actual == expected, f"Column mismatch: {actual} != {expected}"

    # Verify DuckDB DDL generates correctly
    ddl = get_duckdb_temp_table_ddl("_test_table")
    assert "_test_table" in ddl
    assert "claims_json VARCHAR" in ddl
    # ISSUE F5-FIX: 12 VARCHAR columns + 1 DOUBLE (compressed_offset/compressed_size are BIGINT)
    assert ddl.count("VARCHAR") == 12, f"Expected 12 VARCHAR columns, got {ddl.count('VARCHAR')}"
    assert ddl.count("BIGINT") == 2, f"Expected 2 BIGINT columns (compressed_offset, compressed_size), got {ddl.count('BIGINT')}"

    # Verify padding
    row7 = ["a", "b", "c", 1.0, 2.0, "d", "e"]  # missing claims_json
    padded = pad_row_to_schema(row7)
    assert len(padded) == 8, f"Padding failed: {len(padded)} != 8"
    assert padded[-1] is None, "Missing column should be padded with None"

    print("✓ canonical_schema.py self-test passed")
    print(f"  Columns: {CANONICAL_FINDINGS_COLUMNS}")
    print(f"  Arity: {CANONICAL_FINDINGS_ARITY}")
