"""DuckDB Shadow Analytics -- canonical sprint facts store.

ROLE: Canonical store for sprint-level facts and derived analytics.

See :ref:`duckdb-store-internals` for architecture overview, F360 extracted
components, and the 3-tier facts hierarchy (Sprint Facts / Shadow Findings /
Cross-Sprint). The 15 deprecated graph methods are now delegated to
DuckDBGraphAttachment.
"""

from __future__ import annotations

import asyncio
import atexit
import functools
import sys
import time
import weakref

from hledac.universal.core.env_config import ENV
from hledac.universal.runtime.protocols.cleanup_protocol import shutdown_aclose
from hledac.universal.utils.async_helpers import safe_create_task, safe_wait_for
from hledac.universal.knowledge.duckdb_migrator import SchemaMigrator

# OTEL instrumentation — importlib chain, lookup cached once
@functools.lru_cache(maxsize=1)
def _otel_instrumented_factory() -> Any:
    from importlib import import_module

    try:
        return import_module("otel._instrumentation").instrumented
    except ImportError:
        return import_module("hledac.universal.otel._instrumentation").instrumented


_otel_instrumented = _otel_instrumented_factory()

# instrument_duckdb_connection — importlib chain, lookup cached once
@functools.lru_cache(maxsize=1)
def _instrument_duckdb_connection_factory() -> Any:
    from importlib import import_module

    try:
        return import_module("runtime._telemetry_setup").instrument_duckdb_connection
    except ImportError:
        try:
            return import_module("hledac.universal.runtime._telemetry_setup").instrument_duckdb_connection
        except ImportError:
            return None


instrument_duckdb_connection = _instrument_duckdb_connection_factory()

# F360M-R: Extracted DuckDBQueryExecutor to knowledge/query_executor.py
from hledac.universal.knowledge.query_executor import DuckDBQueryExecutor

import datetime as _dt
import logging
import os
import time as _time
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from hledac.universal.transport.circuit_breaker import CBState

# SourceType — strict import
try:
    from hledac.universal.utils.source_types import SourceType, canonical_source_type
except ImportError:
    SourceType = None  # type: ignore[assignment,misc]
    canonical_source_type = None  # type: ignore[assignment,misc]

# BoundedTaskSet — strict import
try:
    from hledac.universal.utils.async_utils import BoundedTaskSet
except ImportError:
    BoundedTaskSet = None  # type: ignore[assignment,misc]

import msgspec

# orjson — strict import with fallback
try:
    import orjson as _orjson_mod
    _HAS_ORJSON: bool = True
    _ORJSON_DECODER = _orjson_mod.loads
except ImportError:
    _orjson_mod = None  # type: ignore[assignment]
    _HAS_ORJSON = False

    import json as _stdjson

    def _ORJSON_DECODER(b: Any) -> Any:
        return _stdjson.loads(b.decode("utf-8") if isinstance(b, (bytes, bytearray)) else b)

# lmdb — strict import with fallback (used by _DuckDBQueryCache)
try:
    import lmdb
    _HAS_LMDB: bool = True
except ImportError:
    lmdb = None  # type: ignore[assignment]
    _HAS_LMDB = False


# ---------------------------------------------------------------------------
# SEC-02: DuckDB file permission hardening
# ---------------------------------------------------------------------------

def _harden_duckdb_permissions(db_path: Path) -> None:
    """
    SEC-02: Enforce 0o600 on DuckDB data files.

    Called immediately after duckdb.connect() creates or opens a file-based
    database. DuckDB creates multiple files (.duckdb, .wal) so we glob
    for all extensions. Fails silently if chmod is not supported.
    """
    import os
    import stat as _stat

    if db_path is None:
        return
    # If db_path is a directory (WAL temp), glob all files inside
    if db_path.is_dir():
        files = list(db_path.glob("*"))
    else:
        files = list(db_path.parent.glob(f"{db_path.stem}*"))
    for f in files:
        try:
            os.chmod(f, _stat.S_IRUSR | _stat.S_IWUSR)  # 0o600
        except OSError:
            pass


if TYPE_CHECKING:
    import polars as pl

    # F360M-R: DuckDBQueryExecutor extracted to knowledge/query_executor.py
    # DuckDBQueryExecutor is already available at runtime (line 55) as an alias
    # _DuckDBQueryExecutor = DuckDBQueryExecutor (line 2799). For type checking,
    # we reference DuckDBQueryExecutor directly since it's the same class.
    from hledac.universal.knowledge.query_executor import DuckDBQueryExecutor

    class _DuckDBShadowStore:
        """Stubs for dynamic attributes set via object.__setattr__ in DuckDBShadowStore."""

        _query_executor: DuckDBQueryExecutor
        _graph_store: Any


def _provenance_to_arrow_native(provenance: tuple[str, ...]) -> bytes | None:
    """
    P1-11: Single canonical encode_for_arrow call — no triple import, no fallback loop.

    Arrow ``pa.array(bytes, type=pa.string())`` ingests bytes directly — zero-copy.
    ``msgspec`` encodes ``tuple`` natively, no ``list()`` conversion needed.

    Returns:
        - bytes: canonical encode_for_arrow() result (Arrow-compatible, zero-copy)
        - None: for empty/None provenance (SQL NULL / Arrow null)
    """
    return encode_for_arrow(provenance)


def _json_dumps_str(value: Any) -> str:
    """P1-11: Single canonical encode for DuckDB VARCHAR parameters.

    DuckDB requires ``str`` for VARCHAR columns. Uses ``encode()`` (pool-backed
    ``msgspec``) then ``.decode()`` — single allocation, no per-call Encoder
    instantiation on the hot path. Fallback to ``orjson`` for msgspec-incompatible
    types (sets, custom objects).

    Used at: DHT metadata INSERT (L1950-1951).
    """
    if value is None:
        return "{}"
    return _msgspec_encode(value).decode("utf-8")


def _json_loads_flexible(raw: Any) -> Any:
    """Sprint F26X: Single-shot JSON decode that handles str | bytes | None | empty.

    Replaces the 6 hand-rolled ``orjson.loads(r[N]) if r[N] else {}`` patterns
    that previously littered this module (lines 3038-3041, 4452-4455).
    """
    if raw is None or raw == b"" or raw == "":
        return {}
    if isinstance(raw, (bytes, bytearray)):
        return _ORJSON_DECODER(raw)
    if isinstance(raw, str):
        # orjson.loads accepts str directly — no encode/decode roundtrip needed
        if _HAS_ORJSON and _orjson_mod is not None:
            return _orjson_mod.loads(raw)
        import json as _stdjson

        return _stdjson.loads(raw)
    return raw


# TargetProfileSummary — strict import with inline fallback
try:
    from hledac.universal.knowledge.sprint_diff_engine import TargetProfileSummary
except ImportError:
    TargetProfileSummary = None  # type: ignore[assignment,misc]


def _get_TargetProfileSummary():
    """Lazy loader for TargetProfileSummary with inline fallback."""
    if TargetProfileSummary is not None:
        return TargetProfileSummary
    from dataclasses import dataclass

    class TargetProfileSummary(msgspec.Struct, gc=False):
        """F350M-R: gc=False for M1 8GB."""
        target_id: str = ""
        first_seen: float = 0.0
        last_seen: float = 0.0
        cumulative_finding_count: int = 0
        entity_summary_json: str = "{}"

    return TargetProfileSummary


# TargetMemory — strict import
try:
    from hledac.universal.knowledge.target_memory import TargetMemory, TargetMemoryUpdate
except ImportError:
    TargetMemory = None  # type: ignore[assignment,misc]
    TargetMemoryUpdate = None  # type: ignore[assignment,misc]


def _get_TargetMemory():
    """Lazy loader for TargetMemory."""
    return TargetMemory


def _get_TargetMemoryUpdate():
    """Lazy loader for TargetMemoryUpdate."""
    return TargetMemoryUpdate


__all__ = [
    "DuckDBShadowStore",
    "ActivationResult",
    "ReplayResult",
    "CanonicalFinding",
    "FindingQualityDecision",
    "QualityRejectionRecord",
    "_normalize_osint_url",
    "ParquetHistoryReader",
    "RemoteParquetSource",
    "export_findings_to_parquet",
]
from hledac.universal.tools.file_cache import apply_nocache_to_path
from hledac.universal.tools.file_cache import madv_nocache_on_path  # R-03: was madv_free_reusable_on_path (broken: madvise NULL+0)
from hledac.universal.utils.async_helpers import parallel

from .dedup import DedupManager
from .sprint_boundary import SprintBoundaryCoordinator

# P4-2: IOC buffering chunk size for parallel flush
_IOC_CHUNK: int = 128  # per-chunk size for parallel IOC buffering

# Lazy imports from quality_assessment to avoid circular dependency
# duckdb_store ↔ quality_assessment — both use TYPE_CHECKING to break circular import
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .quality_assessment import (
        QualityAssessmentState,
        QualityRejectionRecord,
    )


def _get_QualityAssessmentState():
    """Lazy loader for QualityAssessmentState — called at instance init time."""
    from .quality_assessment import QualityAssessmentState

    return QualityAssessmentState

# Rust backend — strict import
try:
    from hledac.universal.core.rust_backend import rust
except ImportError:
    try:
        from hledac.universal.core.rust_backend import rust
    except ImportError:
        rust = None


def _is_quality_gate_available() -> bool:
    """Check if Rust quality gate is available at runtime."""
    return rust is not None and rust.is_available and (rust.quality is not None)


_QUALITY_GATE_BATCH_AVAILABLE = _is_quality_gate_available()
if _QUALITY_GATE_BATCH_AVAILABLE:
    # ISSUE-024: Wire Rust batch functions — these were None even when Rust was available
    _rust_batch_entropy = rust.quality.batch_entropy
    _rust_batch_dedup_fingerprints = rust.quality.batch_dedup_fingerprints
    _rust_batch_normalize_quality_text = rust.quality.batch_normalize_quality_text
    _rust_batch_url_fingerprints = rust.quality.batch_url_fingerprints
    _rust_dedup_fingerprint = rust.quality.dedup_fingerprint
    _rust_url_fingerprint_b2b = rust.quality.url_fingerprint
    _rust_normalize_quality_text = rust.quality.normalize_quality_text
else:
    _rust_batch_entropy = None
    _rust_batch_dedup_fingerprints = None
    _rust_batch_normalize_quality_text = None
    _rust_batch_url_fingerprints = None
    _rust_dedup_fingerprint = None
    _rust_url_fingerprint_b2b = None
    _rust_normalize_quality_text = None

# Rust assess_findings_quality_batch — strict import
try:
    from hledac_rust_extensions import assess_findings_quality_batch as _rust_assess_quality_batch_func
except ImportError:
    _rust_assess_quality_batch_func = None

_RUST_ASSESS_QUALITY_BATCH_AVAILABLE = _rust_assess_quality_batch_func is not None


def _get_rust_assess_quality_batch():
    return _rust_assess_quality_batch_func


# Rust build_arrow_batch_from_findings — strict import
try:
    from hledac_rust_extensions import build_arrow_batch_from_findings as _rust_arrow_func
    from hledac_rust_extensions import build_record_batch_from_structs as _rust_record_batch_cols_func
except ImportError:
    _rust_arrow_func = None
    _rust_record_batch_cols_func = None

_RUST_ARROW_AVAILABLE = _rust_arrow_func is not None
_RUST_RECORD_BATCH_COLS_AVAILABLE = _rust_record_batch_cols_func is not None


def _get_rust_build_arrow_batch():
    """Lazy getter for Rust Arrow batch builder."""
    return _rust_arrow_func


def _get_rust_record_batch_from_structs():
    """Lazy getter for Rust zero-copy column-path Arrow batch builder (P4-7)."""
    return _rust_record_batch_cols_func


# Rust batch_ioc_extract_unified — strict import
try:
    from hledac_rust_extensions import batch_ioc_extract_unified as _rust_batch_ioc_extract_func
except ImportError:
    _rust_batch_ioc_extract_func = None

_IOC_EXTRACT_BATCH_AVAILABLE = _rust_batch_ioc_extract_func is not None


def _get_rust_batch_ioc_extract():
    return _rust_batch_ioc_extract_func


# Rust batch_ioc_extract_unified_python — strict import (zero-copy)
try:
    from hledac_rust_extensions import batch_ioc_extract_unified_python as _rust_batch_ioc_extract_python_func
except ImportError:
    _rust_batch_ioc_extract_python_func = None

_IOC_EXTRACT_PYTHON_ZERO_COPY_AVAILABLE = _rust_batch_ioc_extract_python_func is not None


def _get_rust_batch_ioc_extract_python():
    return _rust_batch_ioc_extract_python_func


# R18 FIX: parquet_reader functions were imported but NEVER called.
# Parquet row-group pagination was prepared but never wired in duckdb_store.py.
# These remain in rust_extensions/src/parquet_reader.rs for future use if needed.
# _RUST_PARQUET_AVAILABLE = False  # hardcoded — no callers exist

def extract_iocs_from_texts(texts: list[str]):
    """
    Extract IOCs from a list of texts using Rust's batch_ioc_extract_unified.
    Yields (ioc_value, ioc_type) tuples lazily — no intermediate flat list allocated.

    PAR-1 P2 / F266-2.3: Three-tier fallback chain for zero-copy memory:

        Tier 1 — batch_ioc_extract_unified_python (F266-2.3):
            Zero-copy path. Rust writes results directly into Python heap
            via PyList::append / PyTuple::new — no intermediate Rust
            Vec<(String,String)> that Python must copy.

        Tier 2 — batch_ioc_extract_unified (rayon Vec return):
            Original path. Rust collects results in Vec<Vec<…>> then
            PyO3 auto-converts to Python list.  Tuples are copied by
            PyO3 at the GIL boundary (PyTuple::new for each element).

        Tier 3 — pure Python ioc_qs.extract_iocs_from_text:
            Slowest; used only when Rust is unavailable.

    Args:
        texts: List of text strings to scan for IOCs.

    Yields:
        Tuples of (ioc_value, ioc_type) from all texts combined.
        IOC types: ipv4, ipv6, domain, md5, sha1, sha256, email, cve.
    """
    if not texts:
        return
    if _IOC_EXTRACT_PYTHON_ZERO_COPY_AVAILABLE and _get_rust_batch_ioc_extract_python() is not None:
        try:
            batch_results: list[list[tuple[str, str]]] = _get_rust_batch_ioc_extract_python()(texts)
            for text_result in batch_results:
                yield from text_result
            return
        except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
            pass
    if _IOC_EXTRACT_BATCH_AVAILABLE and _get_rust_batch_ioc_extract() is not None:
        try:
            batch_results: list[list[tuple[str, str]]] = _get_rust_batch_ioc_extract()(texts)
            for text_result in batch_results:
                yield from text_result
            return
        except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
            pass
    try:
        from hledac.universal.intelligence import ioc_qs

        for text in texts:
            yield from ioc_qs.extract_iocs_from_text(text)
    except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
        return


class RemoteParquetSource:
    """
    DuckDB-native remote Parquet reader via ATTACH — no local Parquet copy needed.

    P4-8: DuckDB 1.5+ supports native Parquet attachment for S3/HTTPS/Postgres.
    Uses DuckDB's own filter pushdown, column pruning, and parallel execution.

    Supported sources:
      - S3: s3://bucket/path/file.parquet
      - HTTPS: https://host/path/file.parquet
      - Azure: az://bucket/path/file.parquet
      - GCS: gs://bucket/path/file.parquet
      - Postgres: postgres://host/db (via DuckDB postgres scanner)

    M1 8GB safe: DuckDB manages memory internally via hard_memory_limit.
    Zero-copy: Arrow Table export from DuckDB via fetchmany + Arrow batch.

    Usage:
        # S3 with credentials
        src = RemoteParquetSource(
            "s3://mybucket/findings/*.parquet",
            source_type="s3",
            credentials={"key_id": "AKIA...", "secret": "..."}
        )
        for batch in src.iter_batches(batch_size=50_000):
            df = pl.from_arrow(batch)  # zero-copy
            process(df)

        # HTTPS public file (no credentials)
        src = RemoteParquetSource(
            "https://example.com/data.parquet",
            source_type="https"
        )

        # Filter pushdown via SQL WHERE (DuckDB optimizes)
        src = RemoteParquetSource("s3://bucket/data.parquet", sql_where="ts > 1700000000")

    ATTACH path (DuckDB 1.5+):
        CREATE SECRET (TYPE S3, KEY_ID '...', SECRET '...')
        ATTACH 's3://bucket/file.parquet' AS remote (TYPE PARQUET)
        SELECT * FROM remote.findings WHERE ts > 1700000000
    """

    __slots__ = tuple((
        "uri", "source_type", "credentials", "alias", "columns",
        "batch_size", "sql_where", "_conn", "_total_rows",
    ))

    # E-33: Whitelisted URI schemes for remote Parquet sources
    _URI_SCHEMES: frozenset[str] = frozenset(("s3", "https", "az", "gs", "postgres"))

    def __init__(
        self,
        uri: str,
        source_type: str = "s3",
        credentials: dict[str, str] | None = None,
        alias: str = "remote",
        columns: list[str] | None = None,
        batch_size: int = 50000,
        sql_where: str | None = None,
    ) -> None:
        # E-33: Validate URI scheme before storing — reject any uri with quote chars
        if not isinstance(uri, str) or "'" in uri or '"' in uri:
            raise ValueError(f"[RemoteParquet] URI contains illegal characters (quote): {uri!r}")
        # E-33: alias must be safe identifier — alphanumeric + underscore only
        if not isinstance(alias, str) or not alias.isidentifier():
            raise ValueError(f"[RemoteParquet] alias must be a valid SQL identifier, got: {alias!r}")
        # E-33: sql_where must not contain dangerous patterns (nested queries, stacked statements)
        if sql_where is not None:
            _forbidden = (";", "--", "/*", "*/", "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE")
            upper = sql_where.upper()
            if any(kw in upper for kw in _forbidden):
                raise ValueError(f"[RemoteParquet] sql_where contains forbidden SQL: {sql_where!r}")
        self.uri = uri
        self.source_type = source_type
        self.credentials = credentials or {}
        self.alias = alias
        self.columns = columns
        self.batch_size = min(batch_size, 100000)
        self.sql_where = sql_where
        self._conn: Any | None = None
        self._total_rows: int | None = None

    def _get_duckdb(self):
        return _get_duckdb()

    def _ensure_connection(self) -> None:
        """Lazily create DuckDB connection and ATTACH the remote source."""
        if self._conn is not None:
            return
        duckdb = self._get_duckdb()
        self._conn = duckdb.connect(":memory:")
        self._configure_conn(self._conn)
        self._attach_source(self._conn)

    async def _ensure_connection_async(self) -> None:
        """
        Async version — runs duckdb.connect() in a thread pool to avoid blocking the event loop.

        S-07: sync duckdb.connect inside async context freezes the loop on M1 8GB.
        DuckDB connect includes native memory allocation and initialisation that can take
        50-200 ms — too long to block the event loop.
        """
        if self._conn is not None:
            return
        duckdb = self._get_duckdb()
        # Run blocking connect + config + attach on thread pool (M1-safe)
        self._conn = await asyncio.to_thread(duckdb.connect, ":memory:")
        await asyncio.to_thread(self._configure_conn, self._conn)
        await asyncio.to_thread(self._attach_source, self._conn)

    def _configure_conn(self, conn: Any) -> None:
        """Apply M1 8GB-safe DuckDB settings."""
        try:
            conn.execute("PRAGMA threads = 2")
            conn.execute("PRAGMA enable_progress_bar = false")
            conn.execute("SET memory_limit = '2GB'")
            conn.execute("PRAGMA hard_memory_limit = '1GB'")
            conn.execute("SET preserve_insertion_order = false")
        except Exception:  # noqa: BLE001 — best-effort; DuckDB settings; non-critical
            pass

    def _attach_source(self, conn: Any) -> None:
        """ATTACH remote source based on type. Failures are logged and recovered."""
        # E-33: Whitelist source_type to prevent ATTACH to arbitrary paths
        if self.source_type not in self._URI_SCHEMES:
            raise ValueError(f"[RemoteParquet] Unsupported source_type={self.source_type!r}; must be one of {sorted(self._URI_SCHEMES)}")
        try:
            if self.source_type == "s3":
                self._attach_s3(conn)
            elif self.source_type == "https":
                self._attach_https(conn)
            elif self.source_type == "azure":
                self._attach_azure(conn)
            elif self.source_type == "gcs":
                self._attach_gcs(conn)
            elif self.source_type == "postgres":
                self._attach_postgres(conn)
        except Exception as e:  # noqa: BLE001 — best-effort; remote attach; non-critical
            logger.warning(f"[RemoteParquet] Failed to ATTACH {self.uri}: {e}")

    def _attach_s3(self, conn: Any) -> None:
        """Attach S3 Parquet via CREATE SECRET + ATTACH."""
        duckdb = self._get_duckdb()
        secret_name = f"_s3_secret_{id(self)}"
        key_id = self.credentials.get("key_id", "").replace("'", "''")
        secret = self.credentials.get("secret", "").replace("'", "''")
        region = self.credentials.get("region", "us-east-1").replace("'", "''")
        if key_id and secret:
            conn.execute(f"""
                CREATE SECRET {secret_name} (
                    TYPE S3,
                    KEY_ID {key_id},
                    SECRET {secret},
                    REGION {region}
                )
            """)
        safe_uri = self.uri.replace("'", "''")
        conn.execute(f"ATTACH {safe_uri} AS {self.alias} (TYPE PARQUET)")

    def _attach_https(self, conn: Any) -> None:
        """Attach HTTPS Parquet — DuckDB handles via httpfs extension."""
        try:
            conn.execute("LOAD httpfs")
        except Exception:  # noqa: BLE001 — best-effort; httpfs load; non-critical
            pass
        duckdb = self._get_duckdb()
        safe_uri = self.uri.replace("'", "''")
        conn.execute(f"ATTACH {safe_uri} AS {self.alias} (TYPE PARQUET)")

    def _attach_azure(self, conn: Any) -> None:
        """Attach Azure Blob Parquet via CREATE SECRET + ATTACH."""
        duckdb = self._get_duckdb()
        secret_name = f"_azure_secret_{id(self)}"
        account = self.credentials.get("account", "").replace("'", "''")
        access_key = self.credentials.get("access_key", "").replace("'", "''")
        if account and access_key:
            conn.execute(f"""
                CREATE SECRET {secret_name} (
                    TYPE AZURE,
                    ACCOUNT_NAME {account},
                    ACCOUNT_KEY {access_key}
                )
            """)
        safe_uri = self.uri.replace("'", "''")
        conn.execute(f"ATTACH {safe_uri} AS {self.alias} (TYPE PARQUET)")

    def _attach_gcs(self, conn: Any) -> None:
        """Attach GCS Parquet via CREATE SECRET + ATTACH."""
        secret_name = f"_gcs_secret_{id(self)}"
        credentials_json = self.credentials.get("credentials_json", "").replace("'", "''")
        if credentials_json:
            conn.execute(f"""
                CREATE SECRET {secret_name} (
                    TYPE GCS,
                    CREDENTIALS_JSON {credentials_json}
                )
            """)
        safe_uri = self.uri.replace("'", "''")
        conn.execute(f"ATTACH {safe_uri} AS {self.alias} (TYPE PARQUET)")

    def _attach_postgres(self, conn: Any) -> None:
        """Attach Postgres table via DuckDB postgres scanner."""
        try:
            conn.execute("LOAD postgres")
        except Exception:  # noqa: BLE001 — best-effort; postgres load; non-critical
            pass
        # E-33: Use urllib.parse for safe URL construction — no manual string formatting
        from urllib.parse import quote_plus
        host = quote_plus(self.credentials.get("host", "localhost"))
        port = quote_plus(self.credentials.get("port", "5432"))
        db = quote_plus(self.credentials.get("database", "postgres"))
        user = quote_plus(self.credentials.get("user", ""))
        password = quote_plus(self.credentials.get("password", ""))
        # Escape single quotes in all credentials — prevents SQL injection in ATTACH URL
        conn.execute(f"""
            ATTACH 'postgres://{user}:{password}@{host}:{port}/{db}'
            AS {self.alias} (TYPE POSTGRES)
        """)

    def _build_sql(self) -> str:
        """Build SQL query with optional column selection and WHERE clause."""
        cols = ", ".join(self.columns) if self.columns else "*"
        sql = f"SELECT {cols} FROM {self.alias}"
        if self.sql_where:
            sql += f" WHERE {self.sql_where}"
        return sql

    def _count_rows(self) -> int:
        """Get total row count via COUNT(*) pushdown."""
        self._ensure_connection()
        try:
            cols = ", ".join(self.columns) if self.columns else "*"
            count_sql = f"SELECT COUNT(*) FROM {self.alias}"
            if self.sql_where:
                count_sql += f" WHERE {self.sql_where}"
            result = self._conn.execute(count_sql).fetchone()
            return result[0] if result else 0
        except Exception:  # noqa: BLE001 — best-effort; row count; non-critical
            return 0

    @property
    def total_rows(self) -> int:
        """Return total row count (DuckDB COUNT(*) pushdown)."""
        if self._total_rows is None:
            self._total_rows = self._count_rows()
        return self._total_rows

    def iter_batches(self) -> Iterator:
        """
        Iterate over remote Parquet as Arrow RecordBatch objects via DuckDB.

        Yields:
            pyarrow.RecordBatch — zero-copy via DuckDB Arrow export.
            Caller converts to Polars via pl.from_arrow(batch).
        """
        self._ensure_connection()
        try:
            import pyarrow as pa

            sql = self._build_sql()
            result = self._conn.execute(sql)
            try:
                # Fast path: DuckDB can return Arrow table directly — O(1) allocation
                batches = result.fetch_arrow_batch(self.batch_size)
                while len(batches) > 0:
                    for batch in batches:
                        yield batch
                    batches = result.fetch_arrow_batch(self.batch_size)
                return
            except AttributeError:
                pass  # Fall through to tuple-based path
            # Fallback: convert tuples to Arrow via from_pydict (single pass per batch)
            while True:
                rows = result.fetchmany(self.batch_size)
                if not rows:
                    break
                columns = [desc[0] for desc in result.description]
                col_arrays = [[row[i] for row in rows] for i in range(len(columns))]
                table = pa.Table.from_pydict(dict(zip(columns, col_arrays)))
                yield from table.to_batches(max_chunksize=self.batch_size)
        except Exception as e:  # noqa: BLE001 — best-effort; remote read; non-critical
            logger.warning(f"[RemoteParquet] iter_batches error: {e}")
            return

    def to_polars_lazy(self):
        """
        Return Polars LazyFrame via DuckDB scan.

        DuckDB's Parquet scanner handles filter pushdown and column pruning
        natively — no PyArrow intermediate.

        Returns:
            polars.LazyFrame — collect() to execute.
        """
        self._ensure_connection()
        try:
            import polars as pl

            sql = self._build_sql()
            lf = self._conn.execute(sql).pl()
            return lf.lazy()
        except ImportError:
            raise ImportError("Polars not installed: pip install polars")
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB scan; non-critical
            logger.warning(f"[RemoteParquet] to_polars_lazy error: {e}")
            return None

    def read_table(self):
        """
        Read entire remote Parquet as Arrow Table.
        WARNING: may OOM for large remote files — prefer iter_batches().

        Returns:
            pyarrow.Table or None on error.
        """
        self._ensure_connection()
        try:
            import pyarrow as pa

            sql = self._build_sql()
            result = self._conn.execute(sql)
            batches = list(result.fetchmany(self.batch_size * 10))  # safety cap
            if not batches:
                return None
            columns = [desc[0] for desc in result.description]
            col_arrays = [[row[i] for row in batches] for i in range(len(columns))]
            return pa.Table.from_pydict(dict(zip(columns, col_arrays)))
        except Exception:  # noqa: BLE001 — best-effort; remote read; non-critical
            return None

    async def _count_rows_async(self) -> int:
        """
        Async version — runs DuckDB COUNT(*) on thread pool.

        S-07: prevents event loop blocking during connection setup + query.
        """
        await self._ensure_connection_async()
        def _sync_count():
            try:
                cols = ", ".join(self.columns) if self.columns else "*"
                count_sql = f"SELECT COUNT(*) FROM {self.alias}"
                if self.sql_where:
                    count_sql += f" WHERE {self.sql_where}"
                result = self._conn.execute(count_sql).fetchone()
                return result[0] if result else 0
            except Exception:  # noqa: BLE001 — best-effort; row count; non-critical
                return 0
        return await asyncio.to_thread(_sync_count)

    async def iter_batches_async(self):
        """
        Async iterator — runs DuckDB batch iteration on a thread pool.

        S-07: prevents event loop blocking during connection setup + remote read.
        S1-06 FIX: producer thread uses synchronous bounded put with backpressure
        instead of fire-and-forget call_soon_threadsafe + put_nowait (which
        silently drops on full and can orphan the consumer).

        Yields:
            pyarrow.RecordBatch — zero-copy via DuckDB Arrow export.
        """
        await self._ensure_connection_async()

        import pyarrow as pa

        def _sync_iter():
            """Run full iteration synchronously on thread pool."""
            sql = self._build_sql()
            result = self._conn.execute(sql)
            try:
                batches = result.fetch_arrow_batch(self.batch_size)
                while len(batches) > 0:
                    yield from batches
                    batches = result.fetch_arrow_batch(self.batch_size)
                return
            except AttributeError:
                pass  # Fall through to tuple-based path
            while True:
                rows = result.fetchmany(self.batch_size)
                if not rows:
                    break
                columns = [desc[0] for desc in result.description]
                col_arrays = [[row[i] for row in rows] for i in range(len(columns))]
                table = pa.Table.from_pydict(dict(zip(columns, col_arrays)))
                yield from table.to_batches(max_chunksize=self.batch_size)

        # S1-06 FIX: use a synchronous bounded queue in the producer thread
        # with backpressure instead of call_soon_threadsafe + put_nowait (silent drop).
        # BoundedQueueBlock is reentrant-safe for the single-producer pattern here.
        from concurrent.futures import ThreadPoolExecutor
        from threading import Condition, Lock

        _QUEUE_MAXSIZE = 16  # S1-06 FIX: was 4 — tiny queue caused premature iterator termination on slow consumers. 16 gives 4× headroom for M1 8GB while staying bounded.
        _QUEUE_PUT_TIMEOUT_S = 5.0

        class _BoundedQueueBlock:
            """Thread-safe synchronous bounded queue with blocking put and non-blocking get.

            S1-06 FIX: replaces call_soon_threadsafe + put_nowait pattern which
            silently drops items on full and can orphan the consumer. This version
            applies backpressure: put() blocks up to timeout_s, then raises so the
            producer can signal stall rather than silently lose data.
            """

            __slots__ = ('_lock', '_cv', '_queue', '_maxsize', '_closed')

            def __init__(self, maxsize: int = _QUEUE_MAXSIZE) -> None:
                self._lock = Lock()
                self._cv = Condition(self._lock)
                self._queue: list = []
                self._maxsize = maxsize
                self._closed = False

            def put(self, item, timeout_s: float = _QUEUE_PUT_TIMEOUT_S) -> bool:
                """Blocking put with timeout. Returns True on success, False on timeout."""
                deadline = time.monotonic() + timeout_s
                with self._cv:
                    while len(self._queue) >= self._maxsize and not self._closed:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            return False  # Backpressure signal: queue saturated
                        if not self._cv.wait(timeout=remaining):
                            # Spurious wakeup or timeout expired
                            if len(self._queue) >= self._maxsize and not self._closed:
                                return False
                    if self._closed:
                        return False
                    self._queue.append(item)
                    self._cv.notify()
                    return True

            def get(self):
                """Non-blocking get. Raises IndexError if empty."""
                with self._cv:
                    if not self._queue:
                        raise IndexError("empty")
                    item = self._queue.pop(0)
                    self._cv.notify()
                    return item

            def close(self) -> None:
                with self._cv:
                    self._closed = True
                    self._cv.notify_all()

            @property
            def closed(self) -> bool:
                return self._closed

        loop = asyncio.get_running_loop()
        bqueue = _BoundedQueueBlock(maxsize=_QUEUE_MAXSIZE)
        producer_done = False

        def _producer():
            nonlocal producer_done
            try:
                for batch in _sync_iter():
                    if bqueue.closed:
                        break
                    if not bqueue.put(batch, timeout_s=_QUEUE_PUT_TIMEOUT_S):
                        # S1-06: backpressure — queue saturated beyond timeout.
                        # Log and signal end-of-stream rather than silently dropping.
                        logger.warning(
                            "[RemoteParquet] iter_batches_async: queue full "
                            f"({_QUEUE_MAXSIZE}), producer backpressured — terminating early"
                        )
                        break
                # Signal end-of-stream
                bqueue.close()
            except Exception as e:
                logger.warning(f"[RemoteParquet] iter_batches_async producer error: {e}")
            finally:
                producer_done = True

        # Run producer on thread pool; consumer runs on asyncio event loop.
        # Using to_thread ensures the producer is tracked and the thread
        # is joined when the consumer exits or is cancelled.
        prod_thread = threading.Thread(target=_producer, daemon=True)
        prod_thread.start()

        try:
            while True:
                # Poll with timeout so we can detect a stalled/dead producer.
                batch = await asyncio.wait_for(
                    loop.run_in_executor(None, bqueue.get),
                    timeout=_QUEUE_PUT_TIMEOUT_S * 2,
                )
                yield batch
        except asyncio.TimeoutError:
            # S1-06: producer may have died silently (OOM kill, exception in thread).
            # Check if producer exited without closing cleanly.
            if not producer_done:
                logger.warning(
                    "[RemoteParquet] iter_batches_async: consumer timeout waiting "
                    "for batch — producer thread may have died. Terminating iterator."
                )
            # If producer is done and queue empty, normal exit (consumer loop will
            # exit via the break above once get() raises IndexError).
            raise StopAsyncIteration from None
        except IndexError:
            # Queue is drained and closed — normal end of stream.
            raise StopAsyncIteration from None
        except asyncio.CancelledError:
            # S1-06: cancelled — wake producer so it can exit.
            bqueue.close()
            raise

    async def read_table_async(self):
        """
        Async version — reads entire remote Parquet as Arrow Table on thread pool.

        S-07: prevents event loop blocking during connection setup + remote read.
        WARNING: may OOM for large remote files — prefer iter_batches_async().

        Returns:
            pyarrow.Table or None on error.
        """
        await self._ensure_connection_async()

        def _sync_read():
            try:
                import pyarrow as pa

                sql = self._build_sql()
                result = self._conn.execute(sql)
                batches = list(result.fetchmany(self.batch_size * 10))  # safety cap
                if not batches:
                    return None
                columns = [desc[0] for desc in result.description]
                col_arrays = [[row[i] for row in batches] for i in range(len(columns))]
                return pa.Table.from_pydict(dict(zip(columns, col_arrays)))
            except Exception:  # noqa: BLE001 — best-effort; remote read; non-critical
                return None

        return await asyncio.to_thread(_sync_read)

    async def to_polars_lazy_async(self):
        """
        Async version — returns Polars LazyFrame via DuckDB scan on thread pool.

        S-07: prevents event loop blocking during connection setup + DuckDB scan.

        Returns:
            polars.LazyFrame or None on error.
        """
        await self._ensure_connection_async()

        def _sync_scan():
            try:
                import polars as pl

                sql = self._build_sql()
                return self._conn.execute(sql).pl().lazy()
            except ImportError:
                raise ImportError("Polars not installed: pip install polars")
            except Exception as e:  # noqa: BLE001 — best-effort; DuckDB scan; non-critical
                logger.warning(f"[RemoteParquet] to_polars_lazy_async error: {e}")
                return None

        return await asyncio.to_thread(_sync_scan)

    def close(self) -> None:
        """Close DuckDB connection."""
        # F350M-R-A3: Set BACKGROUND QoS for close — connection close is I/O bound
        try:
            from hledac.universal.tools.file_cache import apply_thread_qos, QOS_CLASS_BACKGROUND
            apply_thread_qos(QOS_CLASS_BACKGROUND)
        except Exception:  # noqa: BLE001 — best-effort; QoS hinting; non-critical
            pass
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 — best-effort; connection close; non-critical
                pass
            self._conn = None
        # F350M-R-A1: Unregister from madvise registry
        DuckDBShadowStore._instances.discard(self)
        # Restore USER_INITIATED QoS after close
        try:
            from hledac.universal.tools.file_cache import apply_thread_qos, QOS_CLASS_USER_INITIATED
            apply_thread_qos(QOS_CLASS_USER_INITIATED)
        except Exception:  # noqa: BLE001 — best-effort; QoS hinting; non-critical
            pass

    def __repr__(self) -> str:
        return (
            f"RemoteParquetSource(uri={self.uri!r}, source_type={self.source_type!r}, "
            f"total_rows={self.total_rows}, batch_size={self.batch_size})"
        )


class ParquetHistoryReader:
    """
    DuckDB-native parquet reader — eliminates ~200 LOC of manual row-group management.

    P4-8: Replaces hand-rolled row-group stats + PyArrow fallback chain with
    DuckDB's own read_parquet() which has native filter pushdown, column pruning,
    and parallel execution. Falls back to PyArrow only when DuckDB is unavailable.

    M1 8GB safe: DuckDB manages memory via hard_memory_limit (1GB ceiling).
    Zero-copy: Arrow Table from DuckDB → Polars zero-copy via from_arrow.

    Usage:
        reader = ParquetHistoryReader("/path/to/history.parquet")

        # Filter pushdown (DuckDB WHERE pushdown — no row-group manual filtering)
        reader.filter_time_range(min_ts=1700000000.0, max_ts=1701000000.0)
        reader.filter_source_types(["dark_web", "leak"])

        # Streaming via DuckDB fetchmany → Arrow batches
        for batch in reader.iter_batches(batch_size=50_000):
            df = pl.from_arrow(batch)  # zero-copy
            process(df)

        # Or as Polars LazyFrame (DuckDB scan with full optimizer)
        # M1 8GB: streaming engine required — prevents full table materialization
        lf = reader.to_polars_lazy()
        filtered = lf.filter(pl.col("source_type") == "dark_web").collect(engine="streaming")

    ATTACH alternative for remote files:
        RemoteParquetSource — use when file is on S3/HTTPS/Azure/GCS/Postgres.
        ParquetHistoryReader — local files only.
    """

    __slots__ = tuple((
        "path", "columns", "batch_size",
        "_ts_min", "_ts_max", "_source_types",
        "_duckdb_conn", "_total_rows",
    ))

    def __init__(self, path: str, columns: list[str] | None = None, batch_size: int = 50000) -> None:
        self.path = path
        self.columns = columns or ["id", "query", "source_type", "confidence", "ts", "provenance_json"]
        self.batch_size = min(batch_size, 100000)
        self._ts_min: float | None = None
        self._ts_max: float | None = None
        self._source_types: set[str] | None = None
        self._duckdb_conn: Any | None = None
        self._total_rows: int | None = None

    def filter_time_range(self, min_ts: float | None = None, max_ts: float | None = None) -> "ParquetHistoryReader":
        """Set time filter for DuckDB WHERE pushdown. Returns self for chaining."""
        self._ts_min = min_ts
        self._ts_max = max_ts
        return self

    def filter_source_types(self, source_types: list[str] | None) -> "ParquetHistoryReader":
        """Set source_type filter for DuckDB WHERE pushdown. Returns self for chaining."""
        self._source_types = set(source_types) if source_types else None
        return self

    def _ensure_duckdb(self) -> Any | None:
        """Lazily get DuckDB connection with local parquet attached."""
        if self._duckdb_conn is not None:
            return self._duckdb_conn
        try:
            duckdb = self._get_duckdb()
            conn = duckdb.connect(":memory:")
            try:
                conn.execute("PRAGMA threads = 2")
                conn.execute("SET memory_limit = '1GB'")
                conn.execute("PRAGMA hard_memory_limit = '1GB'")
                conn.execute("PRAGMA enable_progress_bar = false")
            except Exception:  # noqa: BLE001 — best-effort; DuckDB settings; non-critical
                pass
            # Read local parquet — DuckDB handles row-group stats + filter pushdown
            conn.execute(f"CREATE VIEW local_parquet AS SELECT * FROM read_parquet('{self.path}')")
            self._duckdb_conn = conn
            return conn
        except Exception:  # noqa: BLE001 — best-effort; DuckDB init; non-critical
            return None

    def _get_duckdb(self):
        return _get_duckdb()

    def _build_where(self) -> str | None:
        """Build DuckDB WHERE clause from filters."""
        parts: list[str] = []
        if self._ts_min is not None:
            parts.append(f"ts >= {self._ts_min}")
        if self._ts_max is not None:
            parts.append(f"ts <= {self._ts_max}")
        if self._source_types:
            # E-33: Escape single quotes for DuckDB string literals
            escaped = [t.replace("'", "''") for t in self._source_types]
            types_list = ", ".join(f"'{e}'" for e in escaped)
            parts.append(f"source_type IN ({types_list})")
        return " AND ".join(parts) if parts else None

    def _count_rows(self) -> int:
        """COUNT(*) via DuckDB (pushdown-aware)."""
        conn = self._ensure_duckdb()
        if conn is None:
            return self._count_rows_pyarrow()
        try:
            where = self._build_where()
            sql = "SELECT COUNT(*) FROM local_parquet"
            if where:
                sql += f" WHERE {where}"
            result = conn.execute(sql).fetchone()
            return result[0] if result else 0
        except Exception:  # noqa: BLE001 — best-effort; count; non-critical
            return self._count_rows_pyarrow()

    def _count_rows_pyarrow(self) -> int:
        """PyArrow fallback row count."""
        try:
            import pyarrow.parquet as pq
            pf = pq.ParquetFile(self.path)
            return pf.metadata.num_rows
        except Exception:  # noqa: BLE001 — best-effort; PyArrow count; non-critical
            return 0

    @property
    def num_row_groups(self) -> int:
        """Return number of row-groups via PyArrow (metadata only, no data read)."""
        try:
            import pyarrow.parquet as pq
            pf = pq.ParquetFile(self.path)
            return pf.num_row_groups
        except Exception:  # noqa: BLE001 — best-effort; rowgroup count; non-critical
            return 0

    @property
    def total_rows(self) -> int:
        """Return total row count."""
        if self._total_rows is None:
            self._total_rows = self._count_rows()
        return self._total_rows

    def iter_batches(self) -> Iterator:
        """
        Iterate via DuckDB fetchmany → Arrow RecordBatch (DuckDB-native path).

        Falls back to PyArrow if DuckDB unavailable.
        Yields:
            pyarrow.RecordBatch — caller uses pl.from_arrow(batch) for zero-copy.
        """
        conn = self._ensure_duckdb()
        if conn is not None:
            yield from self._iter_via_duckdb(conn)
            return
        yield from self._iter_via_pyarrow()

    def _iter_via_duckdb(self, conn: Any) -> Iterator:
        """DuckDB-native iteration with WHERE pushdown."""
        try:
            import pyarrow as pa

            cols = ", ".join(self.columns) if self.columns else "*"
            sql = f"SELECT {cols} FROM local_parquet"
            where = self._build_where()
            if where:
                sql += f" WHERE {where}"
            result = conn.execute(sql)
            try:
                # Fast path: DuckDB Arrow batch export — O(1) allocation
                batches = result.fetch_arrow_batch(self.batch_size)
                while len(batches) > 0:
                    for batch in batches:
                        yield batch
                    batches = result.fetch_arrow_batch(self.batch_size)
                return
            except AttributeError:
                pass  # Fall through to tuple-based path
            # Fallback: convert tuples to Arrow via from_pydict (single pass per batch)
            while True:
                rows = result.fetchmany(self.batch_size)
                if not rows:
                    break
                columns = [desc[0] for desc in result.description]
                col_arrays = [[row[i] for row in rows] for i in range(len(columns))]
                table = pa.Table.from_pydict(dict(zip(columns, col_arrays)))
                yield from table.to_batches(max_chunksize=self.batch_size)
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB iteration; non-critical
            logger.warning(f"[ParquetHistoryReader] DuckDB path failed, falling back to PyArrow: {e}")
            yield from self._iter_via_pyarrow()

    def _iter_via_pyarrow(self) -> Iterator:
        """PyArrow fallback — pure row-group iteration."""
        try:
            import pyarrow.parquet as pq

            pf = pq.ParquetFile(self.path)
            cols = self.columns
            for rg_idx in range(pf.num_row_groups):
                try:
                    batch = next(pf.iter_batches(batch_size=self.batch_size, row_groups=[rg_idx], columns=cols))
                    if batch.num_rows > 0:
                        yield batch
                except StopIteration:
                    continue
        except Exception:  # noqa: BLE001 — best-effort; PyArrow fallback; non-critical
            return

    def to_polars_lazy(self):
        """
        Polars LazyFrame via DuckDB scan (DuckDB-native, full optimizer).

        DuckDB handles filter pushdown, column pruning, and parallel execution.
        Falls back to PyArrow if DuckDB unavailable.

        Returns:
            polars.LazyFrame or None.
        """
        conn = self._ensure_duckdb()
        if conn is None:
            return self._to_polars_lazy_pyarrow()
        try:
            import polars as pl

            cols = ", ".join(self.columns) if self.columns else "*"
            sql = f"SELECT {cols} FROM local_parquet"
            where = self._build_where()
            if where:
                sql += f" WHERE {where}"
            return conn.execute(sql).pl().lazy()
        except ImportError:
            raise ImportError("Polars not installed: pip install polars")
        except Exception:  # noqa: BLE001 — best-effort; DuckDB scan; non-critical
            return self._to_polars_lazy_pyarrow()

    def _to_polars_lazy_pyarrow(self):
        """
        PyArrow fallback for to_polars_lazy.

        M1 8GB: Uses pl.scan_parquet() for streaming — avoids full table
        materialization that pq.read_table() would trigger. Filters are
        applied as predicates pushed down into the scan.
        """
        try:
            import polars as pl

            # M1 8GB: scan_parquet() streams data with predicate pushdown,
            # unlike pq.read_table() which eagerly materializes the entire file.
            lf = pl.scan_parquet(self.path, schema_overrides={"provenance_json": pl.String})

            # Apply filters if set (predicate pushdown into scan)
            if self._ts_min is not None:
                lf = lf.filter(pl.col("ts") >= self._ts_min)
            if self._ts_max is not None:
                lf = lf.filter(pl.col("ts") <= self._ts_max)
            if self._source_types:
                lf = lf.filter(pl.col("source_type").is_in(self._source_types))
            return lf
        except ImportError:
            raise ImportError("Polars not installed: pip install polars")
        except Exception:  # noqa: BLE001 — best-effort; PyArrow fallback; non-critical
            return None

    def iter_batches_async(self):
        """Async iterator — runs iter_batches on thread pool."""
        async def _aiter():
            for batch in self.iter_batches():
                yield await asyncio.to_thread(lambda b=batch: b, batch)

        return _aiter()

    def read_table(self):
        """Read entire parquet as Arrow Table. Prefer iter_batches() for large files.

        M1 8GB: This method is a last-resort fallback for explicit full-table reads.
        For large files, prefer iter_batches() which streams in row groups.
        """
        conn = self._ensure_duckdb()
        if conn is not None:
            try:
                cols = ", ".join(self.columns) if self.columns else "*"
                sql = f"SELECT {cols} FROM local_parquet"
                where = self._build_where()
                if where:
                    sql += f" WHERE {where}"
                result = conn.execute(sql)
                # M1 8GB: fetch_arrow_table() is zero-copy via Arrow C Data Interface,
                # no Python intermediary allocation unlike fetchall() + from_pydict().
                arrow_table = result.fetch_arrow_table()
                if arrow_table is None or arrow_table.num_rows == 0:
                    return None
                return arrow_table
            except Exception:  # noqa: BLE001 — best-effort; DuckDB read; non-critical
                pass
        # DuckDB unavailable — fall back to PyArrow (eager, loads entire file)
        try:
            import pyarrow.parquet as pq
            return pq.read_table(self.path)
        except Exception:  # noqa: BLE001 — best-effort; PyArrow read; non-critical
            return None

    def __len__(self) -> int:
        return self.num_row_groups

    def __repr__(self) -> str:
        filters = []
        if self._ts_min or self._ts_max:
            filters.append(f"ts=[{self._ts_min},{self._ts_max}]")
        if self._source_types:
            filters.append(f"types={self._source_types}")
        filter_str = f", filters=[{'; '.join(filters)}]" if filters else ""
        return f"ParquetHistoryReader(path={self.path!r}, row_groups={self.num_row_groups}, total_rows={self.total_rows}, batch_size={self.batch_size}{filter_str})"


def _get_memory_pressure() -> "MemoryPressureLevel":
    """
    Get current memory pressure level using psutil.

    Returns MemoryPressureLevel.NORMAL if unavailable.
    Uses same thresholds as BaseCoordinator.check_memory_pressure().

    ISSUE-025: Memory pressure gating for parquet export on M1 8GB.
    """
    from hledac.universal.coordinators.enums import MemoryPressureLevel as _MPL
    try:
        import psutil
        vm = psutil.virtual_memory()
        ratio = vm.used / vm.total
        # Match thresholds from coordinators/base.py: _memory_thresholds
        if ratio >= 0.95:
            return _MPL.CRITICAL
        elif ratio >= 0.85:
            return _MPL.HIGH
        elif ratio >= 0.75:
            return _MPL.ELEVATED
        return _MPL.NORMAL
    except Exception:  # noqa: BLE001 — best-effort; psutil/MemoryPressureLevel unavailable
        # Return NORMAL so export proceeds when monitoring is unavailable
        return _MPL.NORMAL


def export_findings_to_parquet(
    path: str,
    query: str = "SELECT id, query, source_type, confidence, ts, provenance_json, payload_text, claims_json FROM canonical_findings",
    batch_size: int = 100000,
    _memory_pressure_gate: bool = True,
) -> bool:
    """
    ISSUE-025: Streaming parquet export with per-batch memory pressure gate.

    Replaces atomic COPY TO with iterative fetchmany + pyarrow ParquetWriter.
    Each batch:
      1. DuckDB fetchmany(batch_size) — rows in memory
      2. Memory pressure check — abort if HIGH/CRITICAL
      3. pyarrow.Table.from_pydict — zero-copy columnar
      4. ParquetWriter.write_table — appends row group to file

    M1 8GB: max ~100 MB per batch (100k rows × 6 cols × ~180 bytes).
    At 0.85 RAM threshold (HIGH), export pauses and returns False.

    Args:
        path: Output parquet file path
        query: SQL query to export (default: all canonical_findings)
        batch_size: Rows per batch (default 100k; M1 8GB safe max ~100 MB/batch)
        _memory_pressure_gate: If True, check pressure between batches, abort if HIGH/CRITICAL

    Returns:
        True on full success, False if paused (pressure) or error.
    """
    import os

    from hledac.universal.coordinators.enums import MemoryPressureLevel as _MPL

    _path = os.path.expanduser(path) if path.startswith("~") else path
    _db_path = os.environ.get("HLEDAC_DUCKDB_PATH", "hledac.duckdb")
    if not os.path.isabs(_db_path):
        if not os.path.exists(_db_path):
            for _p in ["./hledac.duckdb", "../hledac.duckdb", "~/.hledac/hledac.duckdb"]:
                if os.path.exists(os.path.expanduser(_p)):
                    _db_path = os.path.expanduser(_p)
                    break

    try:
        duckdb = _get_duckdb()
        conn = duckdb.connect(":memory:")
        # M1 8GB: memory_limit + threads + preserve_insertion_order
        try:
            conn.execute("PRAGMA threads = 2")
            conn.execute("SET memory_limit = '1GB'")
            conn.execute("SET preserve_insertion_order = false")
        except Exception:  # noqa: BLE001 — fail-soft
            pass
        conn.execute(f"ATTACH '{_db_path}' AS source_db")
        conn.execute("USE source_db")

        result = conn.execute(query)
        columns = [desc[0] for desc in result.description]

        total_rows = 0
        writer = None
        pa = None

        try:
            import pyarrow as _pa

            pa = _pa
            os.makedirs(os.path.dirname(_path) or ".", exist_ok=True)

            batch: list[tuple]
            batch_num = 0
            while True:
                batch = result.fetchmany(batch_size)
                if not batch:
                    break

                batch_num += 1

                # ISSUE-025: Per-batch memory pressure gate — prevent OOM mid-export
                if _memory_pressure_gate:
                    pressure = _get_memory_pressure()
                    if pressure in (_MPL.HIGH, _MPL.CRITICAL):
                        logger.warning(
                            "[EXPORT-PAUSE] batch %d memory pressure=%s, deferring parquet export to reduce OOM risk",
                            batch_num,
                            pressure.value,
                        )
                        if writer is not None:
                            try:
                                writer.close()
                            except Exception:
                                pass
                        return False

                col_arrays = [[row[i] for row in batch] for i in range(len(columns))]
                table = _pa.Table.from_pydict(dict(zip(columns, col_arrays)))

                if writer is None:
                    writer = _pa.parquet.ParquetWriter(
                        _path,
                        table.schema,
                        compression="zstd",
                        row_group_size=batch_size,
                    )

                writer.write_table(table)
                total_rows += len(batch)

            if writer is not None:
                writer.close()
            return True

        except Exception as _e:
            logger.debug("[EXPORT-PAUSE] streaming parquet error: %s", _e)
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
            return False
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
        return False


from .wal import WALManager
from .duckdb_wal_manager import DuckDBWALManager

logger = logging.getLogger(__name__)

# Sprint F360: DTO types deduplicated — single source of truth is
# knowledge/sprint_facts/canonical_finding.py. Backward-compat aliases here.
from .sprint_facts.canonical_finding import ActivationResult, CanonicalFinding

# ReplayResult is NOT in canonical_finding.py — keep it here only.
class ReplayResult(msgspec.Struct, gc=False):
    """
    Sprint F300: msgspec.Struct for pending-sync replay operations.
    F350M-R: gc=False for M1 8GB.

    Fields:
        finding_id:           Unique identifier of the finding
        marker_found:         True if pending marker existed before replay attempt
        wal_truth_found:      True if finding:{id} WAL truth was found in LMDB
        duckdb_written:       True if DuckDB write succeeded during replay
        marker_cleared:       True if pending marker was cleared after success
        read_back_verified:   True if fresh read-back confirmed the DuckDB record
        deadlettered:         True if marker was moved to dead-letter namespace
        retry_count:          Number of retry attempts made
        error:                Error message if there was an exception, None otherwise
    """

    finding_id: str
    marker_found: bool
    wal_truth_found: bool
    duckdb_written: bool
    marker_cleared: bool
    read_back_verified: bool
    deadlettered: bool
    retry_count: int
    error: str | None

# CanonicalFinding imported above from .sprint_facts.canonical_finding
from ._quality_types import FindingQualityDecision
from ._query_cache import _DuckDBQueryCache

_query_cache: _DuckDBQueryCache | None = None


import functools


@functools.cache
def _get_duckdb() -> Any:
    """Lazy import of duckdb - only loaded when sidecar is actually used.

    Thread-safe via functools.cache internal lock (PEP 603 memoization).
    No global variable needed; cache handles idempotent initialization.
    """
    import duckdb

    return duckdb


from hledac.universal.core.env_config import ENV

_DUCKDB_MEMORY_LIMIT: str = ENV.get("GHOST_DUCKDB_MEMORY", default="1GB")
_DUCKDB_MAX_TEMP: str = ENV.get("GHOST_DUCKDB_MAX_TEMP", default="1GB")
# ISSUE-35: Hard ceiling — DuckDB will NOT exceed this under any circumstances.
# On M1 8GB: OS ~2.5GB + Python ~1GB + MLX inference ~4.5GB = 8GB total.
# DuckDB gets 1GB ceiling to leave headroom for other subsystems.
_DUCKDB_HARD_MEMORY_LIMIT: str = "1GB"
_ARROW_INGEST_ENABLED: bool = ENV.get_bool("HLEDAC_ARROW_INGEST")
_DUCKDB_RAMDISK_TEMP: str | None = ENV.get_str("HLEDAC_DUCKDB_RAMDISK_TEMP") or None
_ARROW_MIN_BATCH: int = ENV.get_int("HLEDAC_ARROW_MIN_BATCH", default=5)
_DUCKDB_QUERY_CACHE_ENABLED: bool = ENV.get_bool("HLEDAC_DUCKDB_QUERY_CACHE")
_DUCKDB_QUERY_CACHE_L1_MAX: int = ENV.get_int("HLEDAC_DUCKDB_QUERY_CACHE_L1_MAX", default=500)
_DUCKDB_QUERY_CACHE_L2_MAX: int = ENV.get_int("HLEDAC_DUCKDB_QUERY_CACHE_L2_MAX", default=5000)
_DUCKDB_QUERY_CACHE_TTL_S: int = ENV.get_int("HLEDAC_DUCKDB_QUERY_CACHE_TTL_S", default=300)


def _check_pyarrow_available() -> bool:
    """
    Sprint F265C: Cache-aware pyarrow availability check.

    Called from tight loops (executor overhead path) so we optimize for the
    common case: pyarrow already imported -> O(1) sys.modules lookup, zero I/O.
    Only falls back to find_spec when pyarrow is not yet loaded.

    Caches result in module-level _PYARROW_AVAILABLE so repeated calls in the
    same process are always O(1).
    """
    if "pyarrow" in sys.modules:
        return True
    cached = getattr(_check_pyarrow_available, "_cached", None)
    if cached is not None:
        return cached
    import importlib.util

    spec = importlib.util.find_spec("pyarrow")
    result = spec is not None
    object.__setattr__(_check_pyarrow_available, "_cached", result)
    return result


def get_arrow_metrics() -> dict[str, int]:
    """
    Sprint F265C: Expose Arrow ingest metrics for sprint telemetry.
    Sprint F265B Variant B: DEPRECATED — _ARROW_METRICS moved to instance-level
    DuckDBShadowStore._arrow_metrics (per-sprint reset, prevents cross-sprint growth).

    Returns empty dict — instance metrics are accessed via store._arrow_metrics
    in __main__.py L2664 (preferred path) or sprint_scheduler L8949.
    Kept for backward compat with external callers that import this directly.
    """
    return {}


def _resolve_duckdb_runtime_settings(uma_state: str | None = None, swap_detected: bool = False) -> dict[str, str | int]:
    """
    Resolve DuckDB runtime settings based on UMA memory pressure state.

    DuckDB store receives explicit uma_state from resource_governor/scheduler
    - it MUST NOT import heavy runtime schedulers to determine this internally.

    Args:
        uma_state: One of "WARN", "CRITICAL", "EMERGENCY", or None for normal.
        swap_detected: True if system-level swap pressure is detected.

    Returns:
        dict with keys: memory_limit (str), max_temp (str),
                        threads (int), preserve_insertion_order (bool),
                        safe_mode (bool), write_buffer_limit (str),
                        allocator_flush_threshold (str),
                        allocator_bulk_dealloc_threshold (str),
                        enable_fsst_vectors (bool),
                        temp_file_encryption (bool).
    """
    base_mem = ENV.get_str("GHOST_DUCKDB_MEMORY", default="4GB")
    base_threads = ENV.get_int("HLEDAC_DUCKDB_THREADS", default=4)
    settings: dict[str, str | int | bool] = {
        "memory_limit": base_mem,
        "max_temp": _DUCKDB_MAX_TEMP,
        "threads": base_threads,
        "preserve_insertion_order": False,
        "safe_mode": False,
        "chunk_size": 1024,
        "pipeline_maxsize": 4,
        "write_buffer_limit": "64MiB",
        "allocator_flush_threshold": "64MiB",
        "allocator_bulk_dealloc_threshold": "256MiB",
        "enable_fsst_vectors": True,
        "temp_file_encryption": False,
    }
    if swap_detected:
        settings["memory_limit"] = "200MB"
        settings["threads"] = 1
        settings["safe_mode"] = True
        settings["enable_fsst_vectors"] = False
        settings["write_buffer_limit"] = "32MiB"
        settings["allocator_flush_threshold"] = "32MiB"
        settings["allocator_bulk_dealloc_threshold"] = "128MiB"
        settings["chunk_size"] = 256
        settings["pipeline_maxsize"] = 2
    elif uma_state == "EMERGENCY":
        settings["memory_limit"] = "200MB"
        settings["threads"] = 1
        settings["safe_mode"] = True
        settings["enable_fsst_vectors"] = False
        settings["write_buffer_limit"] = "32MiB"
        settings["allocator_flush_threshold"] = "32MiB"
        settings["allocator_bulk_dealloc_threshold"] = "128MiB"
        settings["chunk_size"] = 256
        settings["pipeline_maxsize"] = 2
    elif uma_state == "CRITICAL":
        settings["memory_limit"] = "250MB"
        settings["threads"] = 1
        settings["enable_fsst_vectors"] = False
        settings["write_buffer_limit"] = "48MiB"
        settings["allocator_flush_threshold"] = "48MiB"
        settings["allocator_bulk_dealloc_threshold"] = "192MiB"
        settings["chunk_size"] = 512
        settings["pipeline_maxsize"] = 2
    elif uma_state == "WARN":
        settings["memory_limit"] = "250MB"
        settings["threads"] = 2
        settings["write_buffer_limit"] = "64MiB"
        settings["allocator_flush_threshold"] = "64MiB"
        settings["allocator_bulk_dealloc_threshold"] = "256MiB"
        settings["chunk_size"] = 768
        settings["pipeline_maxsize"] = 3
    else:
        settings["chunk_size"] = 1536
        settings["pipeline_maxsize"] = 6
    return settings


def _validate_duckdb_setting(value: str, setting_name: str) -> str:
    """
    Validate DuckDB setting value to prevent SQL injection.

    P1-3: Replaces f-string interpolation in SET commands.
    Only allows alphanumeric, GB/MB/KB/TB/MiB/GiB/KiB suffixes, and basic punctuation.
    """
    import re

    if not re.match("^[\\d.]+\\s*(GB|MB|KB|TB|MiB|GiB|KiB)?\\s*$", value.strip(), re.IGNORECASE):
        raise ValueError(f"Invalid DuckDB setting {setting_name}: {value!r}")
    return value.strip()


def _validate_path_setting(value: Path, setting_name: str) -> str:
    """
    Validate Path setting for DuckDB SET commands.

    P1-3: Ensures path is absolute and contains no shell metacharacters.
    """
    value_str = str(value)
    if not value.is_absolute():
        raise ValueError(f"Invalid DuckDB {setting_name}: must be absolute path, got {value_str!r}")
    dangerous = ["'", '"', ";", "$", "`", "\\", "\n", "\r"]
    for char in dangerous:
        if char in value_str:
            raise ValueError(f"Invalid DuckDB {setting_name}: dangerous char {char!r} in {value_str!r}")
    return value_str


def _validate_duckdb_threads(value: str | int, setting_name: str = "threads") -> int:
    """
    Validate threads is a safe positive integer in DuckDB-supported range.

    P1-3: Replaces f-string interpolation in PRAGMA threads=...
    DuckDB PRAGMA does not support ? prepared params, so we validate
    the integer at write time rather than use parameterized syntax.
    """
    try:
        int_val = int(value)
    except (ValueError, TypeError) as err:
        raise ValueError(f"Invalid DuckDB {setting_name}: cannot convert {value!r} to int") from err
    max_threads = min(os.cpu_count() or 4, 8)
    if not 1 <= int_val <= max_threads:
        raise ValueError(f"Invalid DuckDB {setting_name}: {int_val} out of safe range [1, {max_threads}]")
    return int_val


from hledac.universal.config.dedup_config import DEDUP_LMDB_MAP_SIZE

_DEDUP_LMDB_MAP_SIZE: int = DEDUP_LMDB_MAP_SIZE
_SCHEMA_SQL = '\n    CREATE TABLE IF NOT EXISTS canonical_findings (\n        id              VARCHAR PRIMARY KEY,\n        query           VARCHAR,\n        source_type     VARCHAR,\n        confidence      DOUBLE,\n        ts              DOUBLE,\n        provenance_json TEXT,\n        payload_text    TEXT,\n        claims_json     TEXT,\n        UNIQUE (id),\n        UNIQUE (query, source_type)\n    );\n    -- Sprint F360M-R: claims_json column added for sentence-level claims extraction.\n    -- Sprint STORAGE-FIX-1: time-range + per-query lookups\n    -- canonical_findings is queried with WHERE query LIKE ? ORDER BY ts DESC LIMIT N (6+ sites).\n    -- time-range + per-query lookups, indexes for performance\n    CREATE INDEX IF NOT EXISTS idx_canonical_findings_ts ON canonical_findings(ts DESC);\n    CREATE INDEX IF NOT EXISTS idx_canonical_findings_query ON canonical_findings(query);\n    CREATE TABLE IF NOT EXISTS shadow_runs (\n        run_id      VARCHAR PRIMARY KEY,\n        started_at  TIMESTAMP,\n        ended_at    TIMESTAMP,\n        total_fds   INTEGER,\n        rss_mb      INTEGER\n    );\n    CREATE TABLE IF NOT EXISTS sprint_delta (\n        sprint_id TEXT PRIMARY KEY,\n        ts DOUBLE NOT NULL,\n        query TEXT,\n        duration_s REAL DEFAULT 0,\n        new_findings INT DEFAULT 0,\n        dedup_hits INT DEFAULT 0,\n        ioc_nodes INT DEFAULT 0,\n        ioc_new_this_sprint INT DEFAULT 0,\n        uma_peak_gib REAL DEFAULT 0,\n        synthesis_success BOOL DEFAULT false,\n        findings_per_minute REAL DEFAULT 0,\n        top_source_type TEXT,\n        synthesis_confidence REAL DEFAULT 0\n    );\n    -- Index for ORDER BY ts DESC queries (scoreboard, recent sprints)\n    CREATE INDEX IF NOT EXISTS idx_sprint_delta_ts ON sprint_delta(ts DESC);\n    CREATE TABLE IF NOT EXISTS source_hit_log (\n        sprint_id TEXT,\n        ts DOUBLE,\n        source_type TEXT,\n        findings_count INT,\n        ioc_count INT,\n        hit_rate REAL\n    );\n    -- Sprint F-B: indexes for per-sprint + time-range source_hit_log lookups\n    CREATE INDEX IF NOT EXISTS idx_source_hit_log_sprint_ts\n        ON source_hit_log(sprint_id, ts DESC);\n    CREATE INDEX IF NOT EXISTS idx_source_hit_log_ts\n        ON source_hit_log(ts DESC);\n    CREATE TABLE IF NOT EXISTS sprint_scorecard (\n        sprint_id TEXT PRIMARY KEY,\n        ts DOUBLE NOT NULL,\n        findings_per_minute REAL,\n        ioc_density REAL,\n        semantic_novelty REAL,\n        source_yield_json TEXT,\n        phase_timings_json TEXT,\n        outlines_used BOOL,\n        accepted_findings INT,\n        ioc_nodes INT\n    );\n    CREATE INDEX IF NOT EXISTS idx_sprint_scorecard_ts\n        ON sprint_scorecard(ts DESC);\n    CREATE TABLE IF NOT EXISTS research_episodes (\n        episode_id   TEXT PRIMARY KEY,\n        sprint_id    TEXT NOT NULL,\n        query        TEXT NOT NULL,\n        summary      TEXT,\n        top_findings JSON,\n        ioc_clusters JSON,\n        source_yield JSON,\n        synthesis_engine TEXT,\n        duration_s   REAL,\n        ts           DOUBLE NOT NULL\n    );\n    CREATE INDEX IF NOT EXISTS idx_episodes_ts ON research_episodes(ts DESC);\n    CREATE INDEX IF NOT EXISTS idx_episodes_sprint\n        ON research_episodes(sprint_id);\n    CREATE TABLE IF NOT EXISTS target_profiles (\n        target_id TEXT PRIMARY KEY,\n        first_seen DOUBLE,\n        last_seen DOUBLE,\n        cumulative_finding_count INTEGER,\n        entity_summary_json TEXT\n    );\n    -- Sprint F-B: target_profiles queried by last_seen DESC for recent targets\n    CREATE INDEX IF NOT EXISTS idx_target_profiles_last_seen\n        ON target_profiles(last_seen DESC);\n    CREATE TABLE IF NOT EXISTS hypothesis_feedback (\n        id TEXT PRIMARY KEY,\n        target_id TEXT,\n        pivot_type TEXT,\n        ioc_type TEXT,\n        produced_count INTEGER,\n        accepted_count INTEGER,\n        signal_value DOUBLE,\n        ts DOUBLE\n    );\n    -- Sprint F-B: hypothesis_feedback target_id is the primary filter\n    -- for per-target pivot analytics. Index avoids scan.\n    CREATE INDEX IF NOT EXISTS idx_hypothesis_feedback_target_ts\n        ON hypothesis_feedback(target_id, ts DESC);\n    CREATE TABLE IF NOT EXISTS hypothesis_tracking (\n        hypothesis_id TEXT PRIMARY KEY,\n        sprint_id TEXT,\n        hypothesis_text TEXT,\n        status TEXT,\n        confidence REAL,\n        falsification_result TEXT,\n        disproved_by_sprint_id TEXT,\n        ts DOUBLE\n    );\n    -- Sprint F-B: hypothesis_tracking is queried by sprint_id and status\n    -- in the windup_engine hypothesis summarizer.\n    CREATE INDEX IF NOT EXISTS idx_hypothesis_tracking_sprint\n        ON hypothesis_tracking(sprint_id);\n    CREATE INDEX IF NOT EXISTS idx_hypothesis_tracking_status_ts\n        ON hypothesis_tracking(status, ts DESC);\n    CREATE TABLE IF NOT EXISTS target_memory (\n        target_id TEXT PRIMARY KEY,\n        first_seen_ts DOUBLE NOT NULL,\n        last_seen_ts DOUBLE NOT NULL,\n        sprint_count INTEGER NOT NULL,\n        cumulative_finding_count INTEGER NOT NULL,\n        entity_facets_json TEXT NOT NULL,\n        exposure_facets_json TEXT NOT NULL,\n        pivot_facets_json TEXT NOT NULL,\n        confidence_drift_json TEXT NOT NULL,\n        updated_by_sprint_id TEXT NOT NULL\n    );\n    -- Sprint F-B: target_memory last_seen_ts is the primary sort key\n    -- for "recent targets" queries in F204D.\n    CREATE INDEX IF NOT EXISTS idx_target_memory_last_seen\n        ON target_memory(last_seen_ts DESC);\n    -- Sprint F224A: DHT metadata table for torrent content discovery\n    CREATE TABLE IF NOT EXISTS dht_metadata (\n        infohash TEXT PRIMARY KEY,\n        name TEXT,\n        files_json TEXT,\n        size_bytes BIGINT,\n        first_seen DOUBLE,\n        last_seen DOUBLE,\n        peer_count INT,\n        sources_json TEXT\n    );\n    -- Sprint F-B: dht_metadata is queried by last_seen DESC and peer_count\n    -- for "recent active torrents" and "popular torrents" lookups.\n    CREATE INDEX IF NOT EXISTS idx_dht_metadata_last_seen\n        ON dht_metadata(last_seen DESC);\n    CREATE INDEX IF NOT EXISTS idx_dht_metadata_peer_count\n        ON dht_metadata(peer_count DESC);\n    -- Sprint F350M: Cross-sprint research session memory\n    CREATE TABLE IF NOT EXISTS research_sessions (\n        session_id TEXT PRIMARY KEY,\n        sprint_id TEXT NOT NULL,\n        query TEXT NOT NULL,\n        ts DOUBLE NOT NULL,\n        findings_count INTEGER,\n        accepted_count INTEGER,\n        gaps_json TEXT,\n        entities_json TEXT,\n        source_patterns_json TEXT,\n        unexplored_angles_json TEXT,\n        temporal_anomalies_json TEXT\n    );\n    CREATE INDEX IF NOT EXISTS idx_research_sessions_sprint\n        ON research_sessions(sprint_id);\n    CREATE INDEX IF NOT EXISTS idx_research_sessions_ts\n        ON research_sessions(ts DESC);\n    -- Sprint F350M: Entity observations for temporal tracking\n    CREATE TABLE IF NOT EXISTS entity_observations (\n        observation_id TEXT PRIMARY KEY,\n        entity_value TEXT NOT NULL,\n        entity_type TEXT NOT NULL,\n        sprint_id TEXT NOT NULL,\n        source_type TEXT NOT NULL,\n        confidence REAL,\n        ts DOUBLE NOT NULL,\n        finding_id TEXT NOT NULL\n    );\n    CREATE INDEX IF NOT EXISTS idx_entity_observations_entity\n        ON entity_observations(entity_value);\n    CREATE INDEX IF NOT EXISTS idx_entity_observations_sprint\n        ON entity_observations(sprint_id);\n    -- Sprint F330: IOC co-occurrence matrix for speculative edge mining\n    CREATE TABLE IF NOT EXISTS ioc_cooccurrence (\n        ioc_a TEXT NOT NULL,\n        ioc_b TEXT NOT NULL,\n        ioc_type_a TEXT NOT NULL,\n        ioc_type_b TEXT NOT NULL,\n        support INTEGER NOT NULL,\n        confidence REAL NOT NULL,\n        score REAL NOT NULL,\n        last_seen REAL NOT NULL\n    );\n    CREATE INDEX IF NOT EXISTS idx_ioc_cooccurrence_score\n        ON ioc_cooccurrence(score DESC);\n    CREATE INDEX IF NOT EXISTS idx_ioc_cooccurrence_ioc_a\n        ON ioc_cooccurrence(ioc_a);\n    CREATE INDEX IF NOT EXISTS idx_ioc_cooccurrence_ioc_b\n        ON ioc_cooccurrence(ioc_b);\n'


def _apply_schema(conn, schema_sql: str) -> None:
    """Apply multi-statement schema via DuckDB's official tokenizer.

    DuckDB 1.5+ provides ``connection.extract_statements()`` which correctly
    parses SQL including semicolons inside string literals.  Primary path uses it.
    Fallback regex tokenizer is a proper state-machine (not a single re.split)
    that also handles ';'-inside-strings and produces clean statements.

    Idempotent: ``CREATE INDEX`` / ``CREATE TABLE`` errors (already exists) are
    silenced so schema can be re-applied on every init without complaint.
    """
    import re as _re

    def _strip_comments(sql: str) -> str:
        """Remove -- and # line comments, then trailing triple-quote residue."""
        sql = _re.sub("^\\s*--.*$", "", sql, flags=_re.MULTILINE)
        sql = _re.sub("^\\s*#.*$", "", sql, flags=_re.MULTILINE)
        sql = sql.replace('"""', "").strip()
        return sql

    def _regex_split_statements(sql: str) -> list[str]:
        """Split on ';' respecting string literals — safe fallback for older DuckDB."""
        stmts, start, in_string = ([], 0, False)
        i = 0
        while i < len(sql):
            c = sql[i]
            if c == "'" and (not in_string):
                in_string = True
            elif c == "'" and in_string:
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    i += 2
                    continue
                in_string = False
            elif c == ";" and (not in_string):
                stmt = sql[start:i].strip()
                if stmt:
                    stmts.append(stmt)
                start = i + 1
            i += 1
        tail = sql[start:].strip()
        if tail:
            stmts.append(tail)
        return stmts

    sql = _strip_comments(schema_sql)
    if hasattr(conn, "extract_statements"):
        try:
            for stmt in conn.extract_statements(sql):
                stmt_sql = stmt.sql if hasattr(stmt, "sql") else str(stmt)
                if not stmt_sql.strip():
                    continue
                try:
                    conn.execute(stmt_sql)
                except Exception as exc:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                    if "already exists" in str(exc).lower():
                        continue
                    raise
            return
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            pass
    for s in _regex_split_statements(sql):
        if not s:
            continue
        try:
            conn.execute(s)
        except Exception as exc:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            if "already exists" in str(exc).lower():
                continue
            raise


from hledac.universal.utils.msgspec_json import encode as _msgspec_encode
from hledac.universal.utils.msgspec_json import encode_for_arrow

_MAX_INFLIGHT_GRAPH_UPDATES: int = 16


def _duckdb_at_exit_shutdown(instance: "weakref.ProxyType[DuckDBShadowStore]") -> None:
    """Called by weakref.finalize at interpreter exit if explicit aclose() was not called.

    DuckDBShadowStore keeps _shared_executor alive per Sprint 8L contract
    (for re-init safety after aclose()), but we add finalizer to ensure
    atexit cleanup if aclose() was never called.

    This is synchronous (runs in main thread at shutdown):
      1. Signal worker thread to stop via _executor.shutdown()
      2. Best-effort — DuckDB connections are complex to clean up safely

    Issue #40 fix: cancel_futures=True ensures that any pending async tasks
    (graph ingest, semantic buffering) are cancelled immediately at interpreter
    exit rather than blocking shutdown. Data in-flight at shutdown time is
    best-effort — explicit aclose() should be called for guaranteed flush.
    """
    try:
        if instance._shared_executor is not None:
            instance._shared_executor.shutdown(wait=False, cancel_futures=True)
    except Exception:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
        pass


class DuckDBShadowStore:
    # F350M-R-A1: Lightweight registry — avoids gc.get_objects() at CRITICAL/EMERGENCY
    _instances: set["DuckDBShadowStore"] = set()

    __slots__ = (
        "_initialized",
        "_closed",
        "_lazy",
        "_db_path",
        "_temp_dir",
        "_uma_state",
        "_memory_limit",
        "_max_temp",
        "_startup_ready",
        "_quality_state",
        "_duckdb_module",
        "_duckdb_settings",
        "_persistent_conn",
        "_file_conn",
        "_read_pool",
        "_read_pool_idx",
        "_wal_manager",
        "_wal_lmdb",
        "_dedup_lmdb",
        "_dedup_lmdb_path",
        "_dedup_lmdb_boot_error",
        "_dedup_lmdb_last_error",
        "_dedup_manager",
        "_semantic_store",
        "_semantic_buffer",
        "_replay_lock",
        "_startup_replay_done",
        "_bg_tasks",
        "_checkpoint_task",
        "_shared_executor",
        "_executor_semaphore",
        "_write_semaphore",
        "_write_executor",
        "_read_executor",
        "_wal_executor",
        "_duckdb_arrow_executor",
        "_executor",
        "_query_executor",
        "_temporal_anonymizer",
        "_arrow_metrics",
        "_ingest_breaker_state",
        "_ingest_breaker_failures",
        "_ingest_breaker_last_failure",
        "_ingest_breaker_cooldown",
        "_ingest_breaker_threshold",
        "_last_ingest_ts",
        "_min_flush",
        "_max_flush_interval",
        "_quality_gate",  # F360: DuckDBQualityGate (extracted from _assess_finding_quality)
        "_graph_attachment",  # F360: DuckDBGraphAttachment (replaces _graph_store lazy-init)
        "_stmt_insert_finding",
        "_stmt_insert_finding_conn_id",
        "DEAD_LETTER_PREFIX",
        "__weakref__",
        "_query_cache",
        "_finalizer",
        "_pending_accepted_findings",
        "_pending_accepted_indices",
        "_batch_start_ts",
        "_claims_enabled",
    )

    # P1-9: Canonical aclose timeout — matches DEFAULT_ACLOSE_TIMEOUT_S.
    DEFAULT_TIMEOUT_S = 10.0

    "DuckDB sidecar with RAMDISK-first / OPSEC-safe degraded mode."

    def __init__(
        self,
        db_path: Path | str | None = None,
        temp_dir: Path | str | None = None,
        uma_state: str | None = None,
        lazy: bool = True,
    ) -> None:
        """
        Initialize DuckDBShadowStore.

        Args:
            db_path:  Optional explicit DB path. If None, resolved via _resolve_path().
                     Passing a Path enables file-mode (MODE A) without requiring paths.py.
            temp_dir: Optional explicit temp directory for DuckDB scratch space.
                     Required when db_path is set; ignored for :memory: mode.
            uma_state: Optional UMA memory pressure state ("WARN", "CRITICAL", "EMERGENCY").
                     Set by resource_governor or scheduler at startup to adjust DuckDB
                     settings for memory-constrained environments (M1 EIGHTGB UMA only).
            lazy:     If True (default), DuckDB connection is deferred to first actual
                     query via ensure_connected(). __aenter__ returns immediately without
                     connecting. If False, connects eagerly in async_initialize() (legacy
                     behavior). M1 8GB: lazy=True saves ~1-2s from sprint boot.
        """
        self._lazy: bool = lazy
        self._initialized: bool = False
        self._closed: bool = False
        # F350M-R-A1: Register for madvise propagation (avoids gc.get_objects at CRITICAL)
        DuckDBShadowStore._instances.add(self)
        self._db_path: Path | None = Path(db_path) if db_path is not None else None
        self._temp_dir: Path | None = Path(temp_dir) if temp_dir is not None else None
        self._memory_limit: str = _DUCKDB_MEMORY_LIMIT
        self._max_temp: str = _DUCKDB_MAX_TEMP
        self._duckdb_module: Any | None = None
        self._uma_state: str | None = uma_state
        self._duckdb_settings: dict[str, str | int] = {}
        # M1-OPT: Use shared 'duckdb' domain executor instead of per-module TPE
        # duckdb preset = 2 workers (I/O-bound DuckDB sync operations)
        from hledac.universal.utils.domain_executors import get_or_create
        self._shared_executor: ThreadPoolExecutor = get_or_create("duckdb")
        self._write_executor: ThreadPoolExecutor = self._shared_executor
        self._read_executor: ThreadPoolExecutor = self._shared_executor
        self._wal_executor: ThreadPoolExecutor = self._shared_executor
        self._duckdb_arrow_executor: ThreadPoolExecutor = self._shared_executor
        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing

        self._executor_semaphore: asyncio.Semaphore = get_semaphore_for_testing(ConcurrencyCategory.GRAPH_RAG)
        self._write_semaphore: asyncio.Semaphore = asyncio.Semaphore(4)  # F320M-R: was 2; match _max_workers
        self._persistent_conn: Any | None = None
        self._file_conn: Any | None = None
        self._replay_lock: asyncio.Lock | None = None
        self._startup_ready: asyncio.Event = asyncio.Event()
        self._startup_replay_done: bool = False
        self._quality_state = _get_QualityAssessmentState()()
        self._arrow_metrics: dict[str, int] = {
            "arrow_selected": 0,
            "arrow_fallback_env": 0,
            "arrow_fallback_batch": 0,
            "arrow_fallback_pyarrow": 0,
            "arrow_fallback_init": 0,
            "arrow_fallback_executor": 0,
            "arrow_fallback_empty": 0,
            "arrow_fallback_all_fail": 0,
            "arrow_success_count": 0,
            "arrow_success_lmdb_count": 0,
            "arrow_success_duckdb_count": 0,
            "arrow_error_table_build": 0,
            "arrow_error_duckdb_insert": 0,
            "arrow_error_partial": 0,
            "arrow_partial_duplicates": 0,
        }
        self._wal_manager: WALManager | None = None
        from .duckdb_quality_gate import DuckDBQualityGate

        # F360M-R: DuckDBQualityGate creates its own independent lightweight state.
        # IMPORTANT: duckdb_quality_gate.QualityAssessmentState uses METHOD-based API
        # (record_accepted, record_rejected) while quality_assessment.QualityAssessmentState
        # uses DIRECT FIELD ACCESS (_quality_rejected_count += 1). These are intentionally
        # separate to avoid API mismatch. Counters stay consistent via the fact that
        # _quality_gate._assess_finding_quality() is called FIRST and its decisions
        # are reflected in the batch results that update _quality_state.
        self._quality_gate: DuckDBQualityGate = DuckDBQualityGate()
        self._dedup_manager: DedupManager | None = None
        self._wal_lmdb: Any | None = None
        self._dedup_lmdb: Any | None = None
        self._query_cache: _DuckDBQueryCache | None = None
        self._last_ingest_ts: float = 0.0
        self._pending_accepted_findings: list[CanonicalFinding] = []
        self._pending_accepted_indices: list[int] = []
        _flush_cfg = os.getenv("HLEDAC_DUCKDB_MIN_FLUSH", "50")
        self._min_flush: int = max(1, int(_flush_cfg))
        # F350M-R: Claims extraction wiring — Rust batch_extract_claims_python
        self._claims_enabled: bool = os.getenv("HLEDAC_ENABLE_CLAIMS_EXTRACTION", "0") == "1"
        _max_cfg = os.getenv("HLEDAC_DUCKDB_MAX_FLUSH_INTERVAL", "1.0")
        self._max_flush_interval: float = max(0.1, float(_max_cfg))
        self.DEAD_LETTER_PREFIX: str = "deadletter_ingest:"
        self._dedup_lmdb_path: Path | None = None
        self._dedup_lmdb_last_error: str | None = None
        self._dedup_lmdb_boot_error: str | None = None
        self._bg_tasks: BoundedTaskSet = (
            BoundedTaskSet(maxsize=_MAX_INFLIGHT_GRAPH_UPDATES) if BoundedTaskSet is not None else cast(Any, None)
        )
        self._semantic_store: Any | None = None
        self._executor = self._write_executor
        from hledac.universal.knowledge.semantic_store_buffer import SemanticStoreBuffer

        self._semantic_buffer: SemanticStoreBuffer = SemanticStoreBuffer()
        self._ingest_breaker_state: CBState = CBState.CLOSED
        self._ingest_breaker_failures: int = 0
        self._ingest_breaker_last_failure: float = 0.0
        self._ingest_breaker_cooldown: float = 30.0
        self._ingest_breaker_threshold: int = 5
        # RES-03: Automatic maintenance scheduler — op-count + time-based VACUUM/CHECKPOINT
        self._vacuum_interval_ops: int = 10000  # VACUUM every 10K write ops
        self._checkpoint_interval_ops: int = 5000  # CHECKPOINT every 5K ops
        self._write_op_counter: int = 0
        self._last_vacuum_time: float = 0.0
        self._last_checkpoint_time: float = 0.0
        self._vacuum_interval_seconds: float = 3600.0  # 1 hour
        self._checkpoint_interval_seconds: float = 1800.0  # 30 min
        self._checkpoint_task: asyncio.Task | None = None
        # RES-04: LMDB compaction timer (initialized in _maintenance_loop)
        self._last_lmdb_compact_time: float = 0.0
        self._read_pool: list[Any] = []
        self._read_pool_idx: int = 0
        self._adjust_executor_pool()
        try:
            # ISSUE 2.1 fix: use weakref.proxy to avoid strong-ref cycle
            # weakref.finalize holds a strong ref to self via the callback arg,
            # creating a cycle (self → _finalizer → callback(self)) → never GC'd
            # until atexit. proxy(self) breaks the cycle while still allowing
            # the callback to access the instance.
            self._finalizer = weakref.finalize(self, _duckdb_at_exit_shutdown, weakref.proxy(self))
            atexit.register(self._finalizer)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            self._finalizer = None

    def _adjust_executor_pool(self) -> None:
        """
        Adjust _shared_executor worker count based on M1 UMA memory pressure.

        F300S: Reduced defaults for M1 8GB UMA:
          CRITICAL/EMERGENCY: 1 worker (~50 MB saved vs 2 workers baseline)
          SOFT_WARN: 1 worker (conservative, leaves headroom for MLX)
          OK: 2 workers (baseline, set at __init__)

        F285-U1: Unified executor — all 4 former pools are now _shared_executor aliases.
        This method adjusts the single shared pool's max_workers.

        This is a best-effort advisory — executor is NOT restarted, only the
        reference to max_workers is capped for future task submissions.
        Thread count change takes effect on the NEXT submit() call.

        Lazy import of resource_governor to avoid circular deps and cold-start cost.
        """
        try:
            from hledac.universal.core.resource_governor import sample_uma_status

            snap = sample_uma_status()
            state = snap.state if snap else "ok"
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            state = "ok"
        if state in ("critical", "emergency", "soft_warn"):
            target_workers = 1
        else:
            target_workers = 3
        if hasattr(self, "_shared_executor") and self._shared_executor is not None:
            try:
                current = self._shared_executor._max_workers
                if current != target_workers:
                    self._shared_executor._max_workers = target_workers
                    _dbg = logging.getLogger(__name__)
                    _dbg.debug("[DuckDB] executor workers: %d -> %d (uma_state=%s)", current, target_workers, state)
            except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                pass

    @property
    def _accepted_count(self) -> int:
        return self._quality_state._accepted_count

    @property
    def _quality_duplicate_count(self) -> int:
        return self._quality_state._quality_duplicate_count

    @property
    def _quality_rejected_count(self) -> int:
        return self._quality_state._quality_rejected_count

    @property
    def _persistent_duplicate_count(self) -> int:
        return self._quality_state._persistent_duplicate_count

    @classmethod
    def for_testing(cls, *, name: str = "test", temp_dir: Path | str | None = None) -> DuckDBShadowStore:
        """
        Create a DuckDB store for test isolation.

        Not for production use - provides a predictable temp path that is
        cleaned up by the caller after the test.

        Args:
            name:  Identifier used in the temp path (default "test").
                  Pass unique names per test to avoid collisions.
            temp_dir:  Optional temp directory. If None, a temp dir is created
                      via tempfile.mkdtemp and the caller is responsible for
                      cleaning it up.
        """
        import tempfile

        td = temp_dir or Path(tempfile.mkdtemp(prefix="hledac_test_duckdb_"))
        if isinstance(td, str):
            td = Path(td)
        td.mkdir(parents=True, exist_ok=True)
        store_path = td / f"{name}.duckdb"
        return cls(db_path=str(store_path))

    def set_uma_state(self, uma_state: str | None, swap_detected: bool = False) -> None:
        """
        Set or update UMA memory pressure state at runtime.

        Can be called while the store is open to adjust DuckDB settings.
        Resolves new settings and applies to all connections immediately.

        Args:
            uma_state: "WARN", "CRITICAL", "EMERGENCY", or None for normal.
            swap_detected: True if system-level swap is active.
        """
        self._uma_state = uma_state
        self._duckdb_settings = _resolve_duckdb_runtime_settings(uma_state, swap_detected)

    def get_uma_state(self) -> str | None:
        """Return currently configured UMA state."""
        return self._uma_state

    def get_stats(self) -> dict[str, Any]:
        """
        Sprint P2-B: Return store statistics for sprint report.

        Returns duckdb_stats section: findings count, graph stats,UMA state.
        """
        try:
            graph_stats = self.get_graph_stats() if self._graph_attachment is not None else {}
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            graph_stats = {}
        total_iocs = graph_stats.get("nodes", 0) if isinstance(graph_stats, dict) else 0
        try:
            conn = self._qe()._conn if hasattr(self, "_qe") else None
            total_findings = conn.execute("SELECT COUNT(*) FROM canonical_findings").fetchone()[0] if conn else 0
        except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
            total_findings = 0
        return {
            "total_findings": total_findings,
            "total_iocs": total_iocs,
            "graph_stats": graph_stats,
            "uma_state": self._uma_state or "unknown",
            "duckdb_mode": getattr(self, "_duckdb_mode", "unknown"),
            "ingest_breaker": {
                "state": self._ingest_breaker_state.value if hasattr(self, "_ingest_breaker_state") else "unknown",
                "failures": self._ingest_breaker_failures if hasattr(self, "_ingest_breaker_failures") else 0,
                "last_failure_age_s": round(_time.monotonic() - self._ingest_breaker_last_failure, 1)
                if hasattr(self, "_ingest_breaker_last_failure") and self._ingest_breaker_last_failure > 0
                else None,
            },
        }

    # F360: Graph attachment via extracted DuckDBGraphAttachment
    # Replaces 15 deprecated thin wrappers (inject_graph, get_graph_stats, etc.)
    # NOTE: _graph_attachment is declared in __slots__ (L1843) — do NOT add class-level default here
    def _ensure_graph_attachment(self) -> Any:
        """Lazy-init DuckDBGraphAttachment."""
        if self._graph_attachment is None:
            from hledac.universal.knowledge.duckdb_graph_attachment import DuckDBGraphAttachment

            self._graph_attachment = DuckDBGraphAttachment()
        return self._graph_attachment

    def inject_graph(self, graph: Any) -> None:
        """Inject DuckPGQGraph or IOCGraph for entity enrichment."""
        self._ensure_graph_attachment().inject_graph(graph)

    def get_graph_attachment_kind(self) -> str | None:
        """Return kind of attached graph or None."""
        return self._ensure_graph_attachment().get_graph_attachment_kind()

    def graph_supports_buffered_writes(self) -> bool:
        """Return True if attached graph supports buffered writes."""
        return self._ensure_graph_attachment().graph_supports_buffered_writes()

    def inject_stix_graph(self, graph: Any) -> None:
        """Inject STIX synthesis graph."""
        self._ensure_graph_attachment().inject_stix_graph(graph)

    def get_stix_graph(self) -> Any:
        """Return attached STIX graph."""
        return self._ensure_graph_attachment().get_stix_graph()

    def inject_truth_write_graph(self, graph: Any) -> None:
        """Inject DuckPGQGraph for truth write path."""
        self._ensure_graph_attachment().inject_truth_write_graph(graph)

    def get_truth_write_graph(self) -> Any:
        """Return attached truth-write graph."""
        return self._ensure_graph_attachment().get_truth_write_graph()

    def truth_write_graph_supports_buffered_writes(self) -> bool:
        """Return True if truth-write graph supports buffered writes."""
        return self._ensure_graph_attachment().truth_write_graph_supports_buffered_writes()

    def get_top_seed_nodes(self, n: int = 5) -> list[dict[str, Any]]:
        """Return top N seed nodes for graph traversal."""
        return self._ensure_graph_attachment().get_top_seed_nodes(n=n)

    def get_graph_stats(self) -> dict[str, Any]:
        """Return graph stats (nodes, edges, pgq_available)."""
        return self._ensure_graph_attachment().get_graph_stats()

    def get_connected_iocs(self, ioc_value: str, max_hops: int = 2) -> list[dict[str, Any]]:
        """Return IOC nodes connected to given IOC within max_hops."""
        return self._ensure_graph_attachment().get_connected_iocs(ioc_value, max_hops=max_hops)

    def get_connected_iocs_batch(self, values: list[str], max_hops: int = 2) -> dict[str, list[dict[str, Any]]]:
        """Batch graph traversal for multiple IOC values."""
        return self._ensure_graph_attachment().get_connected_iocs_batch(values, max_hops=max_hops)

    def annotate_findings_with_graph_context(
        self, findings: list[Any], max_hops: int = 2, max_annotations: int = 50
    ) -> list[Any]:
        """Enrich findings with graph-derived context (aliases, relationships)."""
        return self._ensure_graph_attachment().annotate_findings_with_graph_context(
            findings, max_hops=max_hops, max_annotations=max_annotations
        )

    def get_analytics_graph_for_synthesis(self) -> Any:
        """Return analytics graph for synthesis layer."""
        return self._ensure_graph_attachment().get_analytics_graph_for_synthesis()

    def get_top_entities_for_ghost_global(self, n: int = 100) -> list[tuple[str, str, float]]:
        """Return top N entities for ghost global identity resolution."""
        return self._ensure_graph_attachment().get_top_entities_for_ghost_global(n=n)

    async def get_top_findings(self, limit: int = 10) -> list[dict]:
        """
        Sprint 8VE B.4: Return top findings by confidence for IOC graph display.

        Queries canonical_findings ordered by confidence DESC, returns dicts
        with ioc, source_type, query, and confidence fields.
        """
        try:
            conn = self._qe()._conn if hasattr(self, "_qe") else None
            if conn is None:
                return []
            from time import perf_counter_ns

            t0 = perf_counter_ns()
            arrow_table = conn.execute(
                "\n                SELECT id, query, source_type, confidence, ts, payload_text\n                FROM canonical_findings\n                ORDER BY confidence DESC, ts DESC\n                LIMIT ?\n                ",
                [limit],
            ).fetch_arrow_table()
            DuckDBShadowStore._record_query_latency(self._qe()._query_latencies_ns, perf_counter_ns() - t0)
            if arrow_table is None or arrow_table.num_rows == 0:
                return []
            # M1 8GB: fetch_arrow_table() zero-copy Arrow C Data Interface — no Python intermediary
            return [
                {
                    "ioc": row["id"],
                    "query": row["query"],
                    "source_type": row["source_type"],
                    "confidence": row["confidence"],
                    "ts": row["ts"],
                    "summary": (row["payload_text"] or "")[:200],
                }
                for row in arrow_table.to_pylist()
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []

    def inject_semantic_store(self, store: Any) -> None:
        """
        Sprint 8SB: Inject SemanticStore instance for semantic buffering of findings.

        The store is used to buffer findings for FastEmbed embedding + LanceDB
        indexing during WINDUP flush.
        """
        self._semantic_store = store
        self._semantic_buffer.inject(store)

    def _semantic_buffer_findings(self, findings: list[CanonicalFinding]) -> None:
        """
        Sprint 8SB: Buffer findings into SemanticStore for batch embedding.

        Runs as a background task (not awaited). Fail-open: any exception
        is caught and logged - semantic buffering failure never blocks storage.
        Delegated to SemanticStoreBuffer.
        """
        self._semantic_buffer.buffer_findings(findings)

    async def _graph_ingest_findings(self, findings: list[CanonicalFinding]) -> None:
        """
        Background task: ingest findings into IOC graph.

        Called via _bg_tasks tracking after async_ingest_findings_batch succeeds.
        Fail-open: any exception is caught and logged.

        Architecture (P0 Batch IOC):
            1. Batch extract IOCs from all findings in parallel (4-thread pool)
            2. Collect all (ioc_type, value) tuples → batch buffer_ioc calls
            3. Collect all observations → batch buffer_observation calls
            4. O(n) per-finding extraction → O(1) batched graph writes
        """
        truth_graph = self._ensure_graph_attachment().get_truth_write_graph()
        if truth_graph is None:
            return

        async def _run() -> None:
            try:
                import xxhash

                from hledac.universal.utils.ioc_extract import extract_iocs_batch

                extraction_items: list[tuple[str, list[tuple[str, str]]]] = []
                for finding in findings:
                    text = finding.payload_text or ""
                    pm = getattr(finding, "pattern_matches", None)
                    matches: list[tuple[str, str]] = []
                    if pm and isinstance(pm, list):
                        for item in pm:
                            if isinstance(item, tuple) and len(item) == 2:
                                matches.append((str(item[0]), str(item[1])))
                            elif isinstance(item, dict):
                                v = item.get("value") or item.get("pattern") or ""
                                label = item.get("label") or ""
                                matches.append((str(v), str(label)))
                    extraction_items.append((text, matches))
                all_ioc_results = extract_iocs_batch(extraction_items)
                if not any((r for r in all_ioc_results)):
                    return
                all_iocs: list[tuple[str, str]] = []
                finding_observations: list[tuple[int, str, str, float, str]] = []
                for finding_idx, (finding, iocs) in enumerate(zip(findings, all_ioc_results, strict=False)):
                    if not iocs:
                        continue
                    ts = finding.ts
                    src = finding.source_type
                    fid = str(finding.finding_id)
                    id_map: dict[str, str] = {}
                    for value, ioc_type in iocs:
                        all_iocs.append((ioc_type, value))
                        ioc_id = f"{ioc_type}:{xxhash.xxh64(value.encode()).hexdigest()}"
                        id_map[value] = ioc_id
                    values = list(id_map.keys())
                    for i, v_a in enumerate(values):
                        id_a = id_map[v_a]
                        for v_b in values[i + 1 :]:
                            id_b = id_map[v_b]
                            finding_observations.append((finding_idx, id_a, id_b, ts, src))
                seen_iocs: set[tuple[str, str]] = set()
                for ioc_type, value in all_iocs:
                    ioc_key = (ioc_type, value)
                    if ioc_key not in seen_iocs:
                        await truth_graph.buffer_ioc(ioc_type, value, 1.0)
                        seen_iocs.add(ioc_key)
                for _, id_a, id_b, ts, src in finding_observations:
                    await truth_graph.buffer_observation(id_a, id_b, fid, ts, src)
            except Exception as e:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
                import logging

                _logger2 = logging.getLogger(__name__)
                _logger2.warning(f"[F206AC] truth_write_graph buffer failed: {e}")

        await self._bg_tasks.spawn(_run(), name="duckdb:truth_write_graph")

    REPLAY_CHUNK_SIZE: int = 100
    MAX_RETRY_COUNT: int = 3
    DEADLETTER_PREFIX: str = "deadletter_duckdb_sync:"
    MAX_PENDING_SYNC_MARKERS: int = 10000

    def _configure_connection(self, conn: Any, runtime: dict[str, Any], *, is_read_only: bool = False) -> None:
        """
        Apply all PRAGMAs/SETs to a DuckDB connection. DRY — called once per connection
        in _init_connection.

        F231: UMA-aware configuration via _resolve_duckdb_runtime_settings().
        F265B: WAL pragmas (file-backed DB only; N/A for :memory:).
        F273F: madvise/F_NOCACHE for zero-copy mmap reads (file-backed only).
        Idempotent — safe to call on any freshly-connected DuckDB connection.
        """
        _db_path = self._db_path
        _is_memory_mode = _db_path is None or str(_db_path) == ":memory:"
        if not _is_memory_mode and _db_path is not None:
            madv_nocache_on_path(_db_path)
            apply_nocache_to_path(_db_path)
        memory_limit_val = _validate_duckdb_setting(str(runtime["memory_limit"]), "memory_limit")
        max_temp_val = _validate_duckdb_setting(self._max_temp, "max_temp")
        conn.execute("SET memory_limit = ?", [memory_limit_val])
        # ISSUE-35: hard_memory_limit is a ceiling DuckDB cannot exceed.
        # On M1 8GB, 1GB ceiling ensures DuckDB never steals from MLX inference budget.
        # DuckDB >= 1.2 replaced hard_memory_limit PRAGMA with SET memory_limit;
        # fail-soft so newer versions still initialize correctly.
        try:
            conn.execute("PRAGMA hard_memory_limit = ?", [_DUCKDB_HARD_MEMORY_LIMIT])
        except Exception:  # noqa: BLE001 — fail-soft; DuckDB version compatibility; non-critical
            logger.debug(f"[DUCKDB] hard_memory_limit PRAGMA not available, skipping")
        conn.execute("SET max_temp_directory_size = ?", [max_temp_val])
        if self._temp_dir is not None and (not is_read_only) and (not _is_memory_mode):
            temp_dir_val = _validate_path_setting(self._temp_dir, "temp_directory")
            conn.execute("SET temp_directory = ?", [temp_dir_val])
        conn.execute("PRAGMA threads = ?", [_validate_duckdb_threads(runtime["threads"])])
        conn.execute("PRAGMA enable_progress_bar=false")
        conn.execute("PRAGMA enable_object_cache=false")
        # F350M-R: Load FTS5 + HNSW extensions for Phase 1 vector/FTS tables.
        # Fail-soft — extensions may not be available in all builds; vector/FTS
        # methods fall back to sequential/LIKE search gracefully.
        for _ext_sql in (
            "LOAD fts5",
            "LOAD hnsw",
            "INSTALL hnsw_cosine",
        ):
            try:
                conn.execute(_ext_sql)
            except Exception:  # noqa: BLE001 — best-effort; extension unavailable; non-critical
                pass
        if not _is_memory_mode:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA wal_autocheckpoint=51200")
            except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                logger.debug(f"[DUCKDB] WAL/busy_timeout config failed: {e!r}")
        try:
            conn.execute(
                "SET write_buffer_row_group_memory_limit = ?", [str(runtime.get("write_buffer_limit", "64MiB"))]
            )
            conn.execute("SET allocator_flush_threshold = ?", [str(runtime.get("allocator_flush_threshold", "64MiB"))])
            conn.execute(
                "SET allocator_bulk_deallocation_flush_threshold = ?",
                [str(runtime.get("allocator_bulk_dealloc_threshold", "256MiB"))],
            )
            conn.execute("SET enable_fsst_vectors = ?", [str(runtime.get("enable_fsst_vectors", "true")).lower()])
            if not _is_memory_mode:
                conn.execute(
                    "SET temp_file_encryption = ?", [str(runtime.get("temp_file_encryption", "false")).lower()]
                )
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            logger.debug(f"[DUCKDB] columnar/allocator tuning failed: {e}")

    def _init_connection(self) -> None:
        """
        Initialize the DuckDB connection. Must be called from the worker thread.
        Sets up file or :memory: mode, applies PRAGMAs and schema.
        For file mode, creates persistent _file_conn (Sprint 7H).

        F231: Uses _resolve_duckdb_runtime_settings() for UMA-aware configuration.
        DRY: All PRAGMA/SET configuration consolidated in _configure_connection().
        """
        duckdb = _get_duckdb()
        runtime = _resolve_duckdb_runtime_settings(self._uma_state, swap_detected=False)
        self._duckdb_settings = runtime
        resolved_memory = runtime["memory_limit"]
        resolved_threads = runtime["threads"]

        # Determine lock mode for file-based DB
        _read_only_flag = self._resolve_lock_mode()

        # Early return: no database path means :memory: mode
        if not self._db_path:
            self._init_memory_mode(duckdb, runtime, resolved_memory, resolved_threads)
            return

        # File mode initialization
        self._init_file_mode(duckdb, runtime, resolved_threads, _read_only_flag)

    def _resolve_lock_mode(self) -> bool:
        """Resolve process lock and return read_only flag. Early return if falling back to memory."""
        if not self._db_path or str(self._db_path) == ":memory:":
            return False
        _lock_mode, _lock_msg = self._acquire_process_lock()
        if _lock_mode == "excl":
            logger.debug(f"[duckdb_init] Exclusive lock acquired: {_lock_msg}")
            return False
        if _lock_mode == "ro":
            logger.warning(f"[duckdb_init] {_lock_msg} — operating in READ-ONLY mode")
            return True
        logger.warning(f"[duckdb_init] {_lock_msg} — falling back to :memory: mode")
        self._db_path = None
        return False

    def _init_file_mode(self, duckdb, runtime, resolved_threads: int, read_only: bool) -> None:
        """Initialize file-based DuckDB connection with schema and read pool."""
        is_memory_mode = str(self._db_path) == ":memory:"
        if not is_memory_mode:
            self._setup_temp_dir()
        self._create_schema_and_migrate(duckdb, runtime, read_only)
        self._create_file_connection(duckdb, runtime, read_only)
        self._create_read_pool(duckdb, runtime)

    def _setup_temp_dir(self) -> None:
        """Ensure temp directory exists for file-based DuckDB."""
        if self._temp_dir is None:
            self._temp_dir = self._db_path.expanduser().parent / "duckdb_tmp"
        self._temp_dir.mkdir(parents=True, exist_ok=True)

    def _create_schema_and_migrate(self, duckdb, runtime, read_only: bool) -> None:
        """Create schema and run migrations on setup connection, then close it."""
        setup_conn = duckdb.connect(str(self._db_path), read_only=read_only)
        # SEC-02: enforce 0o600 on DuckDB data files immediately after creation
        _harden_duckdb_permissions(self._db_path)
        try:
            self._configure_connection(setup_conn, runtime, is_read_only=read_only)
            if not read_only:
                _apply_schema(setup_conn, _SCHEMA_SQL)
                self._run_post_schema_migrations()
        finally:
            self._safe_close(setup_conn)

    def _run_post_schema_migrations(self) -> None:
        """Run versioned migrations after schema init and invalidate caches."""
        SchemaMigrator(_get_connection()).migrate()
        qe = self._qe()
        if qe is not None:
            qe._invalidate_insert_stmt()
        if self._query_cache is not None:
            self._query_cache.invalidate()

    def _create_file_connection(self, duckdb, runtime, read_only: bool) -> None:
        """Create and configure the main file connection."""
        self._file_conn = duckdb.connect(str(self._db_path), read_only=read_only)
        # SEC-02: enforce 0o600 on DuckDB data files immediately after creation
        _harden_duckdb_permissions(self._db_path)
        if instrument_duckdb_connection:
            self._file_conn = instrument_duckdb_connection(self._file_conn)
        self._configure_file_connections(runtime)

    def _configure_file_connections(self, runtime) -> None:
        """Configure file connection with PRAGMAs and checkpoint."""
        try:
            self._configure_connection(self._file_conn, runtime, is_read_only=False)
            self._set_preserve_insertion_order(self._file_conn)
            self._file_conn.execute("PRAGMA force_checkpoint")
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            self._file_conn.close()
            raise

    def _set_preserve_insertion_order(self, conn) -> None:
        """Set preserve_insertion_order=false with best-effort error handling."""
        try:
            conn.execute("SET preserve_insertion_order = false")
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            logger.debug(f"[DUCKDB] preserve_insertion_order config failed: {e}")

    def _create_read_pool(self, duckdb, runtime) -> None:
        """Create read connection pool (3 read-only connections)."""
        self._read_pool = []
        self._read_pool_idx = 0
        for i in range(3):
            if not self._add_read_pool_connection(duckdb, runtime, i):
                break

    def _add_read_pool_connection(self, duckdb, runtime, index: int) -> bool:
        """Add a single read-only connection to the read pool. Returns True on success."""
        try:
            read_conn = duckdb.connect(str(self._db_path), read_only=True)
            # SEC-02: enforce 0o600 on DuckDB data files immediately after creation
            _harden_duckdb_permissions(self._db_path)
            if instrument_duckdb_connection:
                read_conn = instrument_duckdb_connection(read_conn)
            self._configure_connection(read_conn, runtime, is_read_only=True)
            read_conn.execute("SET preserve_insertion_order = false")
            self._read_pool.append(read_conn)
            return True
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            logger.debug(f"[DUCKDB] read pool connection {index} failed: {e}")
            return False

    def _init_memory_mode(self, duckdb, runtime, resolved_memory: str, resolved_threads: int) -> None:
        """Initialize :memory: DuckDB connection with full configuration."""
        _conn = duckdb.connect(":memory:")
        try:
            self._configure_memory_connection(_conn, runtime, resolved_memory, resolved_threads)
            _apply_schema(_conn, _SCHEMA_SQL)
            self._persistent_conn = _conn
            self._init_query_cache_if_needed()
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            _conn.close()
            raise

    def _configure_memory_connection(self, conn, runtime, resolved_memory: str, resolved_threads: int) -> None:
        """Apply all configuration settings to an in-memory connection."""
        memory_limit_val = _validate_duckdb_setting(str(resolved_memory), "memory_limit")
        conn.execute("SET memory_limit = ?", [memory_limit_val])
        self._apply_hard_memory_limit(conn)
        self._apply_temp_directory_settings(conn)
        conn.execute("PRAGMA threads = ?", [_validate_duckdb_threads(resolved_threads)])
        conn.execute("PRAGMA enable_progress_bar=false")
        conn.execute("PRAGMA enable_object_cache=false")
        self._apply_allocator_tuning(conn, runtime)
        self._set_preserve_insertion_order(conn)

    def _apply_hard_memory_limit(self, conn) -> None:
        """Apply hard_memory_limit ceiling (M1 8GB: 1GB max). Fail-soft on version compatibility."""
        try:
            conn.execute("PRAGMA hard_memory_limit = ?", [_DUCKDB_HARD_MEMORY_LIMIT])
        except Exception:  # noqa: BLE001 — fail-soft; DuckDB version compatibility; non-critical
            logger.debug(f"[DUCKDB] hard_memory_limit PRAGMA not available, skipping")

    def _apply_temp_directory_settings(self, conn) -> None:
        """Configure temp directory for in-memory mode (ramdisk or disabled)."""
        if _DUCKDB_RAMDISK_TEMP:
            temp_dir_val = _validate_path_setting(Path(_DUCKDB_RAMDISK_TEMP), "temp_directory")
            conn.execute("SET temp_directory = ?", [temp_dir_val])
            conn.execute("SET max_temp_directory_size = '4GB'")
        else:
            conn.execute("SET max_temp_directory_size = '0GB'")

    def _apply_allocator_tuning(self, conn, runtime) -> None:
        """Apply allocator and columnar store tuning settings."""
        try:
            conn.execute("SET allocator_flush_threshold = ?", [str(runtime.get("allocator_flush_threshold", "64MiB"))])
            conn.execute("SET allocator_bulk_deallocation_flush_threshold = ?",
                         [str(runtime.get("allocator_bulk_dealloc_threshold", "256MiB"))])
            conn.execute("SET enable_fsst_vectors = ?", [str(runtime.get("enable_fsst_vectors", "true")).lower()])
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            logger.debug(f"[DUCKDB] columnar/allocator tuning failed: {e}")

    def _init_query_cache_if_needed(self) -> None:
        """Initialize LMDB-backed query cache if path is available."""
        if self._query_cache is None and self._db_path is not None:
            from hledac.universal.paths import LMDB_ROOT
            _cache_lmdb_path = LMDB_ROOT / "duckdb_query_cache.lmdb"
            self._query_cache = _DuckDBQueryCache(
                _cache_lmdb_path,
                max_l1=_DUCKDB_QUERY_CACHE_L1_MAX,
                max_l2=_DUCKDB_QUERY_CACHE_L2_MAX,
                ttl_s=_DUCKDB_QUERY_CACHE_TTL_S,
            )

    def _safe_close(self, conn) -> None:
        """Safely close a connection with best-effort error handling."""
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                pass

    def _apply_schema_migrations(self) -> None:
        """
        DEPRECATED — P0-8: replaced by SchemaMigrator.migrate() in _init_connection().

        ALTER TABLE ADD COLUMN for any sprint_delta columns missing from old DBs.
        DuckDB does not have IF NOT EXISTS for ALTER, so we catch and ignore errors.

        NOTE: This method is NO LONGER CALLED. It is kept for backward compatibility
        only. The canonical migration path is now:
          1. _init_connection() calls _apply_schema(setup_conn, _SCHEMA_SQL)
          2. _init_connection() then calls SchemaMigrator(setup_conn).migrate()
          3. Caller of _init_connection() invalidates _query_cache after return

        Sprint F192F §2: findings_per_min -> findings_per_minute rename.
        Migration order matters - add new column first, then handle legacy column:
          1. Add findings_per_minute (new canonical name, matches sprint_scorecard)
          2. Add top_source_type (may already exist on very old DBs)
          3. Add synthesis_confidence (may already exist on very old DBs)
        Legacy findings_per_min column is retained but not written to (inserts use
        findings_per_minute). Queries read findings_per_minute which is populated
        by current insert logic.
        """
        if self._db_path is None:
            return
        if self._query_cache is not None:
            self._query_cache.invalidate()
        duckdb = _get_duckdb()
        conn = duckdb.connect(str(self._db_path))
        # SEC-02: enforce 0o600 on DuckDB data files immediately after creation
        _harden_duckdb_permissions(self._db_path)
        try:
            conn.execute("SET memory_limit = '1GB'")
            conn.execute("PRAGMA threads = 2")
            conn.execute("SET preserve_insertion_order = false")
            madv_nocache_on_path(self._db_path)
            apply_nocache_to_path(self._db_path)
            try:
                conn.execute("ALTER TABLE sprint_delta ADD COLUMN findings_per_minute REAL DEFAULT 0")
            except (OSError, RuntimeError) as e:
                logger.debug(f"[DUCKDB] ADD COLUMN findings_per_minute failed: {e}")
            try:
                conn.execute("ALTER TABLE sprint_delta ADD COLUMN top_source_type TEXT")
            except (OSError, RuntimeError) as e:
                logger.debug(f"[DUCKDB] ADD COLUMN top_source_type failed: {e}")
            try:
                conn.execute("ALTER TABLE sprint_delta ADD COLUMN synthesis_confidence REAL DEFAULT 0")
            except (OSError, RuntimeError) as e:
                logger.debug(f"[DUCKDB] ADD COLUMN synthesis_confidence failed: {e}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                    pass

    def ensure_target_profiles_schema(self) -> None:
        """
        Sprint F202K: Ensure target_profiles table exists in DuckDB.
        Safe to call multiple times - uses CREATE TABLE IF NOT EXISTS.
        Must be called after _init_connection (connection must exist).
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return
            conn.execute(
                "\n                CREATE TABLE IF NOT EXISTS target_profiles (\n                    target_id TEXT PRIMARY KEY,\n                    first_seen DOUBLE,\n                    last_seen DOUBLE,\n                    cumulative_finding_count INTEGER,\n                    entity_summary_json TEXT\n                )\n                "
            )
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            pass

    def ensure_target_memory_schema(self) -> None:
        """
        Sprint F204D: Ensure target_memory table exists in DuckDB.
        Safe to call multiple times - uses CREATE TABLE IF NOT EXISTS.
        Must be called after _init_connection (connection must exist).
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return
            conn.execute(
                "\n                CREATE TABLE IF NOT EXISTS target_memory (\n                    target_id TEXT PRIMARY KEY,\n                    first_seen_ts DOUBLE,\n                    last_seen_ts DOUBLE,\n                    sprint_count INTEGER,\n                    cumulative_finding_count INTEGER,\n                    entity_facets_json TEXT,\n                    exposure_facets_json TEXT,\n                    pivot_facets_json TEXT,\n                    confidence_drift_json TEXT,\n                    updated_by_sprint_id TEXT,\n                    updated_ts DOUBLE\n                )\n                "
            )
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            pass

    async def async_ingest_dht_metadata(self, metadata: list[dict[str, Any]]) -> int:
        """
        Sprint F224A: Ingest DHT metadata from torrent discovery.

        Args:
            metadata: List of DHT metadata dicts with keys:
                - infohash: str (required, primary key)
                - name: str (optional)
                - files: list[str] (optional, stored as JSON)
                - size_bytes: int (optional)
                - first_seen: float (optional, defaults to now)
                - last_seen: float (optional, defaults to now)
                - peer_count: int (optional)
                - sources: list[str] (optional, stored as JSON)

        Returns:
            Number of records ingested
        """
        if not metadata:
            return 0
        if not self._initialized or self._closed:
            return 0
        self.ensure_connected()
        _ = asyncio.get_running_loop()
        now = _time.time()

        def _sync_ingest() -> int:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return 0
            conn.execute(
                "\n                CREATE TABLE IF NOT EXISTS dht_metadata (\n                    infohash TEXT PRIMARY KEY,\n                    name TEXT,\n                    files_json TEXT,\n                    size_bytes BIGINT,\n                    first_seen DOUBLE,\n                    last_seen DOUBLE,\n                    peer_count INT,\n                    sources_json TEXT\n                )\n            "
            )
            count = 0
            rows: list[tuple] = []
            for m in metadata:
                infohash = m.get("infohash", "")
                if not infohash:
                    continue
                files_json = _json_dumps_str(m.get("files")) if m.get("files") else None
                sources_json = _json_dumps_str(m.get("sources")) if m.get("sources") else None
                rows.append(
                    (
                        infohash,
                        m.get("name"),
                        files_json,
                        m.get("size_bytes"),
                        m.get("first_seen", now),
                        m.get("last_seen", now),
                        m.get("peer_count"),
                        sources_json,
                    )
                )
            if rows:
                conn.executemany(
                    "\n                    INSERT INTO dht_metadata (\n                        infohash, name, files_json, size_bytes,\n                        first_seen, last_seen, peer_count, sources_json\n                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)\n                    ON CONFLICT(infohash) DO UPDATE SET\n                        name = COALESCE(excluded.name, dht_metadata.name),\n                        files_json = COALESCE(excluded.files_json, dht_metadata.files_json),\n                        size_bytes = COALESCE(excluded.size_bytes, dht_metadata.size_bytes),\n                        last_seen = excluded.last_seen,\n                        peer_count = COALESCE(excluded.peer_count, dht_metadata.peer_count),\n                        sources_json = COALESCE(excluded.sources_json, dht_metadata.sources_json)\n                    ",
                    rows,
                )
                count = len(rows)
            conn.commit()
            return count

        from hledac.universal.runtime.worker_pool import get_rust_pool
        pool = get_rust_pool("io")
        return await pool.submit(_sync_ingest)

    # F360M-R: _DuckDBQueryExecutor extracted to knowledge/query_executor.py
    # Using alias to maintain backward compatibility with existing code references
    _DuckDBQueryExecutor = DuckDBQueryExecutor

    @staticmethod
    def _record_query_latency(latencies: list[float], elapsed_ns: float) -> None:
        """Record a DuckDB query latency to MetricsRegistry (fail-safe)."""
        try:
            ms = elapsed_ns / 1000000
            latencies.append(ms)
            if len(latencies) > 1000:
                latencies[:] = latencies[-1000:]
            avg = sum(latencies) / len(latencies)
            from hledac.universal.metrics_registry import get_metrics_registry

            get_metrics_registry().set_gauge("duckdb_query_latency_ms", avg)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            pass

    def _qe(self):
        """Lazy executor - created on first _sync_* access, shared for instance lifetime."""
        if not hasattr(self, "_query_executor"):
            object.__setattr__(self, "_query_executor", self._DuckDBQueryExecutor(self))
        return self._query_executor

    def _sync_insert_finding(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
        ts: float | None = None,
        provenance_json: str | None = None,
    ) -> bool:
        """Sync insert - MUST be called on the worker thread."""
        return self._qe().insert_finding(finding_id, query, source_type, confidence, ts, provenance_json)

    def _sync_insert_findings_bulk(self, findings: list[dict[str, Any]]) -> int:
        """
        Sprint 7H: True bulk insert using executemany in explicit transaction.
        MUST be called on the worker thread.
        Returns number of successfully inserted records.
        """
        return self._qe().insert_findings_bulk(findings)

    def _sync_insert_run(
        self, run_id: str, started_at: float | None, ended_at: float | None, total_fds: int, rss_mb: int
    ) -> bool:
        """Sync insert run - MUST be called on the worker thread."""
        return self._qe().insert_run(run_id, started_at, ended_at, total_fds, rss_mb)

    def _sync_query_findings(self, limit: int) -> list[dict[str, Any]]:
        """Sync query - MUST be called on the worker thread."""
        return self._qe().query_findings(limit)

    def _sync_upsert_target_profile(self, profile: TargetProfileSummary) -> None:
        """Sync upsert - MUST be called on the worker thread."""
        self._qe().upsert_target_profile(profile)

    def _sync_get_target_profile(self, target_id: str) -> TargetProfileSummary | None:
        """Sync get - MUST be called on the worker thread. Returns None if not found."""
        result = self._qe().get_target_profile(target_id)
        if result is None:
            return None
        try:
            return TargetProfileSummary(
                target_id=result[0],
                first_seen=result[1],
                last_seen=result[2],
                cumulative_finding_count=result[3],
                entity_summary_json=result[4],
            )
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return None

    def _sync_record_hypothesis_feedback(self, record: Any) -> bool:
        """
        Sprint F203G: Insert a single hypothesis_feedback record.

        Thread-safe: MUST be called on the duckdb_worker thread.
        Silently fails if store is closed or uninitialized.

        Returns True if inserted, False otherwise.
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return False
            conn.execute(
                "\n                INSERT INTO hypothesis_feedback\n                (id, target_id, pivot_type, ioc_type, produced_count, accepted_count, signal_value, ts)\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?)\n                ",
                [
                    record.id,
                    record.target_id,
                    record.pivot_type,
                    record.ioc_type,
                    record.produced_count,
                    record.accepted_count,
                    record.signal_value,
                    record.ts,
                ],
            )
            return True
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            logger.warning(f"[F206L] _sync_record_hypothesis_feedback failed for {record.id}: {e}")
            return False

    def _sync_get_hypothesis_feedback(self, target_id: str | None, limit: int) -> list[dict]:
        """
        Sprint F203G: Fetch hypothesis_feedback records ordered by ts DESC.

        Thread-safe: MUST be called on the duckdb_worker thread.

        Args:
            target_id: If provided, filter by target_id. If None, returns all.
            limit: Maximum number of records to return.

        Returns:
            List of dicts with keys: id, target_id, pivot_type, ioc_type,
            produced_count, accepted_count, signal_value, ts.
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            if target_id:
                sql = "\n                    SELECT id, target_id, pivot_type, ioc_type,\n                           produced_count, accepted_count, signal_value, ts\n                    FROM hypothesis_feedback\n                    WHERE target_id = ?\n                    ORDER BY ts DESC\n                    LIMIT ?\n                    "
                result = conn.execute(sql, [target_id, limit])
                result = list(self.arrow_fetch_batch(conn, sql, [target_id, limit]))
            else:
                sql = "\n                    SELECT id, target_id, pivot_type, ioc_type,\n                           produced_count, accepted_count, signal_value, ts\n                    FROM hypothesis_feedback\n                    ORDER BY ts DESC\n                    LIMIT ?\n                    "
                result = conn.execute(sql, [limit])
                result = list(self.arrow_fetch_batch(conn, sql, [limit]))
            return [
                {
                    "id": r[0],
                    "target_id": r[1],
                    "pivot_type": r[2],
                    "ioc_type": r[3],
                    "produced_count": r[4],
                    "accepted_count": r[5],
                    "signal_value": r[6],
                    "ts": r[7],
                }
                for r in result
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []

    def _sync_record_research_session(
        self,
        session_id: str,
        sprint_id: str,
        query: str,
        ts: float,
        findings_count: int,
        accepted_count: int,
        gaps_json: str,
        entities_json: str,
        source_patterns_json: str,
        unexplored_angles_json: str,
        temporal_anomalies_json: str,
    ) -> bool:
        """Sprint F350M: Insert a research_sessions record."""
        try:
            conn = self._qe()._conn()
            conn.execute(
                "\n                INSERT INTO research_sessions\n                (session_id, sprint_id, query, ts, findings_count, accepted_count,\n                 gaps_json, entities_json, source_patterns_json, unexplored_angles_json, temporal_anomalies_json)\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                ",
                [
                    session_id,
                    sprint_id,
                    query,
                    ts,
                    findings_count,
                    accepted_count,
                    gaps_json,
                    entities_json,
                    source_patterns_json,
                    unexplored_angles_json,
                    temporal_anomalies_json,
                ],
            )
            conn.commit()
            return True
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            logger.warning(f"[F350M] _sync_record_research_session failed: {e}")
            return False

    def _sync_record_entity_observations_bulk(self, observations: list[dict[str, Any]]) -> int:
        """Sprint F350M: Bulk insert entity_observations."""
        if not observations:
            return 0
        try:
            conn = self._qe()._conn()
            rows = [
                (
                    o["observation_id"],
                    o["entity_value"],
                    o["entity_type"],
                    o["sprint_id"],
                    o["source_type"],
                    o["confidence"],
                    o["ts"],
                    o["finding_id"],
                )
                for o in observations
            ]
            conn.executemany(
                "\n                INSERT OR REPLACE INTO entity_observations\n                (observation_id, entity_value, entity_type, sprint_id, source_type, confidence, ts, finding_id)\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?)\n                ",
                rows,
            )
            conn.commit()
            return len(rows)
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            logger.warning(f"[F350M] _sync_record_entity_observations_bulk failed: {e}")
            return 0

    def _sync_get_research_sessions_by_sprint(self, sprint_id: str) -> list[dict[str, Any]]:
        """Sprint F350M: Fetch research_sessions by sprint_id."""
        try:
            conn = self._qe()._conn()
            sql = "SELECT session_id, sprint_id, query, ts, findings_count, accepted_count, gaps_json, entities_json, source_patterns_json, unexplored_angles_json, temporal_anomalies_json FROM research_sessions WHERE sprint_id = ? ORDER BY ts DESC"
            result = list(self._store.arrow_fetch_batch(conn, sql, [sprint_id]))
            return [
                {
                    "session_id": r[0],
                    "sprint_id": r[1],
                    "query": r[2],
                    "ts": r[3],
                    "findings_count": r[4],
                    "accepted_count": r[5],
                    "gaps_json": r[6],
                    "entities_json": r[7],
                    "source_patterns_json": r[8],
                    "unexplored_angles_json": r[9],
                    "temporal_anomalies_json": r[10],
                }
                for r in result
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []

    def _sync_get_entity_observations_by_entity(self, entity_value: str, limit: int = 50) -> list[dict[str, Any]]:
        """Sprint F350M: Fetch entity_observations by entity_value."""
        try:
            conn = self._qe()._conn()
            sql = "SELECT observation_id, entity_value, entity_type, sprint_id, source_type, confidence, ts, finding_id FROM entity_observations WHERE entity_value = ? ORDER BY ts DESC LIMIT ?"
            result = list(self._store.arrow_fetch_batch(conn, sql, [entity_value, limit]))
            return [
                {
                    "observation_id": r[0],
                    "entity_value": r[1],
                    "entity_type": r[2],
                    "sprint_id": r[3],
                    "source_type": r[4],
                    "confidence": r[5],
                    "ts": r[6],
                    "finding_id": r[7],
                }
                for r in result
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []

    def _sync_get_recent_research_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        """Sprint F350M: Fetch recent research_sessions."""
        try:
            conn = self._qe()._conn()
            sql = "SELECT session_id, sprint_id, query, ts, findings_count, accepted_count, gaps_json, entities_json, source_patterns_json, unexplored_angles_json, temporal_anomalies_json FROM research_sessions ORDER BY ts DESC LIMIT ?"
            result = list(self._store.arrow_fetch_batch(conn, sql, [limit]))
            return [
                {
                    "session_id": r[0],
                    "sprint_id": r[1],
                    "query": r[2],
                    "ts": r[3],
                    "findings_count": r[4],
                    "accepted_count": r[5],
                    "gaps_json": r[6],
                    "entities_json": r[7],
                    "source_patterns_json": r[8],
                    "unexplored_angles_json": r[9],
                    "temporal_anomalies_json": r[10],
                }
                for r in result
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []

    def _sync_get_previous_findings_for_target(
        self, target_id: str, before_sprint_id: str | None, limit: int
    ) -> list[dict]:
        """
        Sync query - MUST be called on the worker thread.
        Returns raw dict rows from canonical_findings filtered by target_id metadata.
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            if before_sprint_id:
                sql = "\n                    SELECT finding_id, query, source_type, confidence, ts, provenance_json, payload_text\n                    FROM canonical_findings\n                    WHERE payload_text LIKE ?\n                    AND sprint_id < ?\n                    ORDER BY ts DESC\n                    LIMIT ?\n                    "
                result = conn.execute(sql, [f'%"{target_id}"%', before_sprint_id, limit])
                result = list(self.arrow_fetch_batch(conn, sql, [f'%"{target_id}"%', before_sprint_id, limit]))
            else:
                sql = "\n                    SELECT finding_id, query, source_type, confidence, ts, provenance_json, payload_text\n                    FROM canonical_findings\n                    WHERE payload_text LIKE ?\n                    ORDER BY ts DESC\n                    LIMIT ?\n                    "
                result = conn.execute(sql, [f'%"{target_id}"%', limit])
                result = list(self.arrow_fetch_batch(conn, sql, [f'%"{target_id}"%', limit]))
            return [
                {
                    "finding_id": row[0],
                    "query": row[1],
                    "source_type": row[2],
                    "confidence": row[3],
                    "ts": row[4],
                    "provenance_json": row[5],
                    "payload_text": row[6],
                }
                for row in result
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            try:
                conn = self._file_conn if self._db_path else self._persistent_conn
                if conn is None:
                    return []
                return []
            except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                return []

    def _sync_insert_sprint_delta(self, row: dict) -> bool:
        """
        Sync insert - MUST be called on the worker thread.

        Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).
        """
        try:
            if self._db_path:
                self._prewarm_file_conn()
                self._file_conn.execute(
                    "\n                    INSERT INTO sprint_delta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)\n                    ",
                    [
                        row["sprint_id"],
                        row["ts"],
                        row.get("query"),
                        row.get("duration_s", 0),
                        row.get("new_findings", 0),
                        row.get("dedup_hits", 0),
                        row.get("ioc_nodes", 0),
                        row.get("ioc_new_this_sprint", 0),
                        row.get("uma_peak_gib", 0),
                        row.get("synthesis_success", False),
                        row.get("findings_per_minute", 0),
                        row.get("top_source_type"),
                        row.get("synthesis_confidence", 0),
                        row.get("findings_per_minute", 0),
                    ],
                )
            else:
                self._persistent_conn.execute(
                    "\n                    INSERT INTO sprint_delta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)\n                    ",
                    [
                        row["sprint_id"],
                        row["ts"],
                        row.get("query"),
                        row.get("duration_s", 0),
                        row.get("new_findings", 0),
                        row.get("dedup_hits", 0),
                        row.get("ioc_nodes", 0),
                        row.get("ioc_new_this_sprint", 0),
                        row.get("uma_peak_gib", 0),
                        row.get("synthesis_success", False),
                        row.get("findings_per_minute", 0),
                        row.get("top_source_type"),
                        row.get("synthesis_confidence", 0),
                        row.get("findings_per_minute", 0),
                    ],
                )
            return True
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return False

    def _sync_insert_source_hit(
        self, sprint_id: str, ts: float, source_type: str, findings_count: int, ioc_count: int, hit_rate: float
    ) -> bool:
        """Sync insert source hit - MUST be called on the worker thread."""
        try:
            if self._db_path:
                self._prewarm_file_conn()
                self._file_conn.execute(
                    "INSERT INTO source_hit_log VALUES (?,?,?,?,?,?)",
                    [sprint_id, ts, source_type, findings_count, ioc_count, hit_rate],
                )
            else:
                self._persistent_conn.execute(
                    "INSERT INTO source_hit_log VALUES (?,?,?,?,?,?)",
                    [sprint_id, ts, source_type, findings_count, ioc_count, hit_rate],
                )
            return True
        except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
            return False

    def _sync_query_sprint_trend(self, last_n: int) -> list[dict]:
        """Sync query - MUST be called on the worker thread. Uses persistent _file_conn."""
        try:
            if self._db_path:
                self._prewarm_file_conn()
                sql = "\n                    SELECT sprint_id, ts, new_findings, ioc_nodes,\n                           findings_per_minute, synthesis_success, uma_peak_gib\n                    FROM sprint_delta\n                    ORDER BY ts DESC\n                    LIMIT ?\n                    "
                result = self._file_conn.execute(sql, [last_n])
                result = list(self.arrow_fetch_batch(self._file_conn, sql, [last_n]))
            else:
                sql = "\n                    SELECT sprint_id, ts, new_findings, ioc_nodes,\n                           findings_per_minute, synthesis_success, uma_peak_gib\n                    FROM sprint_delta\n                    ORDER BY ts DESC\n                    LIMIT ?\n                    "
                result = self._persistent_conn.execute(sql, [last_n])
                result = list(self.arrow_fetch_batch(self._persistent_conn, sql, [last_n]))
            return [
                {
                    "sprint_id": r[0],
                    "ts": r[1],
                    "new_findings": r[2],
                    "ioc_nodes": r[3],
                    "findings_per_minute": r[4],
                    "synthesis_success": bool(r[5]) if r[5] is not None else False,
                    "uma_peak_gib": r[6] or 0.0,
                }
                for r in result
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []

    def _sync_query_source_leaderboard(self, since_ts: float) -> list[dict]:
        """Sync query - MUST be called on the worker thread. Uses persistent _file_conn."""
        try:
            if self._db_path:
                self._prewarm_file_conn()
                sql = "\n                    SELECT source_type,\n                           SUM(findings_count) as total_findings,\n                           AVG(hit_rate) as avg_hit_rate,\n                           COUNT(*) as sprint_appearances\n                    FROM source_hit_log\n                    WHERE ts > ?\n                    GROUP BY source_type\n                    LIMIT 10000\n                    ORDER BY total_findings DESC\n                    "
                result = self._file_conn.execute(sql, [since_ts])
                result = list(self.arrow_fetch_batch(self._file_conn, sql, [since_ts]))
            else:
                sql = "\n                    SELECT source_type,\n                           SUM(findings_count) as total_findings,\n                           AVG(hit_rate) as avg_hit_rate,\n                           COUNT(*) as sprint_appearances\n                    FROM source_hit_log\n                    WHERE ts > ?\n                    GROUP BY source_type\n                    LIMIT 10000\n                    ORDER BY total_findings DESC\n                    "
                result = self._persistent_conn.execute(sql, [since_ts])
                result = list(self.arrow_fetch_batch(self._persistent_conn, sql, [since_ts]))
            return [
                {
                    "source_type": r[0],
                    "total_findings": r[1] or 0,
                    "avg_hit_rate": r[2] or 0.0,
                    "sprint_appearances": r[3] or 0,
                }
                for r in result
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []

    def _sync_query_sprint_source_stats(self) -> list[dict]:
        """
        Sprint 8RC: Query source_type hit-rate stats for weight loading.
        Returns avg_hit_rate per source_type over the last 5 days.
        MUST be called on the worker thread.
        """
        cutoff = _time.time() - 5 * 86400
        try:
            if self._db_path:
                self._prewarm_file_conn()
                sql = "\n                    SELECT source_type, AVG(hit_rate) as avg_hit_rate\n                    FROM source_hit_log\n                    WHERE ts > ?\n                    GROUP BY source_type\n                    LIMIT 10000\n                    "
                result = self._file_conn.execute(sql, [cutoff])
                result = list(self.arrow_fetch_batch(self._file_conn, sql, [cutoff]))
            else:
                sql = "\n                    SELECT source_type, AVG(hit_rate) as avg_hit_rate\n                    FROM source_hit_log\n                    WHERE ts > ?\n                    GROUP BY source_type\n                    LIMIT 10000\n                    "
                result = self._persistent_conn.execute(sql, [cutoff])
                result = list(self.arrow_fetch_batch(self._persistent_conn, sql, [cutoff]))
            return [{"source_type": r[0], "avg_hit_rate": r[1] or 0.0} for r in result]
        except Exception:  # noqa: BLE001 — best-effort; Arrow/Parquet operation; non-critical
            return []

    def _prewarm_file_conn(self) -> bool:
        """
        Sprint 7H: Amortize cold connect by issuing a no-op query.
        Called on first write to warm up _file_conn.
        Returns True if prewarm succeeded.
        """
        if self._file_conn is None:
            return False
        try:
            self._file_conn.execute("SELECT 1").fetchall()
            return True
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return False

    def _sync_close_on_worker(self) -> None:
        """Close all connections - MUST be called on the worker thread."""
        try:
            if hasattr(self, "_query_executor"):
                qe = self._qe()
                if qe is not None:
                    qe._invalidate_insert_stmt()
        except Exception:  # noqa: BLE001 — best-effort; DB write failure; non-critical
            pass
        if self._persistent_conn is not None:
            try:
                self._persistent_conn.close()
            except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                pass
            self._persistent_conn = None
        if self._file_conn is not None:
            try:
                self._file_conn.close()
            except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                pass
            self._file_conn = None
        if self._wal_manager is not None:
            try:
                self._wal_manager.close()
            except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                pass
            self._wal_manager = None

    def _resolve_path(self) -> None:
        """
        Resolve _db_path and _temp_dir based on RAMDISK availability.

        RAMDISK_ACTIVE=True:  DUCKDB_STORE_ROOT / "shadow_analytics.duckdb", temp = RAMDISK_ROOT / "duckdb_tmp"
        RAMDISK_ACTIVE=False: DUCKDB_STORE_ROOT / "analytics.duckdb",     temp = None (no spill to SSD)

        Sprint F265B: All hot DuckDB data now uses DUCKDB_STORE_ROOT (co-located with LMDB_STORE_ROOT
        for atomic WAL operations). DUCKDB_STORE_ROOT defaults to SPRINT_STORE_ROOT.parent / "duckdb_store"
        which is ~/.hledac/duckdb_store — or RAMDISK-backed when HLEDAC_RAMDISK/HLEDAC_DUCKDB_STORE is set.
        """
        try:
            from hledac.universal.paths import DUCKDB_STORE_ROOT, RAMDISK_ACTIVE, RAMDISK_ROOT

            if RAMDISK_ACTIVE:
                self._db_path = DUCKDB_STORE_ROOT / "shadow_analytics.duckdb"
                self._temp_dir = RAMDISK_ROOT / "duckdb_tmp"
            else:
                self._db_path = DUCKDB_STORE_ROOT / "analytics.duckdb"
                self._temp_dir = None
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            self._db_path = None
            self._temp_dir = None

    def initialize(self) -> bool:
        """
        Initialize DuckDB connection synchronously (backward compat wrapper).

        For async code prefer async_initialize().
        """
        if self._closed:
            return False
        if self._initialized:
            return True
        from hledac.universal.core.mlx_embeddings import MLXEmbeddingManager

        _EMBEDDING_DIM = getattr(MLXEmbeddingManager, "EMBEDDING_DIM", 256)
        assert _EMBEDDING_DIM == 256, (
            f"Embedding dimension mismatch: MLXEmbeddingManager.EMBEDDING_DIM={_EMBEDDING_DIM}, expected 256 (MRL canonical)"
        )
        if self._db_path is None:
            self._resolve_path()
        try:
            fut = self._executor.submit(self._init_connection)
            fut.result()
            self._duckdb_module = _get_duckdb()
            self._initialized = True
            self._startup_ready.set()
            return True
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            self._initialized = False
            return False

    def insert_shadow_finding(self, finding_id: str, query: str, source_type: str, confidence: float) -> bool:
        """Sync insert - backward compat. For async use async_record_shadow_finding()."""
        if not self._initialized or self._closed:
            return False
        try:
            fut = self._executor.submit(self._sync_insert_finding, finding_id, query, source_type, confidence)
            return fut.result()
        except Exception:  # noqa: BLE001 — best-effort; DB write failure; non-critical
            return False

    def insert_shadow_run(
        self, run_id: str, started_at: float, ended_at: float | None, total_fds: int, rss_mb: int
    ) -> bool:
        """Sync insert - backward compat. For async use async_record_shadow_run()."""
        if not self._initialized or self._closed:
            return False
        try:
            fut = self._executor.submit(self._sync_insert_run, run_id, started_at, ended_at, total_fds, rss_mb)
            return fut.result()
        except Exception:  # noqa: BLE001 — best-effort; DB write failure; non-critical
            return False

    def query_recent_findings(self, limit: int = 10) -> list[dict[str, Any]]:
        """Sync query - backward compat. For async use async_query_recent_findings()."""
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_findings, limit)
            return fut.result()
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return []

    def close(self) -> None:
        """
        Synchronous close — full cleanup without any event loop manipulation.

        F300S-FIX: close() now performs the FULL cleanup inline synchronously.
        No run_until_complete() on a running loop — which fails on Python 3.10+
        with RuntimeError: "cannot close event loop while running".
        close() IS the synchronous cleanup path — no event loop manipulation needed.

        Idempotent: safe to call multiple times.
        """
        _ct = getattr(self, "_checkpoint_task", None)
        if _ct is not None:
            _ct.cancel()
            self._checkpoint_task = None
        self._do_sync_close(emergency=True)

    async def _do_async_close(self) -> None:
        """
        Async graph/semantic store close — properly awaits coroutines.

        Called only from aclose() path where an event loop is guaranteed to exist.
        Extracts and awaits all async close() calls that _do_sync_close skips
        when emergency=True.
        """
        gs = self._graph_attachment if self._graph_attachment is not None else None
        if gs is not None:
            # F360: DuckDBGraphAttachment wraps GraphAttachmentStore._store
            store = getattr(gs, "_store", None) or gs
            truth_graph = getattr(store, "_truth_write_graph", None)
            if truth_graph is not None:
                try:
                    if callable(getattr(truth_graph, "close", None)):
                        result = truth_graph.close()
                        if asyncio.iscoroutine(result):
                            await result
                except Exception:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                    pass
            ioc_graph = getattr(store, "_ioc_graph", None)
            if ioc_graph is not None:
                try:
                    if callable(getattr(ioc_graph, "close", None)):
                        result = ioc_graph.close()
                        if asyncio.iscoroutine(result):
                            await result
                except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
                    pass
            stix_graph = getattr(store, "_stix_graph", None)
            if stix_graph is not None:
                try:
                    if callable(getattr(stix_graph, "close", None)):
                        result = stix_graph.close()
                        if asyncio.iscoroutine(result):
                            await result
                except Exception:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                    pass
        if self._semantic_store is not None:
            try:
                result = self._semantic_store.close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                pass
        if self._wal_manager is not None:
            try:
                await self._wal_manager.aclose()
            except Exception:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                pass
            self._wal_manager = None

    # ------------------------------------------------------------------ //
    # Cleanup helpers — isolate try/except per cleanup concern
    # ------------------------------------------------------------------ //

    def _safe_cleanup(self, operation: str, fn: callable, *args, **kwargs) -> None:
        """Fail-safe cleanup wrapper — swallows all exceptions non-critically.

        Centralizes the 15+ duplicate BLE001 except blocks that existed in
        _do_sync_close. Each cleanup operation gets its own helper below.
        """
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
            pass

    def _cleanup_finalizer(self) -> None:
        if self._finalizer is not None:
            self._safe_cleanup("finalizer.detach", self._finalizer.detach)
            self._finalizer = None

    def _cleanup_startup_flags(self) -> None:
        self._safe_cleanup("startup_flags.clear", self._startup_ready.clear)
        self._startup_replay_done = False

    def _cleanup_executor_submit(self) -> None:
        self._safe_cleanup(
            "executor.submit(sync_close_on_worker)",
            lambda: self._executor.submit(self._sync_close_on_worker).result(timeout=5),
        )

    def _cleanup_truth_graph(self) -> None:
        gs = getattr(self, "_graph_attachment", None)
        if gs is None:
            return
        truth_graph = getattr(gs, "_truth_write_graph", None)
        if truth_graph is not None:
            self._safe_cleanup(
                "truth_graph.flush_buffers",
                lambda: callable(getattr(truth_graph, "flush_buffers", None)) and truth_graph.flush_buffers(),
            )

    def _cleanup_semantic_store(self) -> None:
        if self._semantic_store is not None:
            self._safe_cleanup("semantic_store.close", self._semantic_store.close)
            self._semantic_store = None

    def _cleanup_wal_lmdb(self) -> None:
        _wal = getattr(self, "_wal_lmdb", None)
        if _wal is not None:
            self._safe_cleanup("wal_lmdb.close", _wal.close)
            self._wal_lmdb = None

    def _cleanup_read_pool(self) -> None:
        if self._read_pool:
            for conn in self._read_pool:
                self._safe_cleanup("read_pool.conn.close", conn.close)
            self._read_pool = []
            self._read_pool_idx = 0

    def _cleanup_dedup_manager(self) -> None:
        if self._dedup_manager is not None:
            self._safe_cleanup("dedup_manager.close", self._dedup_manager.close)
            self._dedup_manager = None

    def _cleanup_dedup_lmdb(self) -> None:
        _dedup = getattr(self, "_dedup_lmdb", None)
        if _dedup is not None:
            self._safe_cleanup("dedup_lmdb.close", _dedup.close)
            self._dedup_lmdb = None

    def _cleanup_stale_lock(self) -> None:
        def _try_remove_stale_lock() -> None:
            from hledac.universal.graph.lock_manager import _is_lock_stale

            _lock_db_path = str(self._db_path) if self._db_path else "memory"
            duckdb_lock_path = pathlib.Path(_lock_db_path + ".lock")
            if duckdb_lock_path.exists():
                is_stale, reason = _is_lock_stale(duckdb_lock_path, _lock_db_path)
                if is_stale:
                    duckdb_lock_path.unlink(missing_ok=True)
                    _logger.debug(f"[DUCKDB] Removed stale lock {duckdb_lock_path}: {reason}")

        self._safe_cleanup("stale_lock.remove", _try_remove_stale_lock)

    def _cleanup_pending_findings(self) -> None:
        # ISSUE-021: flush pending accepted findings before close.
        # Both lists are an invariant pair — clearing only findings and not indices
        # would leave stale indices that grow unbounded across sprints.
        _pending_findings = getattr(self, "_pending_accepted_findings", None)
        _pending_indices = getattr(self, "_pending_accepted_indices", None)
        if _pending_findings:
            self._safe_cleanup(
                "pending_findings.flush",
                lambda: (
                    _copy_findings := list(_pending_findings),
                    _pending_findings.clear(),
                    _copy_findings and self._executor.submit(self._flush_pending_findings_sync, _copy_findings),
                ),
            )
        if _pending_indices:
            self._safe_cleanup("pending_indices.clear", _pending_indices.clear)

    def _cleanup_arrow_metrics(self) -> None:
        _metrics = getattr(self, "_arrow_metrics", None)
        if _metrics is not None:
            self._safe_cleanup("arrow_metrics.clear", _metrics.clear)

    def _do_sync_close(self, emergency: bool = False) -> None:
        """
        Synchronous full cleanup — called by both close() and aclose().

        Args:
            emergency: If True (close() path), skips async graph/semantic closes
                       since no event loop is guaranteed to be running.
                       Async cleanup is handled by _do_async_close() in aclose() path.
        """
        if self._closed:
            return

        self._cleanup_finalizer()
        self._closed = True
        self._initialized = False
        self._cleanup_startup_flags()
        self._cleanup_executor_submit()
        self._cleanup_truth_graph()
        self._cleanup_semantic_store()
        self._cleanup_wal_lmdb()
        self._cleanup_read_pool()
        self._cleanup_dedup_manager()
        self._cleanup_dedup_lmdb()
        self._cleanup_stale_lock()
        self._cleanup_pending_findings()
        self._cleanup_arrow_metrics()
        self._adjust_executor_pool()

    def _flush_pending_findings_sync(self, findings: list) -> None:
        """Sync flush: persists pending findings from _pending_accepted_findings on close.

        Called from _do_sync_close via executor.submit to avoid blocking.
        Writes via Arrow batch pipeline (same as async_ingest_findings_batch).
        """
        try:
            if not findings:
                return
            if hasattr(self, "_record_canonical_findings_batch_arrow"):
                self._record_canonical_findings_batch_arrow(findings)
            elif hasattr(self, "_sync_record_canonical_findings_batch_arrow_standalone"):
                self._sync_record_canonical_findings_batch_arrow_standalone(findings)
        except Exception:  # noqa: BLE001 — best-effort; Arrow/Parquet operation; non-critical
            pass  # fail-soft: don't fail close for flush errors

    async def async_initialize(self, replay_pending_limit: int | None = None, replay_timeout_s: float = 5.0) -> bool:
        """
        Async initialize - creates connection on the worker thread.

        Optional bounded startup replay runs after connection init, before the store
        accepts new activation writes. This integrates the Sprint 8H recovery API
        into the real init/startup path.

        Args:
            replay_pending_limit: Max number of pending markers to replay at startup.
                                 None or 0 = no startup replay.
            replay_timeout_s:    Wall-time budget for startup replay in seconds.
                                 If exceeded, replay is stopped and remaining
                                 markers are left for a future recovery run.

        Returns:
            True if initialization succeeded, False otherwise.
            Sidecar is safe to use even if this returns False.

        Boot barrier semantics (Sprint 8L):
            While startup replay is running, _startup_ready is NOT set.
            All async activation write methods check this and refuse to proceed
            until the barrier is lifted (or the store is closed).
            After bounded replay completes (success, limit, or timeout),
            _startup_ready is set and writes are accepted.

        NOTE: after aclose(), _closed is True and _initialized is False.
        We allow re-initialization by clearing _closed here.
        """
        if self._closed:
            self._closed = False
        if self._initialized:
            return True
        try:
            self._cleanup_orphaned_locks()
        except Exception:  # noqa: BLE001 — best-effort; lock failure; non-critical
            pass
        if self._lazy:
            if self._db_path is None:
                self._resolve_path()
            self._initialized = True
            self._startup_ready.set()
            return True
        if self._db_path is None:
            self._resolve_path()
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._init_connection)
            self._duckdb_module = _get_duckdb()
            self._initialized = True
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            self._initialized = False
            return False
        if self._wal_manager is None:
            _wal_root = self._db_path.parent if self._db_path else None
            if _wal_root is not None:
                # ISSUE-6.1: F272 consolidation — use UnifiedLMDBStore for WAL namespace
                # This merges WAL LMDB into the shared 256MB mmap instead of separate file
                from .lmdb_subdb import UnifiedLMDBStore

                _unified_lmdb = UnifiedLMDBStore(
                    str(_wal_root / "sprint_unified.lmdb"),
                    lazy=True,
                )
                self._wal_manager = DuckDBWALManager(
                    wal_root=_wal_root,
                    unified_store=_unified_lmdb,  # F272: shared mmap for WAL namespace
                )
                self._wal_manager.initialize()
        if self._dedup_manager is None:
            self._dedup_manager = DedupManager(unified_store=_unified_lmdb)  # F272: shared mmap for dedup namespace
        if replay_pending_limit:
            await self._bounded_startup_replay(
                replay_pending_limit=replay_pending_limit, replay_timeout_s=replay_timeout_s
            )
            self._startup_replay_done = True
        self.ensure_target_profiles_schema()
        if self._db_path is not None:
            self._checkpoint_task = safe_create_task(self._maintenance_loop())
        self._startup_ready.set()
        try:
            if hasattr(self, "_arrow_metrics") and self._arrow_metrics is not None:
                _finalizer = weakref.finalize(
                    self, lambda _metrics: _metrics.clear() if _metrics is not None else None, self._arrow_metrics
                )
                _finalizer.atexit = False
        except (TypeError, AttributeError, Exception):
            pass
        return True

    async def async_initialize_schema(self) -> bool:
        """
        F275: Explicit schema initialization - creates/touches the DB file and
        runs CREATE TABLE IF NOT EXISTS for all canonical tables.

        Safe to call multiple times (idempotent). Does NOT run full
        async_initialize() - no WAL replay, no DedupManager init.
        This is the minimal init path for the zero-findings case.

        Returns True if schema is ready, False on error.
        """
        if self._closed:
            return False
        try:
            if self._db_path is None:
                self._resolve_path()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._init_connection)
            self._initialized = True
            self._startup_ready.set()
            return True
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return False

    async def async_record_shadow_run(
        self, run_id: str, started_at: float, ended_at: float | None, total_fds: int, rss_mb: int
    ) -> bool:
        """
        Insert a run record into the shadow analytics store.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        """
        if not self._initialized or self._closed:
            return False
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._executor, self._sync_insert_run, run_id, started_at, ended_at, total_fds, rss_mb
            )
            return True
        except Exception:  # noqa: BLE001 — best-effort; DB write failure; non-critical
            return False

    def ensure_connected(self) -> None:
        """
        Lazy connection init — called on first actual query.

        When lazy=True (default): defers actual DuckDB connection to this method.
        When lazy=False: no-op (already connected via async_initialize).

        This is the on-demand bootstrap that enables ~0s sprint boot with no findings.
        All async write methods call ensure_connected() before their run_in_executor.

        Barrier semantics (Sprint DuckDB Lazy Init F265X):
            In lazy mode, _startup_ready is cleared here BEFORE connecting, then
            set again AFTER connecting. This ensures writes always wait for the
            connection to be ready (no spurious proceeds before connection exists).
        """
        if not self._lazy:
            return
        if self._persistent_conn is not None or self._file_conn is not None:
            return
        if self._db_path is None:
            self._resolve_path()
        self._startup_ready.clear()
        self._init_connection()
        self._duckdb_module = _get_duckdb()
        self._startup_ready.set()

    def _get_read_conn(self) -> Any | None:
        """
        ISSUE-008 P1: Return next read connection from round-robin pool.

        Read pool allows parallel analytical queries without contention
        with the write connection. Falls back to _file_conn if pool is empty.

        Thread-safe: uses atomic idx increment.
        """
        if self._read_pool:
            idx = self._read_pool_idx % len(self._read_pool)
            self._read_pool_idx = idx + 1
            return self._read_pool[idx]
        return self._file_conn if self._db_path else self._persistent_conn

    async def __aenter__(self) -> DuckDBShadowStore:
        """
        Async context manager entry - initializes the store.

        Usage:
            async with DuckDBShadowStore() as store:
                await store.async_insert_finding(...)
            # aclose() called automatically on exit

        Sprint DuckDB Lazy Init (F265X): when lazy=True (default), this returns
        immediately without connecting. Connection is deferred to the first actual
        query via ensure_connected(). This saves ~1-2s from sprint boot.
        """
        if self._lazy:
            if self._db_path is None:
                self._resolve_path()
            self._initialized = True
            self._startup_ready.set()
            return self
        await self.async_initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Async context manager exit - cleans up the store.
        Idempotent: safe to call even if already closed.
        """
        await self.aclose()

    async def async_record_shadow_finding(
        self, finding_id: str, query: str, source_type: str, confidence: float
    ) -> bool:
        """
        Insert a single finding into the shadow analytics store.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        """
        if not self._initialized or self._closed:
            return False
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._executor, self._sync_insert_finding, finding_id, query, source_type, confidence
            )
            return True
        except Exception:  # noqa: BLE001 — best-effort; DB write failure; non-critical
            return False

    async def async_record_shadow_findings_batch(
        self, findings: list[dict[str, Any]], max_batch_size: int = 500
    ) -> int:
        """
        Sprint 7H: True bulk insert for shadow findings using executemany in explicit transaction.
        Each chunk is at most max_batch_size records.
        Returns the number of successfully inserted records.

        Thread-safe, non-blocking — runs on duckdb_worker via run_in_executor.

        Used by analytics_hook.shadow_record_finding() for batched shadow analytics writes.
        """
        if not self._initialized or self._closed:
            return 0
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        total_inserted = 0
        for i in range(0, len(findings), max_batch_size):
            chunk = findings[i : i + max_batch_size]
            try:
                count = await loop.run_in_executor(self._executor, self._sync_insert_findings_bulk, chunk)
                total_inserted += count
            except Exception:  # noqa: BLE001 — best-effort; DB write failure; non-critical
                break
        return total_inserted

    async def async_query_recent_findings(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        Query recent findings ordered by timestamp descending.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        """
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await safe_wait_for(
                loop.run_in_executor(self._executor, self._sync_query_findings, limit),
                timeout=10.0,
                label="query_findings",
            )
        except asyncio.TimeoutError:
            raise
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return []
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return []

    def _sync_execute_raw_sql(self, sql: str) -> list[Any]:
        """
        Execute raw SQL and return all rows.

        MUST be called on duckdb worker thread (inside run_in_executor).
        Thread-safe: uses _file_conn/_persistent_conn.
        """
        conn = self._file_conn if self._db_path else self._persistent_conn
        if conn is None:
            return []
        try:
            return list(conn.execute(sql).fetchall())
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return []

    async def async_execute_raw_sql(self, sql: str) -> list[Any]:
        """
        Execute raw SQL query asynchronously (non-blocking).

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Use this instead of direct _conn.cursor().execute() in async contexts.

        Args:
            sql: Raw SQL query string

        Returns:
            List of row tuples from fetchall()
        """
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._sync_execute_raw_sql, sql)

    async def aiter_recent_findings(
        self, batch_size: int = 500, sprint_id_filter: str | None = None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """
        STORAGE-FIX-3 / Issue #15: Back-pressure streaming iterator for recent findings.

        M1 EIGHTGB memory benefit: yields Arrow batches via async_query_arrow_batches
        instead of loading all rows into a list. For N=10K rows: -300-400 MB peak
        RAM vs async_query_recent_findings() (which uses .fetchall()).

        Default order: ts DESC. WHERE clause optionally scoped to a sprint query.

        BACK-PRESSURE PATTERN (Issue #15): Each yield is one bounded batch (list[dict]).
        Caller processes a full batch before the next I/O is issued — constant memory
        regardless of result set size. Use for large exports, graph ingestion, etc.

        Args:
            batch_size: rows per batch (default 500; DuckDB Arrow batches 2048 internally).
            sprint_id_filter: optional LIKE pattern on query column.

        Yields:
            list[dict] — each batch of rows, ts DESC. Empty list = end of stream.
        """
        if not self._initialized or self._closed:
            return
        if sprint_id_filter is None:
            sql = "SELECT id, query, source_type, confidence, ts, provenance_json, payload_text, claims_json FROM canonical_findings ORDER BY ts DESC"
            params: list[Any] | None = None
        else:
            sql = "SELECT id, query, source_type, confidence, ts, provenance_json, payload_text, claims_json FROM canonical_findings WHERE query LIKE ? ORDER BY ts DESC"
            params = [f"%{sprint_id_filter}%"]
        try:
            async for batch in self.async_query_arrow_batches(sql, params, batch_size=batch_size):
                try:
                    import polars as _pl

                    pdf = _pl.from_arrow(batch)
                    rows_list: list[dict[str, Any]] = list(pdf.iter_rows(named=True))
                except ImportError:
                    cols = batch.columns
                    names = batch.schema.names
                    rows_list = [
                        dict(
                            zip(
                                names,
                                (
                                    cols[j][i].as_py() if hasattr(cols[j][i], "as_py") else cols[j][i]
                                    for j in range(len(cols))
                                ),
                                strict=False,
                            )
                        )
                        for i in range(batch.num_rows)
                    ]
                if rows_list:
                    yield rows_list
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return

    async def async_query_arrow_batches(
        self, sql: str, params: list[Any] | None = None, batch_size: int = 2048
    ) -> AsyncIterator[Any]:
        """
        F231 C / Thread2b: Streaming Arrow batch query - yields batches without loading full result.

        Uses DuckDB's `fetch_record_batch()` when available (DuckDB 1.2+ with Arrow
        extension), falls back to `to_arrow_reader()`, and finally to a warn-telemetry
        chunked fetch if neither is available.

        Thread2b: batch_size increased from 500 → 2048 for better Arrow throughput
        (~16 MB peak per batch on payload_text-heavy queries, M1 8GB safe).

        IMPORTANT: The DuckDB connection and all iteration stays on the worker thread.
        The async generator bridge ensures no live reader crosses into the event loop.
        Caller must consume the generator fully or cancel to avoid resource leaks.

        Args:
            sql: SQL query to execute.
            params: Optional query parameters (parameterized for SQL injection safety).
            batch_size: Rows per batch (default 500, aligned with batch chunking invariant).

        Yields:
            pyarrow.RecordBatch (or dict rows in fallback mode) - each batch is bounded.
        """
        if not self._initialized or self._closed:
            return
        self.ensure_connected()

        def _sync_fetch_batches() -> Iterator[Any]:
            if not _check_pyarrow_available():
                return
            if self._db_path:
                conn = self._file_conn
            else:
                conn = self._persistent_conn
            if conn is None:
                return
            try:
                result = conn.execute(sql, params or [])
                if hasattr(result, "fetch_record_batch"):
                    reader = result.fetch_record_batch(batch_size)
                    while True:
                        try:
                            batch = reader.read_next_batch()
                            if batch is None:
                                break
                            yield batch
                        except StopIteration:
                            break
                    return
                if hasattr(result, "to_arrow_reader"):
                    reader = result.to_arrow_reader(rows_per_batch=batch_size)
                    while True:
                        try:
                            batch = reader.read_next_batch()
                            if batch is None:
                                break
                            yield batch
                        except StopIteration:
                            break
                    return
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
            os.environ.setdefault("HLEDAC_WARN_ARROW_FALLBACK", "1")
            result = conn.execute(sql, params or [])
            while True:
                rows = result.fetchmany(batch_size)
                if not rows:
                    break
                yield rows

        BATCH_SIZE = 8

        def _sync_iter_next_batch(iterator: Iterator[Any]) -> list[Any]:
            """Pull multiple batches from iterator in single executor call."""
            results = []
            for _ in range(BATCH_SIZE):
                try:
                    batch = next(iterator)
                    results.append(batch)
                except StopIteration:
                    break
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    break
            return results

        def _sync_iter_wrapper() -> Iterator[Any]:
            yield from _sync_fetch_batches()

        loop = asyncio.get_running_loop()
        iterator = await loop.run_in_executor(self._executor, _sync_iter_wrapper)
        while True:
            try:
                batch_results = await loop.run_in_executor(self._executor, _sync_iter_next_batch, iterator)
                if not batch_results:
                    break
                for batch in batch_results:
                    yield batch
            except StopIteration:
                break
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                break

    def arrow_fetch_batch(
        self, conn: Any, sql: str, params: list[Any] | None = None, batch_size: int = 2048
    ) -> Iterator[list[tuple]]:
        """
        Sync streaming fetch - bounded memory alternative to `fetchall()`.

        Yields lists of row tuples, each chunk bounded by `batch_size` (default
        2048, tuned for M1 EIGHTGB UMA - ~16 MB peak per batch on payload_text-heavy
        queries). Replaces `conn.execute(sql, params).fetchall()` patterns so
        the full result set never materializes in RAM at once.

        Two paths, fail-soft throughout:
          1. Arrow zero-copy (DuckDB 1.2+ + pyarrow) - `result.fetch_record_batch(n)`.
          2. fetchmany fallback (universal, no extra deps) - `result.fetchmany(n)`.

        MUST be called on the duckdb worker thread (i.e. from inside `_sync_*`
        methods that run via `self._executor.submit`). Generator stays on the
        worker thread; caller materializes into the final list only after
        full consumption.

        Yields:
            list[tuple] - each chunk bounded; empty generator if conn is None
            or execute fails.
        """
        if conn is None:
            return
        try:
            result = conn.execute(sql, params or [])
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return
        if hasattr(result, "fetch_record_batch"):
            try:
                reader = result.fetch_record_batch(batch_size)
                while True:
                    try:
                        batch = reader.read_next_batch()
                    except StopIteration:
                        break
                    if batch is None:
                        break
                    try:
                        try:
                            import polars as _pl

                            pdf = _pl.from_arrow(batch)
                            yield from pdf.iter_rows(named=False)
                        except ImportError:
                            cols = batch.columns
                            nrows = batch.num_rows
                            ncols = len(cols)
                            yield [
                                tuple(
                                    (
                                        cols[j][i].as_py() if hasattr(cols[j][i], "as_py") else cols[j][i]
                                        for j in range(ncols)
                                    )
                                )
                                for i in range(nrows)
                            ]
                    except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                        cols = batch.columns
                        nrows = batch.num_rows
                        ncols = len(cols)
                        yield [
                            tuple(
                                (
                                    cols[j][i].as_py() if hasattr(cols[j][i], "as_py") else cols[j][i]
                                    for j in range(ncols)
                                )
                            )
                            for i in range(nrows)
                        ]
                return
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        try:
            while True:
                rows = result.fetchmany(batch_size)
                if not rows:
                    break
                yield list(rows)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return

    def duckdb_fetch_polars(self, conn: Any, sql: str, params: list[Any] | None = None) -> pl.DataFrame | None:
        """
        F320-431: Zero-copy DuckDB → Polars via Arrow C Data Interface.

        Uses `conn.execute(sql).pl()` (DuckDB 1.5+) which reads Arrow buffers
        directly via DuckDB's C Data Interface — no Python copies, no IPC
        serialization round-trip. Single GIL acquire/release for the entire
        result set.

        MUST be called on the DuckDB worker thread (thread-affine connection).
        Caller is responsible for thread safety.

        Args:
            conn: DuckDB connection (thread-affine, from _qe()._conn()).
            sql: SQL query.
            params: Optional query parameters.

        Returns:
            pl.DataFrame or None on error. DataFrame column order matches
            SQL projection order.

        Zero-copy guarantees:
          - DuckDB Arrow buffers live in DuckDB's heap
          - Polars adopts buffers via C Data Interface (zero-copy)
          - No IPC bytes serialization (unlike Rust arrow_batch_builder path)
          - Single GIL acquire/release vs N× for row-by-row iteration
        """
        if conn is None:
            return None
        try:
            result = conn.execute(sql, params or [])
            if hasattr(result, "pl"):
                return result.pl()
            if hasattr(result, "to_arrow_reader"):
                reader = result.to_arrow_reader()
                return pl.from_arrow(reader)
            return None
        except Exception:  # noqa: BLE001 — best-effort; Arrow/Parquet operation; non-critical
            return None

    async def wait_until_ready(self, timeout_s: float = 10.0) -> bool:
        """
        Event-driven readiness wait — wakes via asyncio.Event, no polling.

        ISSUE-006 fix: replaces the 40×50ms polling loop (2s worst-case)
        with a single event-driven wait on _startup_ready.

        Returns True if store became ready within timeout, False otherwise.
        """
        if self._startup_ready.is_set():
            return True
        try:
            async with asyncio.timeout(timeout_s):
                await self._startup_ready.wait()
            return True
        except asyncio.TimeoutError:
            return False

    async def async_healthcheck(self) -> bool:
        """
        Quick health check - attempts a zero-cost query.

        Returns True if the store is healthy and responsive.
        """
        if not self._initialized or self._closed:
            return False
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._sync_query_findings, 1)
            return True
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return False

    async def async_record_sprint_delta(self, row: dict) -> bool:
        """
        Insert a sprint_delta record.

        Thread-safe, non-blocking.
        """
        if not self._initialized or self._closed:
            return False
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_insert_sprint_delta, row)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return False

    async def async_record_source_hit(
        self, sprint_id: str, ts: float, source_type: str, findings_count: int, ioc_count: int, hit_rate: float
    ) -> bool:
        """
        Insert a source_hit_log record.

        Thread-safe, non-blocking.
        """
        if not self._initialized or self._closed:
            return False
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_insert_source_hit,
                sprint_id,
                ts,
                source_type,
                findings_count,
                ioc_count,
                hit_rate,
            )
        except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
            return False

    async def async_query_sprint_trend(self, last_n: int = 10) -> list[dict]:
        """
        Return trend data for the last N sprints, ordered by ts DESC.
        Thread-safe, non-blocking.
        """
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_query_sprint_trend, last_n)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return []

    async def async_ingest_cooccurrence_batch(self, pairs: list[dict]) -> bool:
        """
        Batch upsert IOC co-occurrence pairs into DuckDB.

        Replaces raw sqlite3 DELETE+INSERT in IOCooccurrenceMiner.persist().
        Uses DELETE + per-item INSERT (support >= 2 filter applied by caller).

        Args:
            pairs: List of dicts with keys:
                ioc_a, ioc_b, ioc_type_a, ioc_type_b,
                support, confidence, score, last_seen

        Returns:
            True on success, False on failure.
        """
        if not self._initialized or self._closed:
            return False
        if not pairs:
            return True
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_ingest_cooccurrence_batch, pairs)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return False

    def _sync_ingest_cooccurrence_batch(self, pairs: list[dict]) -> bool:
        """Synchronous batch upsert for IOC co-occurrence pairs."""
        conn = self._file_conn if self._db_path else self._persistent_conn
        if conn is None:
            return False
        try:
            conn.execute("DELETE FROM ioc_cooccurrence")
            for p in pairs:
                if p.get("support", 0) >= 2:
                    conn.execute(
                        "INSERT INTO ioc_cooccurrence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            p["ioc_a"],
                            p["ioc_b"],
                            p["ioc_type_a"],
                            p["ioc_type_b"],
                            p["support"],
                            p["confidence"],
                            p["score"],
                            p["last_seen"],
                        ),
                    )
            return True
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return False

    async def async_load_cooccurrence(self, limit: int = 100000) -> list[dict]:
        """
        Load IOC co-occurrence pairs from DuckDB.

        Replaces raw sqlite3 SELECT in IOCooccurrenceMiner._load_sync().

        Args:
            limit: Max pairs to load (default 100_000).

        Returns:
            List of dicts with keys:
                ioc_a, ioc_b, ioc_type_a, ioc_type_b,
                support, confidence, score, last_seen
        """
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_load_cooccurrence, limit)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return []

    def _sync_load_cooccurrence(self, limit: int) -> list[dict]:
        """Synchronous load of IOC co-occurrence pairs from DuckDB."""
        conn = self._file_conn if self._db_path else self._persistent_conn
        if conn is None:
            return []
        try:
            arrow_table = conn.execute(
                "SELECT ioc_a, ioc_b, ioc_type_a, ioc_type_b, support, confidence, score, last_seen FROM ioc_cooccurrence ORDER BY score DESC LIMIT ?",
                (limit,),
            ).fetch_arrow_table()
            if arrow_table is None or arrow_table.num_rows == 0:
                return []
            # M1 8GB: fetch_arrow_table() zero-copy Arrow C Data Interface — no Python intermediary
            return [
                {
                    "ioc_a": row["ioc_a"],
                    "ioc_b": row["ioc_b"],
                    "ioc_type_a": row["ioc_type_a"],
                    "ioc_type_b": row["ioc_type_b"],
                    "support": row["support"],
                    "confidence": row["confidence"],
                    "score": row["score"],
                    "last_seen": row["last_seen"],
                }
                for row in arrow_table.to_pylist()
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []

    async def async_query_recent_findings_by_sprint(self, sprint_id: str, limit: int = 20) -> list[dict]:
        """
        Return the most recent accepted findings for a given sprint,
        ordered by ts DESC. Bounded, read-only, fail-soft.

        Use for: export synthesis input, sprint retrospektivu,
        scheduler priority scoring.
        """
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor, self._sync_query_recent_findings_by_sprint, sprint_id, limit
            )
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return []

    async def async_query_findings_by_text(self, like_pattern: str, limit: int = 1000) -> list[dict[str, Any]]:
        """
        F251A: Read canonical_findings rows matching a text/keyword pattern.
        Used by run_runtime_pivot_prelude() for offline memory seed extraction
        when a text query has no direct IOC seeds.

        Args:
            like_pattern: Keyword to search in query/title/payload_text.
            limit: Max rows to return (default 1000).

        Returns:
            list[dict] with keys: id, query, source_type, title, payload_text, ts.
            Fail-soft: returns [] on any error.
        """
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_query_findings_by_text, like_pattern, limit)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return []

    async def async_query_findings_by_keywords(self, keywords: list[str], limit: int = 1000) -> list[dict[str, Any]]:
        """
        P1-2: Read canonical_findings rows matching ANY of the given keywords.
        Uses OR across keywords so "ransomware breach" matches findings
        containing either "ransomware" OR "breach".
        Used by run_runtime_pivot_prelude() for cross-sprint seed extraction
        when the full query string has no direct match.

        Args:
            keywords: List of keywords to search in query/title/payload_text.
            limit: Max rows to return (default 1000).

        Returns:
            list[dict] with keys: id, query, source_type, title, payload_text, ts.
            Fail-soft: returns [] on any error.
        """
        if not self._initialized or self._closed:
            return []
        if not keywords:
            return []
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_query_findings_by_keywords, keywords, limit)
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return []

    def _sync_query_findings_by_keywords(self, keywords: list[str], limit: int) -> list[dict]:
        """Sync - MUST be called on worker thread."""
        if self._query_cache is not None:
            conditions = " OR ".join(["(query LIKE ? OR title LIKE ? OR payload_text LIKE ?)"] * len(keywords))
            pattern = [f"%{kw}%" for kw in keywords for _ in range(3)]
            _params = tuple(pattern) + (limit,)
            _norm_sql = f"SELECT id, query, source_type, title, payload_text, ts FROM canonical_findings WHERE {conditions} ORDER BY ts DESC LIMIT ?"
            cached = self._query_cache.get(_norm_sql, _params)
            if cached is not None:
                return cached
            result = self._sync_query_findings_by_keywords_impl(keywords, limit)
            if result and self._query_cache is not None:
                self._query_cache.put(_norm_sql, _params, result)
            return result
        return self._sync_query_findings_by_keywords_impl(keywords, limit)

    def _sync_query_findings_by_keywords_impl(self, keywords: list[str], limit: int) -> list[dict]:
        """Sync - MUST be called on worker thread. Internal: actual query without cache."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            conditions = " OR ".join(["(query LIKE ? OR title LIKE ? OR payload_text LIKE ?)"] * len(keywords))
            pattern = [f"%{kw}%" for kw in keywords for _ in range(3)]
            sql = f"\n                SELECT id, query, source_type, title, payload_text, ts\n                FROM canonical_findings\n                WHERE {conditions}\n                ORDER BY ts DESC\n                LIMIT ?\n                "
            rows = list(self.arrow_fetch_batch(conn, sql, pattern + [limit]))
            if not rows:
                return []
            return [
                {
                    "id": r[0],
                    "query": r[1],
                    "source_type": r[2],
                    "title": r[3] or "",
                    "payload_text": r[4] or "",
                    "ts": r[5],
                }
                for r in rows
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []

    def _sync_query_findings_by_text(self, like_pattern: str, limit: int) -> list[dict]:
        """Sync - MUST be called on worker thread."""
        if self._query_cache is not None:
            pattern = f"%{like_pattern}%"
            _norm_sql = "SELECT id, query, source_type, title, payload_text, ts FROM canonical_findings WHERE query LIKE ? OR title LIKE ? OR payload_text LIKE ? ORDER BY ts DESC LIMIT ?"
            cached = self._query_cache.get(_norm_sql, (like_pattern, like_pattern, like_pattern, limit))
            if cached is not None:
                return cached
            result = self._sync_query_findings_by_text_impl(like_pattern, limit)
            if result and self._query_cache is not None:
                self._query_cache.put(_norm_sql, (like_pattern, like_pattern, like_pattern, limit), result)
            return result
        return self._sync_query_findings_by_text_impl(like_pattern, limit)

    def _sync_query_findings_by_text_impl(self, like_pattern: str, limit: int) -> list[dict]:
        """Sync - MUST be called on worker thread. Internal: actual query without cache."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            pattern = f"%{like_pattern}%"
            sql = "\n                SELECT id, query, source_type, title, payload_text, ts\n                FROM canonical_findings\n                WHERE query LIKE ?\n                   OR title LIKE ?\n                   OR payload_text LIKE ?\n                ORDER BY ts DESC\n                LIMIT ?\n                "
            rows = list(self.arrow_fetch_batch(conn, sql, [pattern, pattern, pattern, limit]))
            if not rows:
                return []
            return [
                {
                    "id": r[0],
                    "query": r[1],
                    "source_type": r[2],
                    "title": r[3] or "",
                    "payload_text": r[4] or "",
                    "ts": r[5],
                }
                for r in rows
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []

    def _sync_query_recent_findings_by_sprint(self, sprint_id: str, limit: int) -> list[dict]:
        """Sync - MUST be called on worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            sql = "\n                SELECT id, query, source_type, confidence, ts\n                FROM canonical_findings\n                WHERE query LIKE ('%' || ? || '%')\n                   OR id LIKE ('%' || ? || '%')\n                ORDER BY ts DESC\n                LIMIT ?\n                "
            rows = conn.execute(sql, [sprint_id, sprint_id, limit])
            rows = list(self.arrow_fetch_batch(conn, sql, [sprint_id, sprint_id, limit]))
            if not rows:
                return []
            return [{"id": r[0], "query": r[1], "source_type": r[2], "confidence": r[3], "ts": r[4]} for r in rows]
        except Exception:  # noqa: BLE001 — best-effort; Arrow/Parquet operation; non-critical
            return []

    async def async_query_top_entities_by_sprint(self, sprint_id: str, limit: int = 20) -> list[dict]:
        """
        Return entity-like pivot candidates extracted from finding queries
        and provenance for the given sprint. Looks for domain/IP/url-like
        tokens in query text. Bounded, read-only, fail-soft.

        Use for: synthesis pivot hints, entity correlation candidates,
        export enrichment. Does NOT require global_entities table.
        """
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_query_top_entities_by_sprint, sprint_id, limit)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return []

    def _sync_query_top_entities_by_sprint(self, sprint_id: str, limit: int) -> list[dict]:
        """Sync - MUST be called on worker thread."""
        import re

        DOMAIN_RE = re.compile("(?:https?://)?(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\\.)+[a-zA-Z]{2,}")
        IP_RE = re.compile(
            "\\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\b"
        )
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            sql = "\n                SELECT id, query, source_type, ts\n                FROM canonical_findings\n                WHERE query LIKE ('%' || ? || '%')\n                   OR id LIKE ('%' || ? || '%')\n                ORDER BY ts DESC\n                LIMIT ?\n                "
            rows = conn.execute(sql, [sprint_id, sprint_id, limit * 4])
            rows = list(self.arrow_fetch_batch(conn, sql, [sprint_id, sprint_id, limit * 4]))
            if not rows:
                return []
            candidates: dict[str, dict] = {}
            for row in rows:
                text = f"{row[1]} {row[0]}"
                for m in DOMAIN_RE.finditer(text):
                    domain = m.group().lower()
                    if domain not in candidates:
                        candidates[domain] = {
                            "entity_value": domain,
                            "entity_type": "domain",
                            "occurrences": 0,
                            "last_seen_ts": 0.0,
                        }
                    candidates[domain]["occurrences"] += 1
                    candidates[domain]["last_seen_ts"] = max(candidates[domain]["last_seen_ts"], row[3])
                for m in IP_RE.finditer(text):
                    ip = m.group()
                    if ip not in candidates:
                        candidates[ip] = {
                            "entity_value": ip,
                            "entity_type": "ip",
                            "occurrences": 0,
                            "last_seen_ts": 0.0,
                        }
                    candidates[ip]["occurrences"] += 1
                    candidates[ip]["last_seen_ts"] = max(candidates[ip]["last_seen_ts"], row[3])
            sorted_candidates = sorted(
                candidates.values(), key=lambda x: (x["occurrences"], x["last_seen_ts"]), reverse=True
            )
            return sorted_candidates[:limit]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []

    async def async_query_sprint_ioc_summary(self, sprint_id: str) -> dict:
        """
        Return a lightweight IOC summary for a sprint:
        total findings, unique source_types, avg confidence,
        time span (first->last ts). Bounded, read-only, fail-soft.

        Use for: scheduler decision support, synthesis quality signals,
        sprint retrospektivu.
        """
        if not self._initialized or self._closed:
            return {}
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_query_sprint_ioc_summary, sprint_id)
        except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
            return {}

    def _sync_query_sprint_ioc_summary(self, sprint_id: str) -> dict:
        """Sync - MUST be called on worker thread."""
        if self._query_cache is not None:
            _norm_sql = "SELECT COUNT(*) as total_findings, COUNT(DISTINCT source_type) as unique_sources, AVG(confidence) as avg_confidence, MIN(ts) as first_ts, MAX(ts) as last_ts FROM canonical_findings WHERE query LIKE ('%' || ? || '%') OR id LIKE ('%' || ? || '%')"
            cached = self._query_cache.get(_norm_sql, (sprint_id, sprint_id))
            if cached is not None:
                return cached[0] if cached else {}
            result = self._sync_query_sprint_ioc_summary_impl(sprint_id)
            if result and self._query_cache is not None:
                self._query_cache.put(_norm_sql, (sprint_id, sprint_id), [result])
            return result
        return self._sync_query_sprint_ioc_summary_impl(sprint_id)

    def _sync_query_sprint_ioc_summary_impl(self, sprint_id: str) -> dict:
        """Sync - MUST be called on worker thread. Internal: actual query without cache."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return {}
            row = conn.execute(
                "\n                SELECT\n                    COUNT(*) as total_findings,\n                    COUNT(DISTINCT source_type) as unique_sources,\n                    AVG(confidence) as avg_confidence,\n                    MIN(ts) as first_ts,\n                    MAX(ts) as last_ts\n                FROM canonical_findings\n                WHERE query LIKE ('%' || ? || '%')\n                   OR id LIKE ('%' || ? || '%')\n                ",
                [sprint_id, sprint_id],
            ).fetchone()
            if row is None or row[0] == 0:
                return {}
            return {
                "sprint_id": sprint_id,
                "total_findings": row[0] or 0,
                "unique_sources": row[1] or 0,
                "avg_confidence": round(row[2] or 0.0, 3),
                "first_ts": row[3] or 0.0,
                "last_ts": row[4] or 0.0,
                "span_seconds": (row[4] or 0.0) - (row[3] or 0.0),
            }
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return {}

    async def async_query_top_sources_by_sprint(self, sprint_id: str, limit: int = 10) -> list[dict]:
        """
        Return source_type breakdown (findings count, avg confidence)
        for a given sprint. Bounded, read-only, fail-soft.

        Use for: sprint retrospektivu, source yield analysis,
        scheduler source weighting decisions.
        """
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_query_top_sources_by_sprint, sprint_id, limit)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return []

    def _sync_query_top_sources_by_sprint(self, sprint_id: str, limit: int) -> list[dict]:
        """Sync - MUST be called on worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            sql = "\n                SELECT\n                    source_type,\n                    COUNT(*) as findings_count,\n                    AVG(confidence) as avg_confidence\n                FROM canonical_findings\n                WHERE query LIKE ('%' || ? || '%')\n                   OR id LIKE ('%' || ? || '%')\n                GROUP BY source_type\n                ORDER BY findings_count DESC\n                LIMIT ?\n                "
            rows = conn.execute(sql, [sprint_id, sprint_id, limit])
            rows = list(self.arrow_fetch_batch(conn, sql, [sprint_id, sprint_id, limit]))
            return [
                {"source_type": r[0], "findings_count": r[1] or 0, "avg_confidence": round(r[2] or 0.0, 3)}
                for r in rows
            ]
        except Exception:  # noqa: BLE001 — best-effort; Arrow/Parquet operation; non-critical
            return []

    async def upsert_scorecard(self, data: dict) -> bool:
        """
        Sprint 8TA B.3: Insert or replace a sprint_scorecard record.

        data contains: sprint_id, ts, findings_per_minute, ioc_density,
        semantic_novelty, source_yield_json (orjson), phase_timings_json (orjson),
        outlines_used, accepted_findings, ioc_nodes
        """
        if not self._initialized or self._closed:
            return False
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_upsert_scorecard, data)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return False

    def _sync_upsert_scorecard(self, data: dict) -> bool:
        """Sync upsert scorecard - MUST be called on worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return False
            conn.execute(
                "\n                INSERT OR REPLACE INTO sprint_scorecard VALUES (?,?,?,?,?,?,?,?,?,?)\n                ",
                [
                    data["sprint_id"],
                    data["ts"],
                    data.get("findings_per_minute", 0),
                    data.get("ioc_density", 0),
                    data.get("semantic_novelty", 1.0),
                    data.get("source_yield_json", "{}"),
                    data.get("phase_timings_json", "{}"),
                    data.get("outlines_used", False),
                    data.get("accepted_findings", 0),
                    data.get("ioc_nodes", 0),
                ],
            )
            return True
        except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
            return False

    async def submit_findings(self, findings: list[CanonicalFinding]) -> None:
        """
        Fire-and-forget async write — delegates directly to async_ingest_findings_batch().

        async_ingest_findings_batch() has its own built-in Arrow pipeline batching
        (1024-item chunks, 4-slot pipeline queue, concurrent WAL+DuckDB via asyncio.gather),
        so no separate coalescer layer is needed.

        NOTE: findings list must not be mutated after this call returns.
        Caller is responsible for ensuring this.

        Returns: None (fire-and-forget async write).
        """
        if not findings:
            return
        if self._ingest_breaker_state == CBState.OPEN:
            if _time.monotonic() - self._ingest_breaker_last_failure > self._ingest_breaker_cooldown:
                self._ingest_breaker_state = CBState.HALF_OPEN
            else:
                return
        safe_create_task(self._submit_findings_bg(findings))

    async def _submit_findings_bg(self, findings: list[CanonicalFinding]) -> None:
        """Background task — runs submit_findings() logic without blocking the caller."""
        try:
            await self.async_ingest_findings_batch(findings)
            self._ingest_breaker_failures = 0
            self._ingest_breaker_state = CBState.CLOSED
        except Exception:  # noqa: BLE001 — best-effort; lock failure; non-critical
            self._ingest_breaker_failures += 1
            self._ingest_breaker_last_failure = _time.monotonic()
            if self._ingest_breaker_failures >= self._ingest_breaker_threshold:
                self._ingest_breaker_state = CBState.OPEN

    async def drain_and_get_accepted(self, findings: list[CanonicalFinding]) -> list[Any]:
        """
        Direct ingest — calls async_ingest_findings_batch() and returns results.

        This is the canonical write path for call sites that need the
        accepted/stored counts from async_ingest_findings_batch().

        Args:
            findings: findings to ingest.

        Returns:
            List of FindingQualityDecision/ActivationResult objects,
            one per finding submitted. Empty list on failure.
        """
        if not findings:
            return []
        try:
            return await self.async_ingest_findings_batch(findings)
        except Exception:  # noqa: BLE001 — best-effort; async operation failure; non-critical
            return []

    def _execute_in_thread_sync(self, fn) -> Any:
        """
        Execute synchronous function on the duckdb executor and return its result.

        MUST be called from the main thread. The callable fn runs on the
        single-worker ThreadPoolExecutor and blocks until complete.

        Returns:
            The return value of fn(), or None if the executor raised an exception.

        NOTE: This is a synchronous helper. Async callers MUST await the result:
            result = await loop.run_in_executor(self._executor, self._execute_in_thread_sync, fn)
        For direct async wrappers, prefer loop.run_in_executor() directly.
        """
        try:
            f = self._executor.submit(fn)
            return f.result()
        except Exception:  # noqa: BLE001 — best-effort; async operation failure; non-critical
            return None

    async def upsert_episode(self, data: dict) -> None:
        """Sprint 8UC B.2: Zapsat sprint epizodu pro budoucí recall."""
        import time as _t

        def _sync() -> None:
            conn = self._persistent_conn
            if conn is None:
                return
            conn.execute(
                "INSERT OR REPLACE INTO research_episodes\n                   (episode_id, sprint_id, query, summary, top_findings,\n                    ioc_clusters, source_yield, synthesis_engine, duration_s, ts)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    data.get("sprint_id", ""),
                    data.get("sprint_id", ""),
                    data.get("query", ""),
                    data.get("summary", "")[:300] if data.get("summary") else "",
                    _json_dumps_str(data.get("top_findings", [])),
                    _json_dumps_str(data.get("ioc_clusters", [])),
                    _json_dumps_str(data.get("source_yield", {})),
                    data.get("synthesis_engine", "unknown"),
                    float(data.get("duration_s", 0.0)),
                    float(data.get("ts", _t.time())),
                ],
            )

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, _sync)

    async def recall_episodes(self, query_embedding: list[float] | None, limit: int = 5) -> list[dict]:
        """Sprint 8UC B.2: Načíst posledních `limit` epizod (recency-based)."""

        def _sync():
            conn = self._persistent_conn
            if conn is None:
                return []
            try:
                sql = "SELECT sprint_id, query, summary, top_findings, source_yield, ts\n                       FROM research_episodes\n                       ORDER BY ts DESC\n                       LIMIT ?"
                rows = conn.execute(sql, [limit])
                rows = list(self.arrow_fetch_batch(conn, sql, [limit]))
                if not rows:
                    return []
                cols = ["sprint_id", "query", "summary", "top_findings", "source_yield", "ts"]
                return [dict(zip(cols, r, strict=False)) for r in rows]
            except Exception:  # noqa: BLE001 — best-effort; Arrow/Parquet operation; non-critical
                return []

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, _sync)

    async def upsert_target_memory(self, memory: TargetMemory) -> bool:
        """
        Sprint F204D: Upsert a TargetMemory record into DuckDB.

        Serializes facets as JSON TEXT columns. Uses INSERT OR REPLACE.
        GHOST_INVARIANT: runs on duckdb executor via run_in_executor.
        """
        if not self._initialized or self._closed:
            return False
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_upsert_target_memory, memory)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort; DB write failure; non-critical
            return False

    def _sync_upsert_target_memory(self, memory: TargetMemory) -> bool:
        """Sync upsert target memory - MUST be called on worker thread."""
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return False
            conn.execute(
                "\n                INSERT OR REPLACE INTO target_memory\n                (target_id, first_seen_ts, last_seen_ts, sprint_count,\n                 cumulative_finding_count, entity_facets_json, exposure_facets_json,\n                 pivot_facets_json, confidence_drift_json, updated_by_sprint_id,\n                 updated_ts)\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                ",
                [
                    memory.target_id,
                    memory.first_seen_ts,
                    memory.last_seen_ts,
                    memory.sprint_count,
                    memory.cumulative_finding_count,
                    _json_dumps_str(memory.entity_facets),
                    _json_dumps_str(memory.exposure_facets),
                    _json_dumps_str(memory.pivot_facets),
                    _json_dumps_str(memory.confidence_drift),
                    memory.updated_by_sprint_id,
                    _time.time(),
                ],
            )
            return True
        except Exception as _exc:  # noqa: BLE001 — best-effort; memory operation; non-critical
            _logger.warning(f"_sync_upsert_target_memory failed: {_exc}")
            return False

    async def read_target_memory(self, target_id: str) -> TargetMemory | None:
        """
        Sprint F204D: Read a TargetMemory record by target_id.
        Returns None if not found. Deserializes JSON TEXT columns.
        """
        if not self._initialized or self._closed:
            return None

        def _sync() -> TargetMemory | None:
            try:
                conn = self._file_conn if self._db_path else self._persistent_conn
                if conn is None:
                    return None
                sql = "\n                    SELECT target_id, first_seen_ts, last_seen_ts, sprint_count,\n                           cumulative_finding_count, entity_facets_json,\n                           exposure_facets_json, pivot_facets_json,\n                           confidence_drift_json, updated_by_sprint_id, updated_ts\n                    FROM target_memory\n                    WHERE target_id = ?\n                    "
                rows = conn.execute(sql, [target_id])
                rows = list(self.arrow_fetch_batch(conn, sql, [target_id]))
                if not rows:
                    return None
                r = rows[0]
                return TargetMemory(
                    target_id=r[0],
                    first_seen_ts=r[1],
                    last_seen_ts=r[2],
                    sprint_count=r[3],
                    cumulative_finding_count=r[4],
                    entity_facets=_json_loads_flexible(r[5]),
                    exposure_facets=_json_loads_flexible(r[6]),
                    pivot_facets=_json_loads_flexible(r[7]),
                    confidence_drift=_json_loads_flexible(r[8]),
                    updated_by_sprint_id=r[9],
                )
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                return None

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, _sync)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort; async operation failure; non-critical
            return None

    async def upsert_global_entities(self, entities: list[tuple[str, str, float]]) -> int:
        """
        Sprint F4.1 fix: Upsert entities into ghost_global store using DuckDB.

        Path: ~/.hledac/ghost_global.duckdb (DuckDB with native WAL mode)
        Engine: DuckDB with access_mode='automatic' (native file locking)
        Schema: global_entities(entity_value TEXT PK, entity_type TEXT,
                sprint_count INT, last_seen DOUBLE, confidence_cumulative REAL)
        INSERT OR REPLACE with MAX(confidence) semantics.
        Returns: int (count of upserted entities).
        """
        if not entities:
            return 0
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_upsert_global_entities, entities)
        except Exception:  # noqa: BLE001 — best-effort; DB write failure; non-critical
            return 0

    def _sync_upsert_global_entities(self, entities: list[tuple[str, str, float]]) -> int:
        """Sync upsert global entities - MUST be called on worker thread.

        Uses DuckDB's built-in file locking via access_mode='automatic'.
        DuckDB handles crash-safety internally - no external lock file needed.
        """
        from pathlib import Path

        duckdb = _get_duckdb()
        ghost_home = Path.home() / ".hledac"
        ghost_home.mkdir(parents=True, exist_ok=True)
        db_path = ghost_home / "ghost_global.duckdb"
        conn = duckdb.connect(str(db_path), access_mode="automatic")
        try:
            conn.execute("SET memory_limit = '256MB'")
            conn.execute("PRAGMA threads = 2")
            conn.execute("SET preserve_insertion_order = false")
            conn.execute(
                "\n                CREATE TABLE IF NOT EXISTS global_entities (\n                    entity_value TEXT PRIMARY KEY,\n                    entity_type TEXT,\n                    sprint_count INT DEFAULT 0,\n                    last_seen DOUBLE,\n                    confidence_cumulative REAL DEFAULT 0\n                )\n                "
            )
            now = _time.time()
            conn.execute(
                "\n                INSERT INTO global_entities\n                    (entity_value, entity_type, sprint_count, last_seen, confidence_cumulative)\n                VALUES (?, ?, 1, ?, ?)\n                ON CONFLICT(entity_value) DO UPDATE SET\n                    sprint_count = sprint_count + 1,\n                    confidence_cumulative = MAX(confidence_cumulative, excluded.confidence_cumulative),\n                    last_seen = excluded.last_seen\n                ",
                [(e, t, now, c) for e, t, c in entities],
            )
            conn.commit()
            return len(entities)
        finally:
            conn.close()

    async def async_query_source_leaderboard(self, days: int = 7) -> list[dict]:
        """
        Return top sources by hit rate for the last N days.
        Thread-safe, non-blocking.
        """
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor, self._sync_query_source_leaderboard, _time.time() - days * 86400
            )
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return []

    async def async_query_sprint_source_stats(self) -> list[dict]:
        """
        Return per-source-type avg_hit_rate over the last 5 days.
        Used by SprintScheduler.load_source_weights().
        Thread-safe, non-blocking.
        """
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_query_sprint_source_stats)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return []

    def get_sprint_trend(self, last_n: int = 10) -> list[dict]:
        """
        DEPRECATED (Sprint F183D) - use async_query_sprint_trend() instead.

        Convenience sync wrapper - returns last N sprints ordered by ts DESC.
        For use in sync contexts (e.g., report printing).

        REMOVAL CONDITION: all callers migrated to async read seams.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_sprint_trend, last_n)
            return fut.result()
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return []

    def get_source_leaderboard(self, days: int = 7) -> list[dict]:
        """
        DEPRECATED (Sprint F183D) - use async_query_source_leaderboard() instead.

        Convenience sync wrapper - returns top sources by hit rate.
        For use in sync contexts (e.g., report printing).

        REMOVAL CONDITION: all callers migrated to async read seams.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_source_leaderboard, _time.time() - days * 86400)
            return fut.result()
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return []

    def get_sprint_scorecard_trend(self, last_n: int = 6) -> list[dict]:
        """
        Sprint F150H: Convenience sync wrapper - returns last N scorecards
        ordered by ts DESC. Covers ioc_density, semantic_novelty, accepted_findings,
        findings_per_minute, and outlines_used. Fail-soft, bounded.

        Use for: yield trend reporting, retrospektiva, sprint-to-sprint
        quality comparison without ad-hoc SQL.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_scorecard_trend, last_n)
            return fut.result()
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return []

    def _sync_query_scorecard_trend(self, last_n: int) -> list[dict]:
        """
        Sync - MUST be called on the worker thread.

        F320-6.6: Polars LazyFrame for analytics queries.
        Uses duckdb_fetch_polars() zero-copy path (DuckDB 1.5+ Arrow C Data Interface).
        Streaming collection for bounded memory on large result sets.
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            sql = "\n                SELECT sprint_id, ts, findings_per_minute, ioc_density,\n                       semantic_novelty, outlines_used, accepted_findings, ioc_nodes\n                FROM sprint_scorecard\n                ORDER BY ts DESC\n                LIMIT ?\n                "
            df = self.duckdb_fetch_polars(conn, sql, [last_n])
            if df is None:
                return []
            return df.head(last_n).to_dicts()
        except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
            return []

    def get_sprint_delta_comparison(self, current_sprint_id: str, lookback: int = 4) -> dict:
        """
        Sprint F150H: Compare current sprint against the average of the last
        `lookback` sprints. Returns a delta dict with absolute values of
        current sprint and the delta vs the rolling mean of prior sprints.

        Covers: new_findings, ioc_new_this_sprint, dedup_hits, findings_per_minute,
        uma_peak_gib, synthesis_confidence.

        Use for: "how is this sprint tracking vs history" without ad-hoc SQL.
        Fail-soft - returns empty/near-zero fields on any error.
        """
        if not self._initialized or self._closed:
            return {}
        try:
            fut = self._executor.submit(self._sync_query_delta_comparison, current_sprint_id, lookback)
            return fut.result()
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return {}

    def _sync_query_delta_comparison(self, current_sprint_id: str, lookback: int) -> dict:
        """
        Sync - MUST be called on the worker thread.

        Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return {}
            sql = "\n                SELECT new_findings, ioc_new_this_sprint, dedup_hits,\n                       findings_per_minute, uma_peak_gib, synthesis_confidence\n                FROM sprint_delta\n                WHERE sprint_id = ?\n                "
            current_rows = conn.execute(sql, [current_sprint_id])
            current_rows = list(self.arrow_fetch_batch(conn, sql, [current_sprint_id]))
            if not current_rows:
                return {}
            sql = "\n                SELECT new_findings, ioc_new_this_sprint, dedup_hits,\n                       findings_per_minute, uma_peak_gib, synthesis_confidence\n                FROM sprint_delta\n                WHERE sprint_id != ?\n                ORDER BY ts DESC\n                LIMIT ?\n                "
            prior_rows = conn.execute(sql, [current_sprint_id, lookback])
            prior_rows = list(self.arrow_fetch_batch(conn, sql, [current_sprint_id, lookback]))
            cur = current_rows[0]
            fields = [
                "new_findings",
                "ioc_new_this_sprint",
                "dedup_hits",
                "findings_per_minute",
                "uma_peak_gib",
                "synthesis_confidence",
            ]
            cur_vals = [cur[0] or 0, cur[1] or 0, cur[2] or 0, cur[3] or 0.0, cur[4] or 0.0, cur[5] or 0.0]
            if prior_rows:
                prior_avg = [0.0] * len(fields)
                for pr in prior_rows:
                    for idx, _f in enumerate(fields):
                        v = pr[idx] or (0 if idx < 3 else 0.0)
                        prior_avg[idx] += v / len(prior_rows)
                deltas = {f: round(cur_vals[i] - prior_avg[i], 4) for i, f in enumerate(fields)}
            else:
                deltas = dict.fromkeys(fields, 0.0)
            return {
                "sprint_id": current_sprint_id,
                "current": {f: cur_vals[i] for i, f in enumerate(fields)},
                "vs_prior_mean": deltas,
            }
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return {}

    def get_source_mix_trend(self, days: int = 14) -> list[dict]:
        """
        Sprint F150H: Convenience sync wrapper - returns source_type distribution
        broken down by sprint for the last `days`. Each row contains
        source_type, sprint_id, total_findings, and hit_rate.

        Use for: source mix reporting - is web growing vs feed vs document,
        and is each source getting more productive over time.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_source_mix_trend, _time.time() - days * 86400)
            return fut.result()
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return []

    def _sync_query_source_mix_trend(self, since_ts: float) -> list[dict]:
        """Sync - MUST be called on the worker thread. Uses read pool for parallelism."""
        try:
            conn = self._get_read_conn()
            if conn is None:
                return []
            sql = "\n                SELECT source_type, sprint_id,\n                       SUM(findings_count) as total_findings,\n                       AVG(hit_rate) as avg_hit_rate,\n                       SUM(ioc_count) as total_iocs\n                FROM source_hit_log\n                WHERE ts > ?\n                GROUP BY source_type, sprint_id\n                LIMIT 10000\n                ORDER BY sprint_id DESC, total_findings DESC\n                "
            rows = conn.execute(sql, [since_ts])
            rows = list(self.arrow_fetch_batch(conn, sql, [since_ts]))
            return [
                {
                    "source_type": r[0],
                    "sprint_id": r[1],
                    "total_findings": r[2] or 0,
                    "avg_hit_rate": r[3] or 0.0,
                    "total_iocs": r[4] or 0,
                }
                for r in rows
            ]
        except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
            return []

    def get_yield_trend(self, last_n: int = 8) -> list[dict]:
        """
        Sprint F150H: Derived yield metrics per sprint - new_findings / duration_s,
        dedup_hits ratio (dedup_hits / new_findings), and ioc_rate
        (ioc_new_this_sprint / new_findings). Returns last N sprints.

        Use for: "are we getting better at extracting unique findings from sources"
        - track yield improvement or degradation across sprints.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_yield_trend, last_n)
            return fut.result()
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return []

    def _sync_query_yield_trend(self, last_n: int) -> list[dict]:
        """Sync - MUST be called on the worker thread. Uses read pool for parallelism."""
        try:
            conn = self._get_read_conn()
            if conn is None:
                return []
            sql = "\n                SELECT sprint_id, ts, new_findings, duration_s,\n                       dedup_hits, ioc_new_this_sprint\n                FROM sprint_delta\n                ORDER BY ts DESC\n                LIMIT ?\n                "
            rows = conn.execute(sql, [last_n])
            rows = list(self.arrow_fetch_batch(conn, sql, [last_n]))
            result = []
            for r in rows:
                new_findings = r[2] or 0
                duration_s = r[3] or 0.0
                dedup_hits = r[4] or 0
                ioc_new = r[5] or 0
                result.append(
                    {
                        "sprint_id": r[0],
                        "ts": r[1],
                        "new_findings": new_findings,
                        "duration_s": duration_s,
                        "yield_per_min": round(new_findings / max(duration_s / 60, 0.001), 4),
                        "dedup_ratio": round(dedup_hits / max(new_findings, 1), 4),
                        "ioc_rate": round(ioc_new / max(new_findings, 1), 4),
                    }
                )
            return result
        except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
            return []

    def get_high_value_sprint_ranking(self, last_n: int = 8) -> list[dict]:
        """
        Sprint F150I: Rank last N sprints by a composite value score.
        Composite = accepted_findings * semantic_novelty / max(duration_s, 1).
        Higher is better. Returns sprint_id, composite_score, and component fields.

        Use for: "which sprints delivered the most value per second".
        Fail-soft, bounded.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_high_value_ranking, last_n)
            return fut.result()
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return []

    def _sync_query_high_value_ranking(self, last_n: int) -> list[dict]:
        """Sync - MUST be called on the worker thread. Uses read pool for parallelism."""
        try:
            conn = self._get_read_conn()
            if conn is None:
                return []
            sql = "\n                SELECT\n                    d.sprint_id,\n                    d.ts,\n                    d.new_findings,\n                    d.duration_s,\n                    c.accepted_findings,\n                    c.semantic_novelty,\n                    d.synthesis_confidence,\n                    ROUND(\n                        CAST(c.accepted_findings AS REAL)\n                        * COALESCE(c.semantic_novelty, 1.0)\n                        / MAX(d.duration_s, 1.0),\n                        4\n                    ) AS composite_score\n                FROM sprint_delta d\n                LEFT JOIN sprint_scorecard c ON d.sprint_id = c.sprint_id\n                ORDER BY d.ts DESC\n                LIMIT ?\n                "
            rows = conn.execute(sql, [last_n])
            rows = list(self.arrow_fetch_batch(conn, sql, [last_n]))
            return [
                {
                    "sprint_id": r[0],
                    "ts": r[1],
                    "new_findings": r[2] or 0,
                    "duration_s": r[3] or 0.0,
                    "accepted_findings": r[4] or 0,
                    "semantic_novelty": r[5] or 1.0,
                    "synthesis_confidence": r[6] or 0.0,
                    "composite_score": r[7] or 0.0,
                }
                for r in rows
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []

    def get_scorecard_consistency_check(self, sprint_id: str) -> dict:
        """
        Sprint F150I: Compare findings_per_minute from sprint_scorecard vs
        findings_per_minute from sprint_delta for the same sprint.
        Returns ratio and warns if divergence > 2x.

        Use for: detecting scorecard / delta sync issues.
        Fail-soft - returns empty dict on any error.

        NOTE: As of Sprint F192F, both tables use findings_per_minute (renamed from
        findings_per_min in sprint_delta). The JOIN now compares two same-named columns.
        """
        if not self._initialized or self._closed:
            return {}
        try:
            fut = self._executor.submit(self._sync_query_consistency_check, sprint_id)
            return fut.result()
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return {}

    def _sync_query_consistency_check(self, sprint_id: str) -> dict:
        """
        Sync - MUST be called on the worker thread. Uses read pool for parallelism.

        Sprint F192F §2: both sprint_scorecard and sprint_delta now use findings_per_minute.
        """
        try:
            conn = self._get_read_conn()
            if conn is None:
                return {}
            sql = "\n                SELECT\n                    c.sprint_id,\n                    c.findings_per_minute,\n                    COALESCE(d.findings_per_minute, 0) AS delta_fpm,\n                    d.new_findings,\n                    d.duration_s\n                FROM sprint_scorecard c\n                LEFT JOIN sprint_delta d ON c.sprint_id = d.sprint_id\n                WHERE c.sprint_id = ?\n                ORDER BY rowid DESC\n                LIMIT 1000\n                "
            rows = conn.execute(sql, [sprint_id])
            rows = list(self.arrow_fetch_batch(conn, sql, [sprint_id]))
            if not rows:
                return {}
            r = rows[0]
            scorecard_fpm = r[1] or 0.0
            delta_fpm = r[2] or 0.0
            ratio = round(delta_fpm / max(scorecard_fpm, 0.001), 4)
            return {
                "sprint_id": r[0],
                "scorecard_fpm": scorecard_fpm,
                "delta_fpm": delta_fpm,
                "ratio": ratio,
                "diverges": ratio > 2.0 or ratio < 0.5,
                "new_findings": r[3] or 0,
                "duration_s": r[4] or 0.0,
            }
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return {}

    def get_recent_best_sprints(self, last_n: int = 5) -> list[dict]:
        """
        Sprint F150I: Return the top N sprints by yield (new_findings / duration_s).
        Reads from sprint_delta. Fail-soft, bounded.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_best_sprints, last_n)
            return fut.result()
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return []

    def _sync_query_best_sprints(self, last_n: int) -> list[dict]:
        """
        Sync - MUST be called on the worker thread. Uses read pool for parallelism.

        Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).
        """
        try:
            conn = self._get_read_conn()
            if conn is None:
                return []
            sql = "\n                SELECT sprint_id, ts, new_findings, duration_s,\n                       findings_per_minute, synthesis_confidence,\n                       ROUND(new_findings / MAX(duration_s / 60, 0.001), 4)\n                       AS yield_per_min\n                FROM sprint_delta\n                WHERE new_findings > 0 AND duration_s > 0\n                ORDER BY yield_per_min DESC\n                LIMIT ?\n                "
            rows = conn.execute(sql, [last_n])
            rows = list(self.arrow_fetch_batch(conn, sql, [last_n]))
            return [
                {
                    "sprint_id": r[0],
                    "ts": r[1],
                    "new_findings": r[2] or 0,
                    "duration_s": r[3] or 0.0,
                    "findings_per_minute": r[4] or 0.0,
                    "synthesis_confidence": r[5] or 0.0,
                    "yield_per_min": r[6] or 0.0,
                }
                for r in rows
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []

    def get_recent_worst_sprints(self, last_n: int = 5) -> list[dict]:
        """
        Sprint F150I: Return the bottom N sprints by yield (new_findings / duration_s).
        Only sprints with new_findings > 0 are included (exclude zero-yield noise).
        Reads from sprint_delta. Fail-soft, bounded.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_worst_sprints, last_n)
            return fut.result()
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return []

    def _sync_query_worst_sprints(self, last_n: int) -> list[dict]:
        """
        Sync - MUST be called on the worker thread. Uses read pool for parallelism.

        Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).
        """
        try:
            conn = self._get_read_conn()
            if conn is None:
                return []
            sql = "\n                SELECT sprint_id, ts, new_findings, duration_s,\n                       findings_per_minute, synthesis_confidence,\n                       ROUND(new_findings / MAX(duration_s / 60, 0.001), 4)\n                       AS yield_per_min\n                FROM sprint_delta\n                WHERE new_findings > 0 AND duration_s > 0\n                ORDER BY yield_per_min ASC\n                LIMIT ?\n                "
            rows = conn.execute(sql, [last_n])
            rows = list(self.arrow_fetch_batch(conn, sql, [last_n]))
            return [
                {
                    "sprint_id": r[0],
                    "ts": r[1],
                    "new_findings": r[2] or 0,
                    "duration_s": r[3] or 0.0,
                    "findings_per_min": r[4] or 0.0,
                    "synthesis_confidence": r[5] or 0.0,
                    "yield_per_min": r[6] or 0.0,
                }
                for r in rows
            ]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return []

    async def async_record_activation(
        self, finding_id: str, query: str, source_type: str, confidence: float
    ) -> ActivationResult:
        """
        Record a single finding with WAL-first semantics.

        Order: LMDB WAL first -> DuckDB second.
        If LMDB OK but DuckDB FAIL -> desync=True, LMDB record preserved.
        Caller always receives an ActivationResult.

        Args:
            finding_id:  Unique finding identifier
            query:       Research query text
            source_type: Source type (e.g., "web", "document", "synthetic")
            confidence:  Confidence score [0.0, 1.0]

        Returns:
            ActivationResult with typed fields (never a raw dict)
        """
        if not self._initialized or self._closed:
            return ActivationResult(
                finding_id=finding_id,
                lmdb_success=False,
                duckdb_success=None,
                lmdb_key=f"finding:{finding_id}",
                desync=False,
                error="store closed or not initialized",
                accepted=False,
            )
        if not self._startup_ready.is_set():
            try:
                async with asyncio.timeout(30.0):
                    await self._startup_ready.wait()
            except TimeoutError:
                return ActivationResult(
                    finding_id=finding_id,
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{finding_id}",
                    desync=False,
                    error="startup replay timeout",
                    accepted=False,
                )
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                self._executor, self._activation_record_finding, finding_id, query, source_type, confidence
            )
            desync = bool(result.get("lmdb_success") and result.get("duckdb_success") is False)
            return ActivationResult(
                finding_id=str(finding_id),
                lmdb_success=bool(result.get("lmdb_success")),
                duckdb_success=result.get("duckdb_success"),
                lmdb_key=f"finding:{finding_id}",
                desync=desync,
                error=None,
                accepted=True,
            )
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return ActivationResult(
                finding_id=str(finding_id),
                lmdb_success=False,
                duckdb_success=None,
                lmdb_key=f"finding:{finding_id}",
                desync=False,
                error=str(e),
                accepted=False,
            )

    async def async_record_activation_batch(self, findings: list[dict[str, Any]]) -> list[ActivationResult]:
        """
        Record multiple findings with WAL-first semantics.

        Order: LMDB WAL first (via put_many) -> DuckDB second (chunked batch).
        Returns one ActivationResult per finding in input order.
        Partial failure: if LMDB OK but DuckDB fails for some/all,
        those entries get desync=True.

        Args:
            findings: List of dicts, each must contain:
                      id, query, source_type, confidence

        Returns:
            list[ActivationResult] - one per finding
        """
        if not self._initialized or self._closed:
            return [
                ActivationResult(
                    finding_id=str(f.get("id", "")),
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{f.get('id', '')}",
                    desync=False,
                    error="store closed or not initialized",
                )
                for f in findings
            ]
        if not self._startup_ready.is_set():
            try:
                async with asyncio.timeout(30.0):
                    await self._startup_ready.wait()
            except TimeoutError:
                return [
                    ActivationResult(
                        finding_id=str(f.get("id", "")),
                        lmdb_success=False,
                        duckdb_success=None,
                        lmdb_key=f"finding:{f.get('id', '')}",
                        desync=False,
                        error="startup replay timeout",
                    )
                    for f in findings
                ]
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(self._executor, self._activation_record_findings_batch, findings)
            lmdb_ok = result.get("lmdb_success", False)
            duckdb_ok = result.get("duckdb_success", False)
            failed_ids = set(result.get("failed_ids", []))
            [f.get("id") for f in findings if f.get("id")]
            results: list[ActivationResult] = []
            for f in findings:
                fid = f.get("id", "")
                lmdb_success = lmdb_ok and fid not in failed_ids
                duckdb_success = None
                if lmdb_ok:
                    duckdb_success = duckdb_ok and fid not in failed_ids
                desync = bool(lmdb_ok and duckdb_success is False)
                results.append(
                    ActivationResult(
                        finding_id=str(fid),
                        lmdb_success=lmdb_success,
                        duckdb_success=duckdb_success,
                        lmdb_key=f"finding:{fid}",
                        desync=desync,
                        error=None,
                    )
                )
            return results
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return [
                ActivationResult(
                    finding_id=str(f.get("id", "")),
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{f.get('id', '')}",
                    desync=False,
                    error=str(e),
                )
                for f in findings
            ]

    async def async_record_canonical_finding(self, finding: CanonicalFinding) -> ActivationResult:
        """
        Sprint 8P: Typed ingest API for CanonicalFinding DTO.

        Adapts DTO -> existing WAL-first activation path.
        Používá stejný single-thread write executor jako stávající API.

        DTO -> storage contract mapping:
          finding.finding_id  -> id
          finding.query       -> query
          finding.source_type -> source_type
          finding.confidence  -> confidence
          finding.ts          -> ts (in WAL only)
          finding.provenance  -> LMDB WAL payload (DuckDB nemá provenance sloupec)
          finding.payload_text -> LMDB WAL payload (DuckDB nemá payload_text sloupec)

        Returns ActivationResult with same contract as async_record_activation.

        Provenance: tvrdý invariant - stored in LMDB WAL payload only
        (DuckDB schema nemá provenance_sloupec; backward-compatible,
         probe_8l/probe_8h/probe_8f/probe_8b zůstávají kompatibilní)
        """
        if canonical_source_type is not None and finding.source_type:
            try:
                _raw = (
                    finding.source_type.value
                    if isinstance(finding.source_type, SourceType)
                    else str(finding.source_type)
                )
                if SourceType is not None and _raw not in SourceType._value2member_map_:
                    finding.source_type = canonical_source_type(_raw)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        if not self._initialized or self._closed:
            return ActivationResult(
                finding_id=finding.finding_id,
                lmdb_success=False,
                duckdb_success=None,
                lmdb_key=f"finding:{finding.finding_id}",
                desync=False,
                error="store closed or not initialized",
                accepted=False,
            )
        if not self._startup_ready.is_set():
            try:
                async with asyncio.timeout(30.0):
                    await self._startup_ready.wait()
            except TimeoutError:
                return ActivationResult(
                    finding_id=finding.finding_id,
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{finding.finding_id}",
                    desync=False,
                    error="startup replay timeout",
                    accepted=False,
                )
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(self._executor, self._canonical_finding_to_activation_result, finding)
            desync = bool(result.get("lmdb_success") and result.get("duckdb_success") is False)
            lmdb_ok = bool(result.get("lmdb_success"))
            return ActivationResult(
                finding_id=str(finding.finding_id),
                lmdb_success=lmdb_ok,
                duckdb_success=result.get("duckdb_success"),
                lmdb_key=f"finding:{finding.finding_id}",
                desync=desync,
                error=result.get("error"),
                accepted=lmdb_ok,
            )
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return ActivationResult(
                finding_id=str(finding.finding_id),
                lmdb_success=False,
                duckdb_success=None,
                lmdb_key=f"finding:{finding.finding_id}",
                desync=False,
                error=str(e),
                accepted=False,
            )

    def _canonical_finding_to_activation_result(self, finding: CanonicalFinding) -> dict:
        """
        Sync wrapper: CanonicalFinding DTO -> ActivationResult dict.

        Sprint 8R: DTO -> storage contract mapping:
          finding.finding_id  -> id
          finding.query       -> query
          finding.source_type -> source_type
          finding.confidence  -> confidence
          finding.ts          -> ts (DOUBLE in DuckDB)
          finding.provenance  -> provenance_json (JSON TEXT in DuckDB via msgspec)
          finding.payload_text -> LMDB WAL payload only

        LMDB WAL uses msgspec.json.encode for consistent serialization.
        DuckDB insert uses tuple row (efficient, not dict list).
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        result = {"lmdb_success": False, "duckdb_success": None, "error": None}
        try:
            if not hasattr(self, "_wal_manager") or self._wal_manager is None:
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    result["error"] = "no wal root"
                    return result
                self._wal_manager = DuckDBWALManager(wal_root=_wal_root)
                self._wal_manager.initialize()
            key = f"finding:{finding.finding_id}"
            wal_payload = {
                "id": finding.finding_id,
                "query": finding.query,
                "source_type": finding.source_type,
                "confidence": finding.confidence,
                "ts": finding.ts,
                "provenance": finding.provenance,
                "payload_text": finding.payload_text,
            }
            lmdb_ok = self._wal_manager.wal_put(key, wal_payload)
            result["lmdb_success"] = lmdb_ok
            if not lmdb_ok:
                _logger.warning(f"[Sprint 8P] WAL failed for {finding.finding_id}")
                return result
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            result["error"] = str(e)
            _logger.error(f"[Sprint 8P] WAL exception for {finding.finding_id}: {e}")
            return result
        try:
            provenance_json = _msgspec_encode(finding.provenance).decode()
            duckdb_ok = self._sync_insert_finding(
                finding.finding_id,
                finding.query,
                finding.source_type,
                finding.confidence,
                ts=finding.ts,
                provenance_json=provenance_json,
            )
            result["duckdb_success"] = duckdb_ok
            if not duckdb_ok:
                _logger.error(f"[Sprint 8P] DuckDB failed for {finding.finding_id}, LMDB preserved")
                self._wal_manager.wal_write_pending_sync_marker(
                    finding.finding_id, finding.query, finding.source_type, finding.confidence
                )
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            result["duckdb_success"] = False
            result["error"] = str(e)
            _logger.error(f"[Sprint 8P] DuckDB exception for {finding.finding_id}: {e}, LMDB preserved")
            self._wal_manager.wal_write_pending_sync_marker(
                finding.finding_id, finding.query, finding.source_type, finding.confidence
            )
        return result

    async def _record_fail_open_batch(
        self, findings: list[CanonicalFinding], results: list, indices: list[int]
    ) -> list[ActivationResult]:
        """
        Sprint D7: Batch fail-open path - process N findings whose quality gate threw.

        Replaces N * async_record_canonical_finding() calls with one batch call.
        Order: LMDB WAL first (per finding via wal_put_many) -> DuckDB second (single executemany).

        Returns list[ActivationResult] — one per finding in input order, indexed into results by indices.

        ISSUE-032: WAL sequential (via executor) then DuckDB sequential.
        No _write_semaphore needed — DuckDB serializes writes internally via shared executor.
        """

        import logging as _logging

        _logger = _logging.getLogger(__name__)
        if not findings:
            return []
        ret: list[ActivationResult] = []
        # ISSUE-032: WAL first via executor (WAL is fast I/O, no serialization bottleneck).
        # DuckDB second sequentially. No _write_semaphore needed — DuckDB serializes internally.
        lmdb_ok = False
        try:
            if not hasattr(self, "_wal_manager") or self._wal_manager is None:
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    for f in findings:
                        ret.append(ActivationResult(
                            finding_id=str(f.finding_id),
                            lmdb_success=False,
                            duckdb_success=None,
                            lmdb_key=f"finding:{f.finding_id}",
                            desync=False,
                            error="no wal root",
                            accepted=False,
                        ))
                    return ret
                self._wal_manager = DuckDBWALManager(wal_root=_wal_root)
                self._wal_manager.initialize()
            # B2-FIX: pre-allocate items list — avoids O(n) list reallocation.
            n = len(findings)
            items: list[tuple[str, dict]] = [None] * n  # type: ignore[assignment]
            for i, f in enumerate(findings):
                items[i] = (f"finding:{f.finding_id}", {
                    "id": f.finding_id,
                    "query": f.query,
                    "source_type": f.source_type,
                    "confidence": f.confidence,
                    "ts": f.ts,
                    "provenance": f.provenance,
                    "payload_text": f.payload_text,
                })
            if items:
                loop = asyncio.get_running_loop()
                lmdb_ok = await loop.run_in_executor(
                    self._wal_executor,
                    lambda: self._wal_manager.wal_put_many(items) if hasattr(self._wal_manager, "wal_put_many") else False,
                )
                if not lmdb_ok:
                    _logger.warning(f"[D7] Batch WAL failed for {len(items)} items")
                    for f in findings:
                        ret.append(ActivationResult(
                            finding_id=str(f.finding_id),
                            lmdb_success=False,
                            duckdb_success=None,
                            lmdb_key=f"finding:{f.finding_id}",
                            desync=False,
                            error="lmdb batch failed",
                            accepted=False,
                        ))
                    return ret
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            _logger.error(f"[D7] Batch WAL exception: {e}")
            for f in findings:
                ret.append(ActivationResult(
                    finding_id=str(f.finding_id),
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{f.finding_id}",
                    desync=False,
                    error=str(e),
                    accepted=False,
                ))
            return ret

        # DuckDB sequential via executor (no semaphore)
        duckdb_all_ok = False
        try:
            loop = asyncio.get_running_loop()
            duckdb_count, duckdb_err = await loop.run_in_executor(
                self._duckdb_arrow_executor, self._sync_record_canonical_findings_batch_arrow, findings
            )
            if duckdb_err is not None:
                _logger.error(f"[D7-arrow] DuckDB Arrow failed: {duckdb_err}")
                duckdb_all_ok = False
            elif duckdb_count < len(findings):
                _logger.error(f"[D7-arrow] Partial DuckDB Arrow insert: {duckdb_count}/{len(findings)}")
                duckdb_all_ok = False
            else:
                duckdb_all_ok = True
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            _logger.error(f"[D7] Batch DuckDB exception: {e}, LMDB preserved")
            duckdb_all_ok = False

        accepted_total = 0
        for f in findings:
            lmdb_success = lmdb_ok
            if lmdb_success:
                accepted_total += 1
            ret.append(ActivationResult(
                finding_id=str(f.finding_id),
                lmdb_success=lmdb_success,
                duckdb_success=duckdb_all_ok,
                lmdb_key=f"finding:{f.finding_id}",
                desync=False,
                error=None,
                accepted=bool(lmdb_success),
            ))
        if accepted_total:
            self._quality_state._accepted_count += accepted_total
        return ret

    async def async_record_canonical_findings_batch(self, findings: list[CanonicalFinding]) -> list[ActivationResult]:
        """
        Sprint 8P: Batch typed ingest API for CanonicalFinding DTO list.

        Adapts DTO list -> existing WAL-first batch activation path.
        Používá stejný single-thread write executor jako stávající API.

        Returns list[ActivationResult] - 1:1 mapping, len(results) == len(findings).
        Partial failure: pokud nějaký finding selže, ostatní jsou still processed.
        Celý batch neshodí kvůli jednomu vadnému findingu.
        """
        if not findings:
            return []
        if not self._initialized or self._closed:
            return [
                ActivationResult(
                    finding_id=str(f.finding_id),
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{f.finding_id}",
                    desync=False,
                    error="store closed or not initialized",
                    accepted=False,
                )
                for f in findings
            ]
        if not self._startup_ready.is_set():
            try:
                async with asyncio.timeout(30.0):
                    await self._startup_ready.wait()
            except TimeoutError:
                return [
                    ActivationResult(
                        finding_id=str(f.finding_id),
                        lmdb_success=False,
                        duckdb_success=None,
                        lmdb_key=f"finding:{f.finding_id}",
                        desync=False,
                        error="startup replay timeout",
                        accepted=False,
                    )
                    for f in findings
                ]
        # ISSUE-032: Arrow pipeline with WAL-first ordering and concurrent DuckDB writes.
        # _write_semaphore removed — DuckDB serializes writes internally.
        # WAL and DuckDB run concurrently; WAL is awaited first to maintain WAL-first invariant.
        try:
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                self._executor, self._sync_record_canonical_findings_batch_arrow_standalone, findings
            )
            if (
                results
                and any((r.get("lmdb_success") for r in results))
                and self.truth_write_graph_supports_buffered_writes()
            ):
                await self._graph_ingest_findings(findings)
            if results and any((r.get("lmdb_success") for r in results)):
                self._semantic_buffer_findings(findings)
            accepted_total = sum((1 for r in results if r.get("lmdb_success")))
            self._quality_state._accepted_count += accepted_total
            return [
                ActivationResult(
                    finding_id=str(r.get("finding_id", "")),
                    lmdb_success=bool(r.get("lmdb_success")),
                    duckdb_success=r.get("duckdb_success"),
                    lmdb_key=f"finding:{r.get('finding_id', '')}",
                    desync=bool(r.get("lmdb_success") and r.get("duckdb_success") is False),
                    error=r.get("error"),
                    accepted=bool(r.get("lmdb_success")),
                )
                for r in results
            ]
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return [
                ActivationResult(
                    finding_id=str(f.finding_id),
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{f.finding_id}",
                    desync=False,
                    error=str(e),
                    accepted=False,
                )
                for f in findings
            ]

    async def async_record_canonical_findings_batch_arrow(
        self, findings: list[CanonicalFinding]
    ) -> list[ActivationResult]:
        """
        Sprint P0-4: Arrow zero-copy batch ingest for CanonicalFinding DTO list.

        10-stupňový fallback na legacy `async_record_canonical_findings_batch`:
          1. `HLEDAC_ARROW_INGEST == "0"` (env gate, default ON, opt-out) -> legacy
          2. `len(findings) < _ARROW_MIN_BATCH` (default 5) -> legacy
          3. pyarrow není dostupný (cached O(1) check) -> legacy
          4. store not initialized or closed -> legacy
          5. startup barrier timeout (30 s) -> legacy
          6. WAL executor error -> legacy
          7. WAL phase failed (wal_ok is False) -> legacy
          8. DuckDB executor threw exception -> legacy
          9. sync helper returned empty results -> legacy
          10. all duckdb_success=False despite non-empty results -> legacy

        ISSUE-032: WAL parallel (executor) + DuckDB sequential (executor).
        No _write_semaphore needed — DuckDB serializes internally via shared executor.
        Při úspěchu: WAL first (LMDB put_many) + DuckDB Arrow register+INSERT (one roundtrip).
        Zero-copy: pa.array() drží C++ buffery, DuckDB čte přes Arrow C Data Interface.

        Invarianty (shodné s legacy):
          - 1:1 result mapping (len(results) == len(findings))
          - `self._quality_state._accepted_count` += count of lmdb_success
          - graph + semantic buffer triggered for accepted findings
          - thread-affine execution on `self._executor`
          - LMDB WAL precedes DuckDB (recovery invariant)

        Returns list[ActivationResult]. Empty list for empty findings input.
        """
        if not findings:
            return []
        if not _ARROW_INGEST_ENABLED:
            self._arrow_metrics["arrow_fallback_env"] += len(findings)
            logger.debug(f"[D7-arrow-fallback] HLEDAC_ARROW_INGEST=0, using legacy path for {len(findings)} findings")
            return await self.async_record_canonical_findings_batch(findings)
        if len(findings) < _ARROW_MIN_BATCH:
            self._arrow_metrics["arrow_fallback_batch"] += len(findings)
            logger.debug(
                f"[D7-arrow-fallback] batch size {len(findings)} < _ARROW_MIN_BATCH({_ARROW_MIN_BATCH}), using legacy path"
            )
            return await self.async_record_canonical_findings_batch(findings)
        if not _check_pyarrow_available():
            self._arrow_metrics["arrow_fallback_pyarrow"] += len(findings)
            logger.debug(f"[D7-arrow-fallback] pyarrow not available, using legacy path for {len(findings)} findings")
            return await self.async_record_canonical_findings_batch(findings)
        if not self._initialized or self._closed:
            return await self.async_record_canonical_findings_batch(findings)
        if not self._startup_ready.is_set():
            try:
                async with asyncio.timeout(30.0):
                    await self._startup_ready.wait()
            except TimeoutError:
                self._arrow_metrics["arrow_fallback_init"] += len(findings)
                return await self.async_record_canonical_findings_batch(findings)

        # ISSUE-032: WAL parallel (fast I/O). DuckDB sequential via shared executor.
        # Replaces old _write_semaphore(4) which serialized both unnecessarily.
        loop = asyncio.get_running_loop()
        wal_future = loop.run_in_executor(self._wal_executor, self._wal_put_many_sync, findings)
        duckdb_future = loop.run_in_executor(self._duckdb_arrow_executor, self._duckdb_arrow_sync, findings)

        # Wait for WAL first (WAL-first invariant)
        wal_ok_or_exc: bool | Exception = await wal_future
        if isinstance(wal_ok_or_exc, Exception):
            self._arrow_metrics["arrow_fallback_executor"] += len(findings)
            logger.warning(
                f"[D7-arrow-fallback] WAL executor error ({wal_ok_or_exc}), using legacy path for {len(findings)} findings"
            )
            return await self.async_record_canonical_findings_batch(findings)
        wal_ok = wal_ok_or_exc
        if not wal_ok:
            self._arrow_metrics["arrow_fallback_empty"] += len(findings)
            _logger.error(
                "[D7] Arrow WAL phase failed for %d findings - falling back to legacy executemany.", len(findings)
            )
            return await self.async_record_canonical_findings_batch(findings)

        # Wait for DuckDB result (from run_in_executor)
        try:
            duckdb_result: tuple[int, str | None] | Exception = await duckdb_future
        except asyncio.CancelledError:
            duckdb_result = (0, "cancelled")

        if isinstance(duckdb_result, Exception):
            self._arrow_metrics["arrow_fallback_executor"] += len(findings)
            logger.warning(
                f"[D7-arrow-fallback] DuckDB executor error ({duckdb_result}), using legacy path for {len(findings)} findings"
            )
            return await self.async_record_canonical_findings_batch(findings)
        logger.debug(f"[D7-arrow] WAL ok, DuckDB result={duckdb_result!r} batch={len(findings)}")
        duckdb_count, duckdb_err = duckdb_result

        if duckdb_err is not None:
            _logger.error(f"[D7] DuckDB Arrow bulk failed: {duckdb_err}")
            duckdb_all_ok = False
        elif duckdb_count < len(findings):
            self._arrow_metrics["arrow_partial_duplicates"] += 1
            _logger.debug(
                f"[D7-arrow] DuckDB MERGE deduplication: {duckdb_count}/{len(findings)} inserted (rest were deduplicated by MERGE)"
            )
            duckdb_all_ok = True
        else:
            duckdb_all_ok = True
        results = [
            {"finding_id": f.finding_id, "lmdb_success": wal_ok, "duckdb_success": duckdb_all_ok, "error": duckdb_err}
            for f in findings
        ]
        if not results:
            self._arrow_metrics["arrow_fallback_empty"] += len(findings)
            _logger.error(
                "[D7] Arrow path returned 0 results for %d findings - falling back to legacy executemany. Enable HLEDAC_ARROW_INGEST=0 to use legacy path only.",
                len(findings),
            )
            return await self.async_record_canonical_findings_batch(findings)
        if results and all((r.get("duckdb_success") is False for r in results)):
            self._arrow_metrics["arrow_fallback_all_fail"] += len(results)
            errors_in_results = [r.get("error") for r in results if r.get("error")]
            if errors_in_results and errors_in_results[0] == "duckdb_error":
                self._arrow_metrics["arrow_error_duckdb_insert"] += len(results)
            elif errors_in_results and errors_in_results[0] == "table_build":
                self._arrow_metrics["arrow_error_table_build"] += len(results)
            else:
                self._arrow_metrics["arrow_error_partial"] += len(results)
            logger.error(
                "[D7] Arrow path: all %d findings failed DuckDB write - falling back to legacy executemany.",
                len(results),
            )
            return await self.async_record_canonical_findings_batch(findings)
        if results and any((r.get("lmdb_success") for r in results)):
            if self.truth_write_graph_supports_buffered_writes():
                await self._graph_ingest_findings(findings)
            self._semantic_buffer_findings(findings)
        accepted_total = sum((1 for r in results if r.get("lmdb_success")))
        self._quality_state._accepted_count += accepted_total
        self._arrow_metrics["arrow_selected"] += len(findings)
        self._arrow_metrics["arrow_success_count"] += len(findings)
        lmdb_ok = sum((1 for r in results if r.get("lmdb_success")))
        duckdb_ok = sum((1 for r in results if r.get("duckdb_success")))
        self._arrow_metrics["arrow_success_lmdb_count"] += lmdb_ok
        self._arrow_metrics["arrow_success_duckdb_count"] += duckdb_ok
        logger.info(f"[D7-arrow] path=arrow batch={len(findings)} lmdb_ok={lmdb_ok} duckdb_ok={duckdb_ok}")
        return [
            ActivationResult(
                finding_id=str(r.get("finding_id", "")),
                lmdb_success=bool(r.get("lmdb_success")),
                duckdb_success=r.get("duckdb_success"),
                lmdb_key=f"finding:{r.get('finding_id', '')}",
                desync=bool(r.get("lmdb_success") and r.get("duckdb_success") is False),
                error=r.get("error"),
                accepted=bool(r.get("lmdb_success")),
            )
            for r in results
        ]

    async def async_get_recent_findings(self, limit: int = 10) -> list[CanonicalFinding]:
        """
        Sprint F800A: Controller-facing async adapter for recent findings.

        Thin wrapper around async_query_recent_findings - converts raw dict rows
        to CanonicalFinding instances so callers receive typed DTOs.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Returns empty list if store is closed or uninitialized.

        Args:
            limit: Maximum number of findings to return (ordered by ts DESC).

        Returns:
            list[CanonicalFinding] - ordered by ts descending, most recent first.
        """
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            rows: list[dict] = await loop.run_in_executor(self._executor, self._sync_query_findings, limit)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return []
        findings: list[CanonicalFinding] = []
        for row in rows:
            try:
                provenance: tuple[str, ...] = ()
                raw_prov = row.get("provenance_json") or row.get("provenance")
                if raw_prov:
                    if isinstance(raw_prov, str):
                        try:
                            decoded = msgspec.json.decode(raw_prov.encode())
                            if isinstance(decoded, list):
                                provenance = tuple((str(v) for v in decoded))
                        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            provenance = ()
                    elif isinstance(raw_prov, list):
                        provenance = tuple((str(v) for v in raw_prov))
                finding = CanonicalFinding(
                    finding_id=str(row.get("id", row.get("finding_id", ""))),
                    query=str(row.get("query", "")),
                    source_type=str(row.get("source_type", "")),
                    confidence=float(row.get("confidence", 0.0)),
                    ts=float(row.get("ts", 0.0)),
                    provenance=provenance,
                    payload_text=row.get("payload_text"),
                )
                findings.append(finding)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                continue
        return findings

    async def async_upsert_target_profile(self, profile: TargetProfileSummary) -> None:
        """
        Sprint F202K: Insert or update a target profile.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Silently fails if store is closed or uninitialized.
        """
        if not self._initialized or self._closed:
            return
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._sync_upsert_target_profile, profile)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            pass

    async def async_get_target_profile(self, target_id: str) -> TargetProfileSummary | None:
        """
        Sprint F202K: Get a target profile by target_id.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Returns None if not found or on error.
        """
        if not self._initialized or self._closed:
            return None
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_get_target_profile, target_id)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return None

    async def async_upsert_target_memory(self, memory: TargetMemory) -> None:
        """
        Sprint F204D: Insert or update target memory from a TargetMemory.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Silently fails if store is closed or uninitialized.

        F206H FIX: Previously accepted TargetMemoryUpdate and silently failed
        (type mismatch with _sync_upsert_target_memory which expects TargetMemory).
        Now accepts TargetMemory directly - caller (SprintScheduler) passes
        the already-merged memory from TargetMemoryService.merge_update().
        """
        if not self._initialized or self._closed:
            return
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._sync_upsert_target_memory, memory)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort; DB write failure; non-critical
            pass

    async def async_get_target_memory(self, target_id: str) -> TargetMemory | None:
        """
        Sprint F204D: Get target memory by target_id.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Returns None if not found or on error.
        """
        if not self._initialized or self._closed:
            return None
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            from hledac.universal.knowledge.target_memory import TargetMemory

            def _sync_get():
                conn = self._file_conn if self._db_path else self._persistent_conn
                if conn is None:
                    return None
                result = conn.execute("SELECT * FROM target_memory WHERE target_id = ?", [target_id]).fetchone()
                if result is None:
                    return None
                return TargetMemory(
                    target_id=result[0],
                    first_seen_ts=result[1],
                    last_seen_ts=result[2],
                    sprint_count=result[3],
                    cumulative_finding_count=result[4],
                    entity_facets=_json_loads_flexible(result[5]),
                    exposure_facets=_json_loads_flexible(result[6]),
                    pivot_facets=_json_loads_flexible(result[7]),
                    confidence_drift=_json_loads_flexible(result[8]),
                    updated_by_sprint_id=result[9],
                )

            return await loop.run_in_executor(self._executor, _sync_get)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort; async operation failure; non-critical
            return None

    async def async_record_research_session(
        self,
        session_id: str,
        sprint_id: str,
        query: str,
        ts: float,
        findings_count: int,
        accepted_count: int,
        gaps_json: str,
        entities_json: str,
        source_patterns_json: str,
        unexplored_angles_json: str,
        temporal_anomalies_json: str,
    ) -> bool:
        """Sprint F350M: Record research session outcome."""
        if not self._initialized or self._closed:
            return False
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_record_research_session,
                session_id,
                sprint_id,
                query,
                ts,
                findings_count,
                accepted_count,
                gaps_json,
                entities_json,
                source_patterns_json,
                unexplored_angles_json,
                temporal_anomalies_json,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort; async operation failure; non-critical
            return False

    async def async_record_entity_observations_bulk(self, observations: list[dict[str, Any]]) -> int:
        """Sprint F350M: Bulk record entity observations."""
        if not self._initialized or self._closed:
            return 0
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_record_entity_observations_bulk, observations)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort; async operation failure; non-critical
            return 0

    async def async_get_research_sessions_by_sprint(self, sprint_id: str) -> list[dict[str, Any]]:
        """Sprint F350M: Get research sessions by sprint_id."""
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_get_research_sessions_by_sprint, sprint_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort; async operation failure; non-critical
            return []

    async def async_get_entity_observations_by_entity(self, entity_value: str, limit: int = 50) -> list[dict[str, Any]]:
        """Sprint F350M: Get entity observations by entity value."""
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor, self._sync_get_entity_observations_by_entity, entity_value, limit
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort; async operation failure; non-critical
            return []

    async def async_get_recent_research_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        """Sprint F350M: Get recent research sessions."""
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_get_recent_research_sessions, limit)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort; async operation failure; non-critical
            return []

    async def async_get_previous_findings_for_target(
        self, target_id: str, before_sprint_id: str | None = None, limit: int = 1000
    ) -> list[CanonicalFinding]:
        """
        Sprint F202K: Get previous findings for a target, optionally before a specific sprint.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Returns canonical findings ordered by ts DESC.

        Args:
            target_id: The target identifier to filter findings by.
            before_sprint_id: Optional sprint ID to filter findings before this sprint.
            limit: Maximum number of findings to return (default 1000).

        Returns:
            list[CanonicalFinding] - ordered by ts descending, most recent first.
            Returns empty list if store is closed, uninitialized, or query fails.
        """
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            rows: list[dict] = await loop.run_in_executor(
                self._executor, self._sync_get_previous_findings_for_target, target_id, before_sprint_id, limit
            )
        except Exception:  # noqa: BLE001 — best-effort; async operation failure; non-critical
            return []
        findings: list[CanonicalFinding] = []
        for row in rows:
            try:
                provenance: tuple[str, ...] = ()
                raw_prov = row.get("provenance_json") or row.get("provenance")
                if raw_prov:
                    if isinstance(raw_prov, str):
                        try:
                            decoded = msgspec.json.decode(raw_prov.encode())
                            if isinstance(decoded, list):
                                provenance = tuple((str(v) for v in decoded))
                        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            provenance = ()
                    elif isinstance(raw_prov, list):
                        provenance = tuple((str(v) for v in raw_prov))
                finding = CanonicalFinding(
                    finding_id=str(row.get("finding_id", row.get("id", ""))),
                    query=str(row.get("query", "")),
                    source_type=str(row.get("source_type", "")),
                    confidence=float(row.get("confidence", 0.0)),
                    ts=float(row.get("ts", 0.0)),
                    provenance=provenance,
                    payload_text=row.get("payload_text"),
                )
                findings.append(finding)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                continue
        return findings

    async def async_record_hypothesis_feedback(self, record: Any) -> bool:
        """
        Sprint F203G: Record a single hypothesis_feedback entry.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Silently fails if store is closed or uninitialized.

        Args:
            record: HypothesisFeedbackRecord (frozen dataclass) with fields:
                id, target_id, pivot_type, ioc_type, produced_count,
                accepted_count, signal_value, ts.

        Returns:
            True if recorded, False otherwise.
        """
        if not self._initialized or self._closed:
            return False
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._sync_record_hypothesis_feedback, record)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return False

    async def async_get_hypothesis_feedback(self, target_id: str | None = None, limit: int = 1000) -> list[Any]:
        """
        Sprint F203G: Fetch aggregated hypothesis_feedback records.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.

        Args:
            target_id: If provided, filter by target_id. If None, returns all.
            limit: Maximum records to return (default 1000).

        Returns:
            List of HypothesisFeedbackRecord instances ordered by ts DESC.
            Returns empty list if store is closed or uninitialized.
        """
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            rows: list[dict] = await loop.run_in_executor(
                self._executor, self._sync_get_hypothesis_feedback, target_id, limit
            )
        except Exception:  # noqa: BLE001 — best-effort; async operation failure; non-critical
            return []
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackRecord

        records: list[Any] = []
        for row in rows:
            try:
                records.append(
                    HypothesisFeedbackRecord(
                        id=str(row["id"]),
                        target_id=str(row["target_id"]),
                        pivot_type=str(row["pivot_type"]),
                        ioc_type=str(row["ioc_type"]),
                        produced_count=int(row["produced_count"] or 0),
                        accepted_count=int(row["accepted_count"] or 0),
                        signal_value=float(row["signal_value"] or 0.0),
                        ts=float(row["ts"] or 0.0),
                    )
                )
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                continue
        return records

    async def async_record_hypothesis_tracking(
        self,
        hypothesis_id: str,
        sprint_id: str,
        hypothesis_text: str,
        status: str,
        confidence: float,
        falsification_result: str | None = None,
        disproved_by_sprint_id: str | None = None,
    ) -> bool:
        """
        F214: Persist a hypothesis tracking record for cross-sprint lineage.
        Supports: "we hypothesized X in sprint N, disproved by sprint M."

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Fail-soft: returns False silently on error.
        """
        if not self._initialized or self._closed:
            return False
        self.ensure_connected()
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._executor,
                self._sync_record_hypothesis_tracking,
                hypothesis_id,
                sprint_id,
                hypothesis_text,
                status,
                confidence,
                falsification_result,
                disproved_by_sprint_id,
            )
            return True
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return False

    def _sync_record_hypothesis_tracking(
        self,
        hypothesis_id: str,
        sprint_id: str,
        hypothesis_text: str,
        status: str,
        confidence: float,
        falsification_result: str | None,
        disproved_by_sprint_id: str | None,
    ) -> None:
        """Sync writer for hypothesis_tracking."""
        conn = self._get_conn()
        if conn is None:
            return
        try:
            conn.execute(
                self._statements._SQL_INSERT_HYPOTHESIS_TRACKING,
                (
                    hypothesis_id,
                    sprint_id,
                    hypothesis_text,
                    status,
                    confidence,
                    falsification_result,
                    disproved_by_sprint_id,
                    time.time(),
                ),
            )
        except (OSError, RuntimeError) as e:
            logger.warning(f"[DUCKDB] insert_hypothesis_tracking failed: {e}")
    def _extract_url_from_provenance(self, provenance: tuple[str, ...]) -> str:
        """
        Sprint 8AK: Extract the first HTTP(S) URL from a provenance tuple.

        Source-agnostic: scans all positions regardless of source type.
        Returns empty string if no URL is found.
        """
        if not provenance:
            return ""
        for item in provenance:
            if isinstance(item, str) and item.startswith("http"):
                return item
        return ""

    def _record_quality_rejection(self, finding: CanonicalFinding, decision: FindingQualityDecision) -> None:
        """
        Sprint F216G: Record a quality gate rejection to the bounded ledger.

        Delegates to QualityAssessmentState.record_rejection().
        """
        self._quality_state.record_rejection(finding, decision)

    def _check_semantic_duplicate(
        self,
        dedup_cache,
        text_for_embed: str,
        threshold: float,
    ) -> tuple[bool, str | None]:
        """
        Sprint F350M-R: Shared helper for semantic dedup check.

        ISSUE-022: Extracted from duplicated blocks in _apply_stateful_quality_checks
        and _assess_finding_quality_batch. Eliminates 6+ copy-paste instances.

        Returns (True, reason) if duplicate, (False, None) if not.
        Fail-soft: any exception returns (False, None).
        """
        try:
            if text_for_embed and len(text_for_embed) >= 16:
                is_dup = dedup_cache.check_and_cache(text_for_embed, threshold=threshold)
                if is_dup:
                    self._quality_state._quality_duplicate_count += 1
                    return (True, "semantic_duplicate")
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        return (False, None)

    def _check_guard4_short_fingerprint(
        self,
        fp: str,
        url_fp: str,
        finding: CanonicalFinding,
        idx: int | None,
        dedup_batch: list[tuple[str, str]] | None,
        is_feed_source: bool,
    ) -> FindingQualityDecision | None:
        """
        F360M-R: Guard 4 — short fingerprint path in _run_stateful_quality_guards.

        Returns FindingQualityDecision if guard reached terminal state (caller sets
        results[idx] and returns True).  Returns None if guard should fall through
        to the accept path in the caller.

        ISSUE-FXXX: Extracted from _run_stateful_quality_guards to eliminate
        4-level nested if/for/if/for block at the Guard 4 site
        (nesting depth 5 → 2; cyclomatic complexity -4).
        """
        dedup_cache = self._dedup_manager.semantic_dedup_cache if self._dedup_manager else None
        if dedup_cache is not None:
            text_for_embed = url_fp or (finding.payload_text or finding.query)
            if text_for_embed and len(text_for_embed) >= 16:
                _semantic_thresh = 0.8 if is_feed_source else 0.85
                is_dup, dup_reason = self._check_semantic_duplicate(
                    dedup_cache, text_for_embed, _semantic_thresh,
                )
                if is_dup:
                    return FindingQualityDecision(
                        accepted=False, reason=dup_reason,
                        entropy=0.0, normalized_hash=fp, duplicate=True,
                    )
        if dedup_batch is not None:
            dedup_batch.append((fp, finding.finding_id))
        if not is_feed_source:
            self._add_to_hot_cache(fp, finding.finding_id)
        return FindingQualityDecision(
            accepted=True, reason="short_string_skip", entropy=0.0, normalized_hash=fp, duplicate=False,
        )

    def _check_guard7_ioc_duplicate(
        self,
        idx: int | None,
        has_any_ioc: list[bool] | None,
        ioc_offsets: list[int] | None,
        ioc_items: list[list[tuple[str, str]]] | None,
        ioc_dup_flags: list[bool],
        entropy: float,
        fp: str,
        finding: CanonicalFinding,
        results: list,
        new_iocs_for_batch: list[tuple[str, str, float]],
    ) -> bool:
        """
        F360M-R: Guard 7 — IOC duplicate check (batch mode only).

        Returns True if guard reached terminal state (caller should return True).
        Returns False if guard passed — caller continues to accept path.

        ISSUE-FXXX: Extracted from _run_stateful_quality_guards to eliminate
        4-level nested if block (idx/has_any_ioc/has_any_ioc[idx]/any(...)).
        """
        if idx is None or has_any_ioc is None or ioc_offsets is None:
            return False

        if not has_any_ioc[idx]:
            return False

        ioc_start = ioc_offsets[idx]
        ioc_end = ioc_offsets[idx + 1]
        finding_ioc_dup_flags = ioc_dup_flags[ioc_start:ioc_end]
        finding_iocs = ioc_items[idx] if ioc_items else []

        if any(finding_ioc_dup_flags):
            self._quality_state._quality_duplicate_count += 1
            results[idx] = FindingQualityDecision(
                accepted=False, reason="ioc_duplicate", entropy=entropy, normalized_hash=fp, duplicate=True,
            )
            return True

        new_iocs_for_batch.clear()
        new_iocs_for_batch.extend(
            (val, ioc_type, float(finding.confidence))
            for (val, ioc_type), is_dup in zip(finding_iocs, finding_ioc_dup_flags)
            if not is_dup
        )
        return False

    def _extract_ioc_batch(
        self, findings: list[CanonicalFinding]
    ) -> tuple[list[list[tuple[str, str]]], list[bool]]:
        """
        F360M-R: Extract and dedupe IOCs from findings batch via Rust.

        ISSUE-FXXX: Extracted from _apply_stateful_quality_checks to eliminate
        3-level nested loop (if/for/for) at the top of the batch processing.

        Returns (ioc_items, has_any_ioc) where:
          - ioc_items: per-finding deduplicated IOC list
          - has_any_ioc: per-finding bool flag
        """
        n = len(findings)
        ioc_items: list[list[tuple[str, str]]] = []
        has_any_ioc: list[bool] = []

        if _IOC_EXTRACT_BATCH_AVAILABLE and _get_rust_batch_ioc_extract() is not None:
            texts_for_ioc = []
            for f in findings:
                pt = f.payload_text if f.payload_text else f.query or ""
                texts_for_ioc.append(pt[:5000] if pt else "")
            try:
                raw_iocs = _get_rust_batch_ioc_extract()(texts_for_ioc)
                for ioc_list in raw_iocs:
                    seen_types: set[str] = set()
                    deduped: list[tuple[str, str]] = []
                    for val, ioc_type in ioc_list:
                        key = (val, ioc_type)
                        if key not in seen_types:
                            seen_types.add(key)
                            deduped.append(key)
                    ioc_items.append(deduped)
                    has_any_ioc.append(bool(deduped))
            except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
                ioc_items = [[] for _ in findings]
                has_any_ioc = [False] * n
        else:
            ioc_items = [[] for _ in findings]
            has_any_ioc = [False] * n

        return ioc_items, has_any_ioc

    def _run_stateful_quality_guards(
        self,
        fp: str,
        entropy: float,
        url_fp: str,
        is_feed_source: bool,
        finding: CanonicalFinding,
        results: list,
        idx: int | None,
        dedup_batch: list[tuple[str, str]] | None,
        new_iocs_for_batch: list[tuple[str, str, float]] | None,
        ioc_offsets: list[int] | None,
        has_any_ioc: list[bool] | None,
        ioc_items: list[list[tuple[str, str]]] | None,
        ioc_dup_flags: list[bool] | None,
    ) -> bool:
        """
        F360M-R: Shared stateful quality guard runner — eliminates 8 Type-1 exact duplicates
        across _apply_stateful_quality_checks, _assess_finding_quality_batch, and
        _assess_finding_quality.

        Runs guards 1-7 in order:
          1. Hot cache duplicate
          2. Persistent dedup store hit
          3. URL-based finding → accept immediately
          4. Short fingerprint → semantic dedup then accept
          5. Low entropy → reject
          6. Semantic duplicate
          7. IOC duplicate → reject

        For batch callers (idx is not None):
          - On reject: sets results[idx] and returns True (skip further processing)
          - On accept (guards 3, 4, 7 terminal): sets results[idx] and returns True
          - On guard-7 non-terminal: appends to dedup_batch, sets results[idx], returns True
        For single caller (idx is None):
          - Same logic but returns FindingQualityDecision via early returns

        Returns True if guard reached terminal state (caller should continue/skip).
        The new_iocs_for_batch/ioc_offsets/has_any_ioc/ioc_items/ioc_dup_flags params
        are used only for guard 7 (IOC dedup) in batch mode.

        ISSUE-FXXX: Guards 1-2 + 3 + 4 short-circuit; guard 7 is terminal on IOC dup,
        otherwise falls through to the accept path in the caller.
        """
        # Guard 1: hot cache duplicate
        if self._hot_cache_lookup(fp) is not None:
            self._quality_state._quality_duplicate_count += 1
            reason = "persistent_duplicate" if url_fp else "duplicate_detected"
            decision = FindingQualityDecision(
                accepted=False, reason=reason, entropy=entropy, normalized_hash=fp, duplicate=True,
            )
            if idx is not None:
                results[idx] = decision
                return True
            self._record_quality_rejection(finding, decision)
            return False  # Signal: caller should return decision (already set externally for single)

        # Guard 2: persistent dedup store hit
        stored_id = self._lookup_persistent_dedup(fp)
        if stored_id is not None:
            self._add_to_hot_cache(fp, stored_id)
            self._quality_state._persistent_duplicate_count += 1
            reason = "persistent_duplicate" if url_fp else "duplicate_detected"
            decision = FindingQualityDecision(
                accepted=False, reason=reason, entropy=entropy, normalized_hash=fp, duplicate=True,
            )
            if idx is not None:
                results[idx] = decision
                return True
            self._record_quality_rejection(finding, decision)
            return False

        # Guard 3: URL-based finding — accept immediately
        if url_fp:
            if dedup_batch is not None:
                dedup_batch.append((fp, finding.finding_id))
            if not is_feed_source:
                self._add_to_hot_cache(fp, finding.finding_id)
            if idx is not None:
                results[idx] = FindingQualityDecision(
                    accepted=True, reason=None, entropy=entropy, normalized_hash=fp, duplicate=False,
                )
                return True
            return False

        # Guard 4: short fingerprint — semantic dedup only, then accept
        if len(fp) < _QUALITY_MIN_ENTROPY_LEN:
            result = self._check_guard4_short_fingerprint(
                fp=fp, url_fp=url_fp, finding=finding,
                idx=idx, dedup_batch=dedup_batch, is_feed_source=is_feed_source,
            )
            if result is not None:
                results[idx] = result
                return True
            return False

        # Guard 5: low entropy — reject
        _threshold = 0.3 if is_feed_source else _QUALITY_ENTROPY_THRESHOLD
        if entropy < _threshold:
            self._quality_state._quality_rejected_count += 1
            decision = FindingQualityDecision(
                accepted=False, reason="low_entropy_rejected", entropy=entropy, normalized_hash=fp, duplicate=False,
            )
            if idx is not None:
                results[idx] = decision
                return True
            self._record_quality_rejection(finding, decision)
            return False

        # Guard 6: semantic duplicate
        dedup_cache = self._dedup_manager.semantic_dedup_cache if self._dedup_manager else None
        if dedup_cache is not None:
            text_for_embed = url_fp or (finding.payload_text or finding.query)
            if text_for_embed and len(text_for_embed) >= 16:
                _semantic_thresh = 0.8 if is_feed_source else 0.85
                is_dup, dup_reason = self._check_semantic_duplicate(
                    dedup_cache, text_for_embed, _semantic_thresh,
                )
                if is_dup:
                    decision = FindingQualityDecision(
                        accepted=False, reason=dup_reason,
                        entropy=entropy, normalized_hash=fp, duplicate=True,
                    )
                    if idx is not None:
                        results[idx] = decision
                        return True
                    self._record_quality_rejection(finding, decision)
                    return False

        # Guard 7: IOC duplicate (batch only — for single, IOC check is done by caller)
        if self._check_guard7_ioc_duplicate(
            idx=idx,
            has_any_ioc=has_any_ioc,
            ioc_offsets=ioc_offsets,
            ioc_items=ioc_items,
            ioc_dup_flags=ioc_dup_flags,
            entropy=entropy,
            fp=fp,
            finding=finding,
            results=results,
            new_iocs_for_batch=new_iocs_for_batch,
        ):
            return True

        # Accept: write dedup, hot_cache, IOC batch
        if dedup_batch is not None:
            dedup_batch.append((fp, finding.finding_id))
        if not is_feed_source:
            self._add_to_hot_cache(fp, finding.finding_id)
        if idx is not None:
            results[idx] = FindingQualityDecision(
                accepted=True, reason=None, entropy=entropy, normalized_hash=fp, duplicate=False,
            )
            return True
        return False

    def get_quality_rejection_ledger(self) -> tuple[QualityRejectionRecord, ...]:
        """
        Sprint F216G: Expose the quality rejection ledger to callers (e.g. scheduler).

        Returns a tuple (immutable view) of all recorded rejection records.
        Delegates to QualityAssessmentState for backward compat.
        """
        return self._quality_state.get_rejection_history()

    def _apply_stateful_quality_checks(
        self, findings: list[CanonicalFinding], rust_decisions: list[dict]
    ) -> list[FindingQualityDecision]:
        """
        ISSUE-022: Apply stateful quality checks after Rust pure-compute decisions.

        Rust assess_findings_quality_batch() handles pure compute:
          URL fp, normalize, entropy, dedup fp — all rayon-parallel.

        This method applies the stateful checks that Rust cannot do:
          hot_cache lookup, LMDB persistent dedup, semantic dedup cache,
          IOC dedup flags, persistent dedup storage.

        Mirrors the stateful parts of the legacy _assess_finding_quality_batch loop.
        Returns list[FindingQualityDecision] in same order as findings.
        """
        # Lazy import to break circular dependency with quality_assessment.py
        from .quality_assessment import _HIGH_CONF_IOC_RE, FindingQualityDecision

        n = len(findings)
        results: list[FindingQualityDecision] = [FindingQualityDecision(
            accepted=True, reason=None, entropy=0.0, normalized_hash="", duplicate=False
        )] * n

        # Pre-compute IOC dedup flags (same as legacy, before the per-finding loop)
        ioc_items, has_any_ioc = self._extract_ioc_batch(findings)

        all_iocs_flat: list[tuple[str, str]] = []
        ioc_offsets: list[int] = [0]
        for ioc_list in ioc_items:
            ioc_offsets.append(ioc_offsets[-1] + len(ioc_list))
            all_iocs_flat.extend(ioc_list)
        ioc_dup_flags: list[bool] = [False] * len(all_iocs_flat)
        if all_iocs_flat and self._dedup_manager is not None:
            try:
                ioc_dup_flags = self._dedup_manager.is_duplicate_ioc_batch(all_iocs_flat)
            except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
                pass

        # ISSUE-FXXX: Collect all (fp, finding_id) pairs for batch dedup flush.
        _dedup_batch: list[tuple[str, str]] = []

        for idx, f in enumerate(findings):
            rd = rust_decisions[idx]
            is_url = rd.get("is_url", False)
            url_fp = rd.get("normalized_hash", "") if is_url else ""
            fp = rd.get("normalized_hash", "") if not is_url else ""
            entropy = rd.get("entropy", 0.0)
            rust_accepted = rd.get("accepted", True)

            # If Rust already rejected, respect that decision (no stateful checks needed)
            if not rust_accepted:
                _decision = FindingQualityDecision(
                    accepted=False,
                    reason=rd.get("reason", "rejected"),
                    entropy=entropy,
                    normalized_hash=fp,
                    duplicate=rd.get("duplicate", False),
                )
                self._record_quality_rejection(f, _decision)
                results[idx] = _decision
                continue

            is_feed_source = f.source_type == "rss_atom_pipeline"
            new_iocs_for_batch: list[tuple[str, str, float]] = []
            skipped = self._run_stateful_quality_guards(
                fp=fp,
                entropy=entropy,
                url_fp=url_fp,
                is_feed_source=is_feed_source,
                finding=f,
                results=results,
                idx=idx,
                dedup_batch=_dedup_batch,
                new_iocs_for_batch=new_iocs_for_batch,
                ioc_offsets=ioc_offsets,
                has_any_ioc=has_any_ioc,
                ioc_items=ioc_items,
                ioc_dup_flags=ioc_dup_flags,
            )
            if not skipped:
                if new_iocs_for_batch and self._dedup_manager is not None:
                    try:
                        self._dedup_manager.add_ioc_batch(new_iocs_for_batch)
                    except (OSError, RuntimeError) as e:
                        logger.debug(f"[DUCKDB] add_ioc_batch failed: {e}")

        # ISSUE-FXXX: Flush all dedup writes in one batch = one LMDB transaction.
        if _dedup_batch and self._dedup_manager is not None:
            try:
                self._dedup_manager.store_persistent_dedup_batch(_dedup_batch)
            except Exception as e:  # noqa: BLE001 — best-effort; export failure; non-critical
                logger.debug(f"[DUCKDB] store_persistent_dedup_batch failed: {e}")

        return results

    def _assess_finding_quality(self, finding: CanonicalFinding) -> FindingQualityDecision:
        """
        Sprint 8W + 8AG + 8AK: Assess a single finding's quality via entropy + dedup.

        Sprint 8AK: URL-first fingerprint - if a canonical URL is present in
        provenance, use it (normalized) as the primary dedup signal, independent
        of source_type or payload position. Falls back to payload_text.

        Sprint 8AG §6.17: Persistent dedup via LMDB with hot-cache read-through.
        Lookup order: hot cache -> persistent LMDB -> store if miss.
        LMDB is the authority; hot cache is a bounded read-through cache.

        Returns FindingQualityDecision (frozen, immutable).
        Fail-open: any exception -> accept with reason="quality_check_error".

        Text mapping: URL (if present) or payload_text (if exists and non-empty), else query.
        If both are empty, falls back to query (may accept trivially).
        """
        # Lazy imports to break circular dependency with quality_assessment.py
        from .quality_assessment import (
            _compute_url_fingerprint,
            _normalize_for_quality,
            _compute_entropy,
            _compute_dedup_fingerprint,
            FindingQualityDecision,
        )
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        url_from_provenance = self._extract_url_from_provenance(finding.provenance)
        url_fingerprint = _compute_url_fingerprint(url_from_provenance) if url_from_provenance else ""
        if url_fingerprint:
            fingerprint = url_fingerprint
            entropy = 0.0
        else:
            text = finding.payload_text if finding.payload_text else finding.query
            if not text or not text.strip():
                text = finding.query
            normalized = _normalize_for_quality(text)
            entropy = _compute_entropy(normalized)
            fingerprint = _compute_dedup_fingerprint(normalized)
        _is_feed_source: bool = finding.source_type == "rss_atom_pipeline"
        logger.debug(
            "[QUALITY-GATE] assessing source_type=%s is_feed=%s url_fp=%s entropy=%.3f fp_len=%d finding_id=%s",
            finding.source_type,
            _is_feed_source,
            bool(url_fingerprint),
            entropy,
            len(fingerprint),
            finding.finding_id[:16] if finding.finding_id else "",
        )
        duplicate = self._hot_cache_lookup(fingerprint) is not None
        if duplicate:
            self._quality_state._quality_duplicate_count += 1
            reason = "persistent_duplicate" if url_fingerprint else "duplicate_detected"
            logger.debug(
                "[QUALITY-GATE] rejected=hot_cache_duplicate fp=%s finding_id=%s",
                fingerprint[:16] if fingerprint else "",
                finding.finding_id[:16] if finding.finding_id else "",
            )
            return FindingQualityDecision(
                accepted=False, reason=reason, entropy=entropy, normalized_hash=fingerprint, duplicate=True
            )
        stored_finding_id = self._lookup_persistent_dedup(fingerprint)
        if stored_finding_id is not None:
            if not _is_feed_source:
                self._add_to_hot_cache(fingerprint, stored_finding_id)
            self._quality_state._persistent_duplicate_count += 1
            reason = "persistent_duplicate" if url_fingerprint else "duplicate_detected"
            logger.debug(
                "[QUALITY-GATE] rejected=lmdb_duplicate fp=%s finding_id=%s stored_id=%s",
                fingerprint[:16] if fingerprint else "",
                finding.finding_id[:16] if finding.finding_id else "",
                stored_finding_id[:16] if stored_finding_id else "",
            )
            return FindingQualityDecision(
                accepted=False, reason=reason, entropy=entropy, normalized_hash=fingerprint, duplicate=True
            )
        if url_fingerprint:
            self._store_persistent_dedup(fingerprint, finding.finding_id)
            if not _is_feed_source:
                self._add_to_hot_cache(fingerprint, finding.finding_id)
            return FindingQualityDecision(
                accepted=True, reason=None, entropy=entropy, normalized_hash=fingerprint, duplicate=False
            )
        if len(fingerprint) < _QUALITY_MIN_ENTROPY_LEN:
            dedup_cache = self._dedup_manager.semantic_dedup_cache if self._dedup_manager else None
            if dedup_cache is not None:
                try:
                    text_for_embed = url_from_provenance or (finding.payload_text or finding.query)
                    if text_for_embed and len(text_for_embed) >= 16:
                        _semantic_thresh = 0.75 if _is_feed_source else 0.8
                        is_dup = dedup_cache.check_and_cache(text_for_embed, threshold=_semantic_thresh)
                        if is_dup:
                            self._quality_state._quality_duplicate_count += 1
                            return FindingQualityDecision(
                                accepted=False,
                                reason="semantic_duplicate",
                                entropy=entropy,
                                normalized_hash=fingerprint,
                                duplicate=True,
                            )
                except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    _logger.warning(f"Quality gate error (short_string path): {e}")
            self._store_persistent_dedup(fingerprint, finding.finding_id)
            if not _is_feed_source:
                self._add_to_hot_cache(fingerprint, finding.finding_id)
            return FindingQualityDecision(
                accepted=True, reason="short_string_skip", entropy=entropy, normalized_hash=fingerprint, duplicate=False
            )
        _effective_threshold = 0.3 if _is_feed_source else _QUALITY_ENTROPY_THRESHOLD
        if entropy < _effective_threshold:
            self._quality_state._quality_rejected_count += 1
            logger.debug(
                "[QUALITY-GATE] rejected=low_entropy threshold=%.2f entropy=%.2f finding_id=%s is_feed=%s",
                _effective_threshold,
                entropy,
                finding.finding_id[:16] if finding.finding_id else "",
                _is_feed_source,
            )
            return FindingQualityDecision(
                accepted=False,
                reason="low_entropy_rejected",
                entropy=entropy,
                normalized_hash=fingerprint,
                duplicate=False,
            )
        dedup_cache = self._dedup_manager.semantic_dedup_cache if self._dedup_manager else None
        if dedup_cache is not None:
            try:
                dedup_cache_ref = dedup_cache
                text_for_embed = url_from_provenance or (finding.payload_text or finding.query)
                if text_for_embed and len(text_for_embed) >= 16:
                    _semantic_thresh = 0.8 if _is_feed_source else 0.85
                    is_dup = dedup_cache_ref.check_and_cache(text_for_embed, threshold=_semantic_thresh)
                    if is_dup:
                        self._quality_state._quality_duplicate_count += 1
                        logger.debug(
                            "[QUALITY-GATE] rejected=semantic_duplicate finding_id=%s",
                            finding.finding_id[:16] if finding.finding_id else "",
                        )
                        return FindingQualityDecision(
                            accepted=False,
                            reason="semantic_duplicate",
                            entropy=entropy,
                            normalized_hash=fingerprint,
                            duplicate=True,
                        )
            except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                _logger.warning(f"Quality gate error (entropy path): {e}")
        self._store_persistent_dedup(fingerprint, finding.finding_id)
        if not _is_feed_source:
            self._add_to_hot_cache(fingerprint, finding.finding_id)
        return FindingQualityDecision(
            accepted=True, reason=None, entropy=entropy, normalized_hash=fingerprint, duplicate=False
        )

    # ---------------------------------------------------------------------------
    # F360M-R: Flat guard helpers — eliminate triple-nested blocks that would
    # otherwise push cognitive complexity past thresholds (was 179, target ≤25).
    # ---------------------------------------------------------------------------

    @staticmethod
    def _dedup_ioc_lists(
        raw_iocs: list[list[tuple[str, str]]],
    ) -> tuple[list[list[tuple[str, str]]], list[bool]]:
        """
        Flatten IOC dedup loop — replaces triple-nested for/if block.

        Input:  [[(val, type), ...], ...]  (one inner list per finding)
        Output: (ioc_items, has_any_ioc)   same structure as legacy
        """
        ioc_items: list[list[tuple[str, str]]] = []
        has_any_ioc: list[bool] = []
        for ioc_list in raw_iocs:
            deduped: list[tuple[str, str]] = []
            seen: set[tuple[str, str]] = set()
            for val, ioc_type in ioc_list:
                key = (val, ioc_type)
                if key not in seen:
                    seen.add(key)
                    deduped.append(key)
            ioc_items.append(deduped)
            has_any_ioc.append(bool(deduped))
        return ioc_items, has_any_ioc

    def _dedup_fp_fallback_python(self, normalized_batch: list[str]) -> list[str]:
        """
        Fallback chain for dedup fingerprint — replaces triple-nested try/except.

        Tries: rust_dedup_fingerprint → _compute_dedup_fingerprint
        """
        try:
            return [_rust_dedup_fingerprint(t) for t in normalized_batch]
        except Exception:  # noqa: BLE001 — best-effort; rust unavailable; non-critical
            return [_compute_dedup_fingerprint(t) for t in normalized_batch]

    def _compute_url_fingerprints_batch(
        self,
        url_texts: list[str],
        url_indices: list[int],
        url_fingerprints: list[str],
    ) -> None:
        """
        Batch URL fingerprint computation — replaces depth-5 if/for nested block.

        Reads from url_texts (aligned with url_indices) and writes directly to
        url_fingerprints at the corresponding indices.
        """
        if _QUALITY_GATE_BATCH_AVAILABLE and _rust_batch_url_fingerprints is not None:
            try:
                batch_urls: list[str] = _rust_batch_url_fingerprints(url_texts)
                for j, idx in enumerate(url_indices):
                    url_fingerprints[idx] = batch_urls[j]
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                for j, idx in enumerate(url_indices):
                    url_fingerprints[idx] = _compute_url_fingerprint(url_texts[j])
        else:
            for j, idx in enumerate(url_indices):
                url_fingerprints[idx] = _compute_url_fingerprint(url_texts[j])
        """
        Flatten IOC dedup loop — replaces triple-nested for/if block.

        Input:  [[(val, type), ...], ...]  (one inner list per finding)
        Output: (ioc_items, has_any_ioc)   same structure as legacy
        """
        ioc_items: list[list[tuple[str, str]]] = []
        has_any_ioc: list[bool] = []
        for ioc_list in raw_iocs:
            deduped: list[tuple[str, str]] = []
            seen: set[tuple[str, str]] = set()
            for val, ioc_type in ioc_list:
                key = (val, ioc_type)
                if key not in seen:
                    seen.add(key)
                    deduped.append(key)
            ioc_items.append(deduped)
            has_any_ioc.append(bool(deduped))
        return ioc_items, has_any_ioc

    def _dedup_fp_fallback_python(self, normalized_batch: list[str]) -> list[str]:
        """
        Fallback chain for dedup fingerprint — replaces triple-nested try/except.

        Tries: rust_dedup_fingerprint → _compute_dedup_fingerprint
        """
        try:
            return [_rust_dedup_fingerprint(t) for t in normalized_batch]
        except Exception:  # noqa: BLE001 — best-effort; rust unavailable; non-critical
            return [_compute_dedup_fingerprint(t) for t in normalized_batch]

    def _assess_finding_quality_batch(self, findings: list[CanonicalFinding]) -> list[FindingQualityDecision]:
        """
        Sprint P1-2: Batch quality gate — rayon-parallel via Rust batch_* APIs.

        P1-07: Added IOC-level dedup — extracted IOCs are checked against
        Rust MmapIocDedupStore before the finding is accepted.

        ISSUE-022: Tries assess_findings_quality_batch() Rust fast path first —
        pure-compute decisions (URL fp, normalize, entropy, dedup fp) in a single
        rayon pass. Stateful checks (hot_cache, LMDB, semantic dedup) run in Python
        after Rust returns.

        Falls back to the full per-finding loop if Rust is unavailable or fails.

        Bounded: caller should chunk at 4096 max (Rust BATCH_HARD_CAP).
        Returns list[FindingQualityDecision] in same order as findings.
        Fail-soft: any exception propagates to caller for per-row fallback.
        """
        # Lazy import to break circular dependency with quality_assessment.py
        from .quality_assessment import _HIGH_CONF_IOC_RE, FindingQualityDecision

        n = len(findings)

        # ISSUE-022: Try Rust fast path — pure-compute decisions in one rayon pass
        if _RUST_ASSESS_QUALITY_BATCH_AVAILABLE:
            _assess_batch_fn = _get_rust_assess_quality_batch()
            if _assess_batch_fn is not None:
                try:
                    # Build PyFindingInput dicts — mirror Rust PyFindingInput struct fields
                    py_findings: list[dict] = [
                        {
                            "finding_id": f.finding_id,
                            "source_type": f.source_type,
                            "provenance": f.provenance or "",
                            "payload_text": f.payload_text or None,
                            "query": f.query or "",
                        }
                        for f in findings
                    ]
                    rust_decisions: list[dict] = _assess_batch_fn(py_findings)
                    if rust_decisions is not None and len(rust_decisions) == n:
                        # Convert Rust dict output to FindingQualityDecision,
                        # then apply only the stateful Python checks.
                        return self._apply_stateful_quality_checks(findings, rust_decisions)
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass  # Fall through to legacy implementation

        # Legacy implementation — runs when Rust fast path is unavailable or failed
        results: list[FindingQualityDecision | None] = [None] * n
        ioc_items: list[list[tuple[str, str]]] = []
        has_any_ioc: list[bool] = []
        if _IOC_EXTRACT_BATCH_AVAILABLE and _get_rust_batch_ioc_extract() is not None:
            texts_for_ioc = []
            for f in findings:
                pt = f.payload_text if f.payload_text else f.query or ""
                texts_for_ioc.append(pt[:5000] if pt else "")
            try:
                raw_iocs = _get_rust_batch_ioc_extract()(texts_for_ioc)
                ioc_items, has_any_ioc = self._dedup_ioc_lists(raw_iocs)
            except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
                ioc_items = [[] for _ in findings]
                has_any_ioc = [False] * len(findings)
        else:
            ioc_items = [[] for _ in findings]
            has_any_ioc = [False] * len(findings)
        all_iocs_flat: list[tuple[str, str]] = []
        ioc_offsets: list[int] = [0]
        for ioc_list in ioc_items:
            ioc_offsets.append(ioc_offsets[-1] + len(ioc_list))
            all_iocs_flat.extend(ioc_list)
        ioc_dup_flags: list[bool] = [False] * len(all_iocs_flat)
        if all_iocs_flat and self._dedup_manager is not None:
            try:
                ioc_dup_flags = self._dedup_manager.is_duplicate_ioc_batch(all_iocs_flat)
            except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
                pass
        url_fingerprints: list[str] = [""] * n
        entropies: list[float] = [0.0] * n
        fingerprints: list[str] = [""] * n
        url_indices: list[int] = []
        payload_indices: list[int] = []
        texts: list[str] = []
        for idx, f in enumerate(findings):
            url = self._extract_url_from_provenance(f.provenance) if f.provenance else ""
            if url:
                url_fingerprints[idx] = url
                url_indices.append(idx)
                texts.append("")
            else:
                payload_text = f.payload_text if f.payload_text else f.query
                if not (payload_text and payload_text.strip()):
                    payload_text = f.query
                texts.append(payload_text)
                payload_indices.append(idx)
        if url_indices:
            url_texts = [url_fingerprints[i] for i in url_indices]
            self._compute_url_fingerprints_batch(url_texts, url_indices, url_fingerprints)
        if payload_indices:
            payload_texts = [texts[i] for i in payload_indices]
            if _QUALITY_GATE_BATCH_AVAILABLE and _rust_batch_normalize_quality_text is not None:
                try:
                    normalized_batch: list[str] = _rust_batch_normalize_quality_text(payload_texts)
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    normalized_batch = [_normalize_for_quality(t) for t in payload_texts]
            else:
                normalized_batch = [_normalize_for_quality(t) for t in payload_texts]
            if _QUALITY_GATE_BATCH_AVAILABLE and _rust_batch_entropy is not None:
                try:
                    entropies_batch: list[float] = _rust_batch_entropy(normalized_batch)
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    entropies_batch = [_compute_entropy(t) for t in normalized_batch]
            else:
                entropies_batch = [_compute_entropy(t) for t in normalized_batch]
            if _QUALITY_GATE_BATCH_AVAILABLE and _rust_batch_dedup_fingerprints is not None:
                try:
                    fps_batch = _rust_batch_dedup_fingerprints(normalized_batch)
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    fps_batch = self._dedup_fp_fallback_python(normalized_batch)
            else:
                fps_batch = [_compute_dedup_fingerprint(t) for t in normalized_batch]
            for j, idx in enumerate(payload_indices):
                entropies[idx] = entropies_batch[j]
                fingerprints[idx] = fps_batch[j]
        # ISSUE-FXXX: Collect all (fp, finding_id) pairs for batch dedup flush.
        # Previously each accepted finding wrote a single-item LMDB transaction
        # (putmulti of 1 pair = txn.begin + cursor.putmulti + txn.commit).
        # Batch: one txn.begin + N×cursor.putmulti + txn.commit per chunk.
        _dedup_batch: list[tuple[str, str]] = []
        # ISSUE-FXXX: new_iocs_for_batch is populated by _run_stateful_quality_guards
        # (guard 7, only when Rust batch path is used) and consumed after the loop
        # to update the dedup manager's IOC store. This avoids redundant IOC
        # buffering — the parallel IOC path in async_ingest_findings_batch uses
        # separate Rust batch extraction and is a different code path.
        new_iocs_for_batch: list[tuple[str, str, float]] = []

        # F350M-R: Extracted per-finding logic to flatten complexity (was ~90 nested).
        # Each guard applies one rejection/accept path and returns True to skip.
        for idx, f in enumerate(findings):
            url_fp = url_fingerprints[idx]
            fp = fingerprints[idx]
            entropy = entropies[idx]
            is_feed_source = f.source_type == "rss_atom_pipeline"
            new_iocs_for_batch: list[tuple[str, str, float]] = []
            self._run_stateful_quality_guards(
                fp=fp,
                entropy=entropy,
                url_fp=url_fp,
                is_feed_source=is_feed_source,
                finding=f,
                results=results,
                idx=idx,
                dedup_batch=_dedup_batch,
                new_iocs_for_batch=new_iocs_for_batch,
                ioc_offsets=ioc_offsets,
                has_any_ioc=has_any_ioc,
                ioc_items=ioc_items,
                ioc_dup_flags=ioc_dup_flags,
            )
            if new_iocs_for_batch and self._dedup_manager is not None:
                try:
                    self._dedup_manager.add_ioc_batch(new_iocs_for_batch)
                except (OSError, RuntimeError) as e:
                    logger.debug(f"[DUCKDB] add_ioc_batch failed: {e}")

        # ISSUE-FXXX: Flush all dedup writes in one batch = one LMDB transaction.
        if _dedup_batch and self._dedup_manager is not None:
            try:
                self._dedup_manager.store_persistent_dedup_batch(_dedup_batch)
            except Exception as e:  # noqa: BLE001 — best-effort; export failure; non-critical
                logger.debug(f"[DUCKDB] store_persistent_dedup_batch failed: {e}")

        assert None not in results, "_assess_finding_quality_batch: 1:1 invariant violated"
        return results

    # F360M-R: Helper methods extracted from async_ingest_findings_batch to reduce nesting depth.
    # Each method follows early-exit pattern to minimize cyclomatic complexity.

    def _get_finding_decision(
        self,
        finding: CanonicalFinding,
        i_offset: int,
        decisions: list[FindingQualityDecision],
        batch_rust_ok: bool,
    ) -> FindingQualityDecision | None:
        """
        Extract quality decision for a single finding with early-exit on error.

        Returns None when assessment throws (fail-open signal), otherwise decision.
        Reduces nesting from 4 levels to 2 in the per-finding loop.
        """
        try:
            if not batch_rust_ok:
                return self._quality_gate._assess_finding_quality(finding)
            if i_offset >= len(decisions):
                return FindingQualityDecision(
                    accepted=False,
                    reason="batch_incomplete",
                    entropy=0.0,
                    normalized_hash=None,
                    duplicate=False,
                )
            return decisions[i_offset]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return None

    def _apply_zero_attribution(
        self, finding: CanonicalFinding, temporal_anonymizer: Any
    ) -> CanonicalFinding:
        """Apply zero attribution timestamp anonymization if available."""
        try:
            finding.timestamp = temporal_anonymizer.anonymize_timestamp(finding.timestamp)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        return finding

    async def _buffer_iocs_from_findings(
        self, findings: list[CanonicalFinding], truth_graph: Any
    ) -> None:
        """
        F360M-R: Parallel IOC buffering from accepted findings.

        Extracts IOCs via Rust batch, deduplicates, and buffers to truth graph
        in parallel chunks. Reduces nesting from 6 to 2 levels.
        """
        if not (_IOC_EXTRACT_BATCH_AVAILABLE and _get_rust_batch_ioc_extract()):
            return
        try:
            ioc_texts = [f.payload_text or f.query or "" for f in findings]
            ioc_results: list[list[tuple[str, str]]] = await asyncio.to_thread(
                _get_rust_batch_ioc_extract(), ioc_texts
            )
            if not ioc_results:
                return
            buffer_ioc = getattr(truth_graph, "buffer_ioc", None)
            flush_buffers = getattr(truth_graph, "flush_buffers", None)
            if not (callable(buffer_ioc) and callable(flush_buffers)):
                return
            # Collect and dedupe IOCs
            all_iocs: list[tuple[str, str, float]] = []
            seen_iocs: set[tuple[str, str]] = set()
            for finding_idx, _ in enumerate(findings):
                for ioc_value, ioc_type in ioc_results[finding_idx]:
                    ioc_key = (ioc_type, ioc_value)
                    if ioc_key not in seen_iocs:
                        seen_iocs.add(ioc_key)
                        all_iocs.append((ioc_type, ioc_value, 1.0))
            # Chunk and buffer in parallel
            ioc_chunks: list[list[tuple[str, str, float]]] = [
                all_iocs[i : i + _IOC_CHUNK] for i in range(0, len(all_iocs), _IOC_CHUNK)
            ]

            async def _buffer_chunk(chunk: list[tuple[str, str, float]]) -> None:
                for ioc_type, ioc_value, score in chunk:
                    await buffer_ioc(ioc_type, ioc_value, score)

            if ioc_chunks:
                await parallel(
                    [_buffer_chunk(chunk) for chunk in ioc_chunks],
                    taskgroup=True,
                    policy="collect",
                    ctx="duckdb_store:ioc_buffer",
                    logger_instance=None,
                )
            flush_buffers()
        except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
            pass

    async def async_ingest_finding(self, finding: CanonicalFinding) -> FindingQualityDecision | ActivationResult:
        """
        Sprint 8W: Quality-gated single-finding ingest.

        Layer ABOVE async_record_canonical_finding - applies quality gate first,
        then delegates to legacy storage path on accept.

        Quality gate is CPU-only, deterministic, and cheap.
        Fail-open: if quality helpers raise, the finding is stored via legacy path.

        Returns FindingQualityDecision when rejected/duplicate.
        Returns ActivationResult on accept or fail-open.
        """
        try:
            decision = self._assess_finding_quality(finding)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            self._quality_state._quality_fail_open_count += 1
            result = await self.async_record_canonical_finding(finding)
            return result
        if not decision.accepted:
            self._record_quality_rejection(finding, decision)
            return decision
        result = await self.async_record_canonical_finding(finding)
        if isinstance(result, dict):
            lmdb_ok = result.get("lmdb_success", False)
        else:
            lmdb_ok = bool(result.lmdb_success)
        if lmdb_ok:
            self._quality_state._accepted_count += 1
        return result

    @_otel_instrumented("duckdb.ingest_batch", component="storage")
    async def async_ingest_findings_batch(
        self, findings: list[CanonicalFinding]
    ) -> list[FindingQualityDecision | ActivationResult]:
        """
        Sprint 8W: Quality-gated batch ingest.

        Layer ABOVE async_record_canonical_findings_batch - applies quality gate to each
        finding, then delegates acceptable ones to legacy batch storage.

        Quality gate is CPU-only, deterministic, and cheap.
        Fail-open: if quality helpers raise for any finding, that finding is stored
        via legacy path.

        Returns list with len(results) == len(findings) - 1:1 invariant.
        Each entry is FindingQualityDecision (rejected/duplicate) or ActivationResult (accepted).
        """
        if not findings:
            await self.async_initialize_schema()
            return []
        self.ensure_connected()
        n = len(findings)
        logger.debug(
            "[INGEST-BATCH] entry len(findings)=%d  quality_state=_accepted:%d _rejected:%d _dup:%d _persist_dup:%d",
            n,
            self._quality_state._accepted_count,
            self._quality_state._quality_rejected_count,
            self._quality_state._quality_duplicate_count,
            self._quality_state._persistent_duplicate_count,
        )
        results: list[FindingQualityDecision | ActivationResult | None] = [None] * n
        _accepted_findings: list[CanonicalFinding] = []
        _accepted_indices: list[int] = []
        _chunk_size = self._duckdb_settings.get("chunk_size", 1024)
        CHUNK_SIZE: int = _chunk_size
        self._last_ingest_ts = _time.monotonic()
        self._batch_start_ts = self._last_ingest_ts
        # ISSUE-021: entry clear — both lists cleared together so they stay in sync.
        # The three intermediate flush sites below each call .clear() on both lists
        # together, preserving the invariant pair.  _do_sync_close also clears both.
        self._pending_accepted_findings.clear()
        self._pending_accepted_indices.clear()
        # F350M-R: Phase 1 — launch ALL quality assessments CONCURRENTLY via asyncio.gather.
        pending_tasks: list[tuple[list[int], asyncio.Task[list[ActivationResult]]]] = []
        loop = asyncio.get_running_loop()
        quality_tasks: list[asyncio.Future[list[FindingQualityDecision]]] = []
        chunk_boundaries: list[tuple[int, int, list[CanonicalFinding]]] = []
        for chunk_start in range(0, n, CHUNK_SIZE):
            chunk_end = min(chunk_start + CHUNK_SIZE, n)
            chunk_findings = findings[chunk_start:chunk_end]
            chunk_boundaries.append((chunk_start, chunk_end, chunk_findings))
            task = loop.run_in_executor(
                self._shared_executor,
                lambda cf=chunk_findings: self._assess_finding_quality_batch(cf),
            )
            quality_tasks.append(task)

        # Wait for ALL quality assessments concurrently — this is the main speedup
        _result = await parallel(quality_tasks, taskgroup=True, policy='collect', ctx='duckdb_store:quality_gate', logger_instance=None)
        quality_results: tuple[list[FindingQualityDecision] | Exception, ...] = tuple(_result.ok)

        # Phase 2 — process decisions sequentially (fast Python, no I/O)
        self._last_ingest_ts = _time.monotonic()
        for (chunk_start, chunk_end, chunk_findings), quality_result in zip(
            chunk_boundaries, quality_results, strict=True
        ):
            chunk_decisions: list[FindingQualityDecision]
            if isinstance(quality_result, Exception):
                self._quality_state._quality_fail_open_count += 1
                chunk_decisions = []
                _batch_rust_ok = False
            else:
                chunk_decisions = quality_result
                _batch_rust_ok = True

            fail_open_chunk_findings: list[CanonicalFinding] = []
            fail_open_chunk_indices: list[int] = []
            chunk_accepted_findings: list[CanonicalFinding] = []
            chunk_accepted_indices: list[int] = []

            # F350M-R: hoist ZERO_ATTRIBUTION check outside the per-finding loop
            _do_zero_attribution = os.getenv("HLEDAC_ENABLE_ZERO_ATTRIBUTION") == "1"
            _temporal_anonymizer: Any = None
            if _do_zero_attribution and not hasattr(self, "_temporal_anonymizer"):
                try:
                    from hledac.universal.security.temporal_anonymizer import TemporalAnonymizer
                    self._temporal_anonymizer = TemporalAnonymizer()
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    _do_zero_attribution = False
            # B3-FIX: Vectorized decision extraction — eliminates per-iteration
            # branching and decision lookup inside the loop body.
            # Fallback path (_assess_finding_quality per finding) stays sequential
            # because it is fail-open and non-vectorizable (stateful + exception-driven).
            if _batch_rust_ok:
                decisions: list[FindingQualityDecision] = chunk_decisions
            else:
                decisions = []

            for i_offset, f in enumerate(chunk_findings):
                i = chunk_start + i_offset
                # F360M-R: early-exit decision extraction — reduces nesting from 4 to 2
                decision = self._get_finding_decision(
                    f, i_offset, decisions, _batch_rust_ok
                )
                if decision is None:
                    # Fail-open: assessment threw, record and skip
                    fail_open_chunk_findings.append(f)
                    fail_open_chunk_indices.append(i)
                    continue
                if not decision.accepted:
                    self._record_quality_rejection(f, decision)
                    results[i] = decision
                    continue
                # Accepted path — zero attribution if enabled
                if _do_zero_attribution:
                    f = self._apply_zero_attribution(f, _temporal_anonymizer)
                chunk_accepted_findings.append(f)
                chunk_accepted_indices.append(i)

            if fail_open_chunk_findings:
                batch_results = await self._record_fail_open_batch(
                    fail_open_chunk_findings, results, fail_open_chunk_indices
                )
                for idx, br in zip(fail_open_chunk_indices, batch_results, strict=False):
                    if br is not None:
                        results[idx] = br

            self._pending_accepted_findings.extend(chunk_accepted_findings)
            self._pending_accepted_indices.extend(chunk_accepted_indices)
            _elapsed = _time.monotonic() - self._last_ingest_ts
            # ISSUE-021: both lists are an invariant pair — flush must check both.
            # If lengths diverge due to a bug, force flush to prevent unbounded divergence.
            _findings_len = len(self._pending_accepted_findings)
            _indices_len = len(self._pending_accepted_indices)
            if _findings_len != _indices_len:
                _should_flush = True  # force flush to break any divergence spiral
            else:
                _should_flush = _findings_len >= self._min_flush or _elapsed >= self._max_flush_interval
            if _should_flush and _findings_len > 0:
                _flush_indices = list(self._pending_accepted_indices)
                _flush_findings = list(self._pending_accepted_findings)
                _flush_task = safe_create_task(
                    self.async_record_canonical_findings_batch_arrow(_flush_findings),
                    name="duckdb:record_arrow_tb",
                )
                pending_tasks.append((_flush_indices, _flush_task))
                self._pending_accepted_findings.clear()
                self._pending_accepted_indices.clear()
                self._last_ingest_ts = _time.monotonic()
        # ISSUE-021: final flush — guard against length divergence between findings and indices.
        _fp_findings_len = len(self._pending_accepted_findings)
        _fp_indices_len = len(self._pending_accepted_indices)
        if _fp_findings_len != _fp_indices_len:
            # Diverged — force flush with whatever is present to stop the spiral.
            _logger.warning(
                "[A8HIGH] pending list length divergence at final flush: "
                "findings=%d indices=%d — force flushing",
                _fp_findings_len,
                _fp_indices_len,
            )
        if _fp_findings_len > 0:
            _flush_indices = list(self._pending_accepted_indices)
            _flush_findings = list(self._pending_accepted_findings)
            _flush_task = safe_create_task(
                self.async_record_canonical_findings_batch_arrow(_flush_findings), name="duckdb:record_arrow_tb_final"
            )
            pending_tasks.append((_flush_indices, _flush_task))
            self._pending_accepted_findings.clear()
            self._pending_accepted_indices.clear()
        all_accepted_findings: list[CanonicalFinding] = []
        if pending_tasks:
            tasks_only = [t for _, t in pending_tasks]
            _result = await parallel(tasks_only, taskgroup=True, policy='collect', ctx='duckdb_store:storage_pipeline', logger_instance=None)
            storage_results_all: tuple[list[ActivationResult] | Exception, ...] = tuple(_result.ok)
            for (chunk_indices, task), task_result in zip(pending_tasks, storage_results_all, strict=True):
                if isinstance(task_result, Exception):
                    logger.warning("[A8HIGH] storage task failed: %s", task_result)
                    for idx in chunk_indices:
                        if results[idx] is None:
                            results[idx] = ActivationResult(
                                finding_id=str(findings[idx].finding_id),
                                lmdb_success=False,
                                duckdb_success=None,
                                lmdb_key=f"finding:{findings[idx].finding_id}",
                                desync=False,
                                error="pipeline_storage_failed",
                                accepted=False,
                            )
                    continue
                for idx, sr in zip(chunk_indices, task_result, strict=False):
                    results[idx] = sr
                    if getattr(sr, "accepted", False):
                        all_accepted_findings.append(findings[idx])
        truth_graph = self._ensure_graph_attachment().get_truth_write_graph() if all_accepted_findings else None
        if truth_graph is not None:
            await self._buffer_iocs_from_findings(all_accepted_findings, truth_graph)
            self._schedule_graph_update(all_accepted_findings)
        assert None not in results, "Internal error: 1:1 invariant violated"
        accepted_total = sum(
            (
                1
                for r in results
                if getattr(r, "accepted", None) is True or (isinstance(r, dict) and r.get("accepted") is True)
            )
        )
        logger.debug(
            "[INGEST-BATCH] exit len(results)=%d  accepted=%d  _accepted_count=%d",
            len(results),
            accepted_total,
            self._quality_state._accepted_count,
        )
        if accepted_total > 0:
            logger.info("[DuckDB] written %d records (sprint F265-P1-2 canonical write verification)", accepted_total)
        try:
            if self._wal_manager is not None:
                compact_result = self._wal_manager.compact()
                if compact_result is not None:
                    logger.debug(
                        "[WAL] compact pages_reclaimed=%d pages_free=%d",
                        compact_result.get("pages_reclaimed", -1),
                        compact_result.get("pages_free", -1),
                    )
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        try:
            _ingest_end = _time.monotonic()
            _batch_start = getattr(self, "_batch_start_ts", self._last_ingest_ts)
            _ingest_latency_ms = (_ingest_end - _batch_start) * 1000.0
            from hledac.universal.metrics_registry import get_metrics_registry

            get_metrics_registry().set_gauge("duckdb_ingest_latency_ms", _ingest_latency_ms)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            pass
        # RES-03: Track write ops for auto-maintenance
        self._write_op_counter += accepted_total
        # RES-03: Trigger automatic VACUUM/CHECKPOINT if thresholds reached
        await self._maybe_auto_maintenance()
        return results

    def _envelope_to_payload(self, envelope: FindingEnvelope) -> str | None:
        """
        Sprint F202A §2: Serialize FindingEnvelope to payload_text string.

        Fail-soft: returns None if serialization fails or size exceeds limit.
        Caller degrades to plain finding when None is returned.
        """
        from hledac.universal.knowledge.finding_envelope import FindingEnvelope, envelope_size_guard, serialize_envelope

        if not isinstance(envelope, FindingEnvelope):
            return None
        if not envelope_size_guard(envelope):
            return None
        return serialize_envelope(envelope)

    def _payload_to_envelope(self, payload_text: str | None) -> FindingEnvelope | None:
        """
        Sprint F202A §2: Deserialize FindingEnvelope from payload_text string.

        Fail-soft: returns None if payload_text is None/empty, parsing fails,
        or required audit_reason field is missing.
        """
        from hledac.universal.knowledge.finding_envelope import deserialize_envelope

        return deserialize_envelope(payload_text)

    def _store_envelope_payload(self, finding_id: str, payload_text: str) -> bool:
        """
        Sprint F202A §2: Update LMDB WAL entry with envelope payload_text.

        Called after initial ingest when envelope is attached post-hoc.
        Returns True if LMDB update succeeded.
        """
        try:
            from hledac.universal.tools.lmdb_kv import LMDBKVStore

            if not hasattr(self, "_wal_lmdb"):
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    return False
                self._wal_lmdb = LMDBKVStore(path=str(_wal_root / "shadow_wal.lmdb"))
            key = f"finding:{finding_id}"
            existing = self._wal_lmdb.get(key)
            if existing is None:
                return False
            if isinstance(existing, dict):
                existing["payload_text"] = payload_text
            else:
                return False
            # SEC-01: Scrub secrets from serialized envelope payload before WAL storage.
            scrubbed_payload = existing.get("payload_text")
            if scrubbed_payload:
                try:
                    from hledac.universal.security.secrets_scrubber import scrub_secrets

                    scrubbed_payload = scrub_secrets(scrubbed_payload)
                except Exception:  # noqa: BLE001 — fail-safe: use raw if scrubbing fails
                    scrubbed_payload = existing.get("payload_text")
            existing["payload_text"] = scrubbed_payload
            return self._wal_lmdb.put(key, existing)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return False

    async def async_ingest_findings_with_envelope(
        self, findings: list[CanonicalFinding], envelopes: list[FindingEnvelope]
    ) -> list[ActivationResult | FindingQualityDecision]:
        """
        Sprint F202A §2: Ingest findings WITH pre-built evidence envelopes.

        Envelope is serialized and stored in payload_text field of LMDB WAL.
        Size guard: oversized envelope -> degraded to plain finding (no crash).
        Returns same 1:1 result list as async_ingest_findings_batch.

        Args:
            findings:  List of CanonicalFinding instances
            envelopes: List of FindingEnvelope instances (same length as findings)
        """
        from hledac.universal.knowledge.finding_envelope import FindingEnvelope

        if not findings:
            return []
        if len(envelopes) != len(findings):
            return await self.async_ingest_findings_batch(findings)
        payload_overrides: dict[int, str | None] = {}
        for i, env in enumerate(envelopes):
            if not isinstance(env, FindingEnvelope):
                payload_overrides[i] = None
                continue
            from hledac.universal.knowledge.finding_envelope import envelope_size_guard

            if not envelope_size_guard(env):
                payload_overrides[i] = None
            else:
                from hledac.universal.knowledge.finding_envelope import serialize_envelope

                payload_overrides[i] = serialize_envelope(env)
        results = await self.async_ingest_findings_batch(findings)
        for i, override in payload_overrides.items():
            if override is None:
                continue
            if results[i].get("lmdb_success") if isinstance(results[i], dict) else False:
                fid = results[i].get("finding_id", "")
                if fid:
                    self._store_envelope_payload(fid, override)
        return results

    def _sync_read_envelope(self, finding_id: str) -> FindingEnvelope | None:
        """
        Sprint F202A §3: Read and deserialize envelope from LMDB WAL entry.

        Returns None if finding doesn't exist or has no valid envelope.
        Fail-soft: does not raise.
        """
        try:
            from hledac.universal.tools.lmdb_kv import LMDBKVStore

            if not hasattr(self, "_wal_lmdb"):
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    return None
                self._wal_lmdb = LMDBKVStore(path=str(_wal_root / "shadow_wal.lmdb"))
            key = f"finding:{finding_id}"
            entry = self._wal_lmdb.get(key)
            if entry is None or not isinstance(entry, dict):
                return None
            payload_text = entry.get("payload_text")
            return self._payload_to_envelope(payload_text)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return None

    async def async_get_findings_with_envelope(self, limit: int = 20) -> list[dict]:
        """
        Sprint F202A §3: Read recent findings with deserialized envelopes.

        Returns list of dicts with envelope fields attached:
          {finding_id, query, source_type, confidence, ts, provenance,
           payload_text, envelope: FindingEnvelope | None}
        Fail-soft: any finding without valid envelope has envelope=None.
        """
        raw_findings = await self.async_query_recent_findings(limit=limit)
        result = []
        for f in raw_findings:
            fid = f.get("id", f.get("finding_id", ""))
            env = self._sync_read_envelope(fid) if fid else None
            item = dict(f)
            item["envelope"] = env
            result.append(item)
        return result

    def _sync_record_canonical_findings_batch_arrow(self, findings: list[CanonicalFinding]) -> tuple[int, str | None]:
        """
        Sprint P0-4: Arrow zero-copy bulk insert for CanonicalFinding list.

        MUST be called on the worker thread (thread-affine connection).
        Returns (inserted_count, error_type):
          - (n, None) on success where n = number of rows in input table
          - (0, error_type) on any failure, where error_type is one of:
              "pyarrow_not_installed" - pyarrow import failed
              "table_build_failed"    - pa.Table.from_arrays failed
              "duckdb_insert_failed" - QueryExecutor.insert_findings_bulk_arrow failed

        Distinct from the legacy `_canonical_findings_batch_to_activation_results`
        in three ways:
          1. Builds a single pyarrow.Table with columnar zero-copy arrays.
          2. Calls QueryExecutor.insert_findings_bulk_arrow (register + INSERT...SELECT).
          3. Does NOT touch LMDB WAL - caller is responsible for that half (or falls
             back to the legacy path which does both). This split keeps the Arrow
             path optional and side-effect-free at the WAL layer.

        Fail-soft: any error returns (0, error_type) and the async wrapper falls back
        to legacy. The error_type is used for typed telemetry.
        """
        if not findings:
            return (0, None)
        if not _check_pyarrow_available():
            return (0, "pyarrow_not_installed")

        # F350M-R: Enrich findings with claims before Arrow build.
        # Claims extraction is best-effort; if enrichment fails we continue with null claims_json.
        findings_dicts = self._enrich_canonical_findings_for_arrow(findings) if self._claims_enabled else None
        if findings_dicts and any(d.get("claims_json") for d in findings_dicts):
            _logger.debug(
                f"[F350M-R Arrow] Claims enriched for "
                f"{sum(1 for d in findings_dicts if d.get('claims_json'))}/{len(findings_dicts)} findings"
            )

        # --- Sequential dispatch: best-available Arrow path ---
        # Path 1: Rust zero-copy columns (fastest; skips dict roundtrip).
        # Skip if enrichment produced claims_json (Rust path doesn't support it).
        _has_claims = findings_dicts is not None and any(d.get("claims_json") for d in findings_dicts)
        if not _has_claims and _RUST_RECORD_BATCH_COLS_AVAILABLE and _rust_record_batch_cols_func is not None:
            _result = self._arrow_insert_rust_cols(findings)
            if _result[0] > 0 or _result[1] is None:
                return _result

        # Path 2: Rust dict → IPC (build_arrow_batch_from_findings).
        if _RUST_ARROW_AVAILABLE and _rust_arrow_func is not None:
            _result = self._arrow_insert_rust_dict(findings, findings_dicts)
            if _result[0] > 0 or _result[1] is None:
                return _result

        # Path 3: Pure Python pa.RecordBatch.from_arrays (universal fallback).
        return self._arrow_insert_python(findings, findings_dicts)

    # ------------------------------------------------------------------
    # F360M-R: Extracted Arrow insert paths — one per concern, no nesting.
    # Each helper returns (count, error_type) matching the parent contract.
    # ------------------------------------------------------------------

    def _arrow_insert_rust_cols(self, findings: list[CanonicalFinding]) -> tuple[int, str | None]:
        """
        Path 1: Rust zero-copy column-path via build_record_batch_from_structs.

        Builds IPC bytes by passing 7 pre-separated Python list columns to Rust,
        which iterates them via PyO3 Bound API (zero GIL overhead per item).
        Returns (count, None) on success, (0, None) on empty batch,
        (0, error_type) on failure.
        """
        import io as _io
        import pyarrow as _pa

        ids_list = [f.finding_id for f in findings]
        queries_list = [f.query for f in findings]
        src_types_list = [f.source_type for f in findings]
        conf_list = [f.confidence for f in findings]
        ts_list = [f.ts for f in findings]
        prov_list = [_provenance_to_arrow_native(f.provenance) for f in findings]
        # SEC-01: scrub payload_text before passing to Rust Arrow builder
        payload_list: list[str] = []
        for f in findings:
            raw = f.payload_text or ""
            if raw:
                try:
                    from hledac.universal.security.secrets_scrubber import scrub_secrets
                    payload_list.append(scrub_secrets(raw))
                except Exception:  # noqa: BLE001 — fail-safe
                    payload_list.append(raw)
            else:
                payload_list.append("")

        try:
            ipc_bytes = _rust_record_batch_cols_func(
                ids_list, queries_list, src_types_list, conf_list, ts_list, prov_list, payload_list
            )
        except Exception as _e:  # noqa: BLE001 — best-effort; non-critical fallback path
            _logger.debug("[Arrow-Rust] record_batch_cols failed: %s", _e)
            return (0, None)  # fall through to next path

        if not ipc_bytes or len(ipc_bytes) <= 8:
            return (0, None)  # empty batch — success, zero records

        reader = _pa.ipc.open_stream(_io.BytesIO(ipc_bytes))
        try:
            record_batch = reader.read_next_batch()
        except StopIteration:
            return (0, None)  # empty schema-only batch — treat as success

        duckdb_count, duckdb_err = self._qe().insert_findings_bulk_arrow(record_batch)
        if duckdb_err is not None:
            return (0, "duckdb_insert_failed")
        return (duckdb_count, None)

    def _arrow_insert_rust_dict(
        self, findings: list[CanonicalFinding], findings_dicts: list[dict] | None
    ) -> tuple[int, str | None]:
        """
        Path 2: Rust dict → IPC via build_arrow_batch_from_findings.

        Reuses pre-enriched findings_dicts if available; otherwise builds
        plain dicts with null claims_json. Returns (count, None) on success,
        (0, None) on empty batch, (0, error_type) on failure.
        """
        import io as _io
        import pyarrow as _pa

        if findings_dicts is None:
            # SEC-01: scrub payload_text before building dicts for Rust Arrow path
            try:
                from hledac.universal.security.secrets_scrubber import scrub_secrets
                _scrub = scrub_secrets
            except Exception:  # noqa: BLE001 — fail-safe
                _scrub = lambda t: t  # type: ignore[assignment,misc]

            def _scrub_payload(raw: str | None) -> str:
                if not raw:
                    return ""
                try:
                    return _scrub(raw)
                except Exception:  # noqa: BLE001 — fail-safe
                    return raw

            findings_dicts = [
                {
                    "id": f.finding_id,
                    "query": f.query,
                    "source_type": f.source_type,
                    "confidence": f.confidence,
                    "ts": f.ts,
                    "provenance_json": _provenance_to_arrow_native(f.provenance),
                    "payload_text": _scrub_payload(f.payload_text),
                    "claims_json": None,
                }
                for f in findings
            ]

        try:
            ipc_bytes = _rust_arrow_func(findings_dicts)
        except Exception as _e:  # noqa: BLE001 — best-effort; non-critical fallback path
            _logger.debug("[Arrow-Rust] build_arrow_batch_from_findings failed: %s", _e)
            return (0, None)  # fall through to next path

        if not ipc_bytes or len(ipc_bytes) <= 8:
            return (0, None)  # empty batch — success, zero records

        reader = _pa.ipc.open_stream(_io.BytesIO(ipc_bytes))
        try:
            record_batch = reader.read_next_batch()
        except StopIteration:
            return (0, None)  # empty schema-only batch — treat as success

        duckdb_count, duckdb_err = self._qe().insert_findings_bulk_arrow(record_batch)
        if duckdb_err is not None:
            return (0, "duckdb_insert_failed")
        return (duckdb_count, None)

    def _arrow_insert_python(
        self, findings: list[CanonicalFinding], findings_dicts: list[dict] | None
    ) -> tuple[int, str | None]:
        """
        Path 3: Pure Python pa.RecordBatch.from_arrays — universal fallback.

        Builds an 8-column RecordBatch (id, query, source_type, confidence,
        ts, provenance_json, payload_text, claims_json) entirely in Python.
        Returns (count, None) on success, (0, error_type) on failure.
        """
        import logging as _logging
        import pyarrow as _pa

        # SEC-01: lazy import scrubber
        try:
            from hledac.universal.security.secrets_scrubber import scrub_secrets
            _scrub = scrub_secrets
        except Exception:  # noqa: BLE001 — fail-safe
            _scrub = lambda t: t  # type: ignore[assignment,misc]

        def _scrub_payload(raw: str | None) -> str:
            if not raw:
                return ""
            try:
                return _scrub(raw)
            except Exception:  # noqa: BLE001 — fail-safe
                return raw

        try:
            if findings_dicts is not None:
                # Enriched path: claims_json already populated where applicable
                provenance_arr = _pa.array([d["provenance_json"] for d in findings_dicts], type=_pa.string())
                id_arr = _pa.array([d["id"] for d in findings_dicts], type=_pa.string())
                query_arr = _pa.array([d["query"] for d in findings_dicts], type=_pa.string())
                src_arr = _pa.array([d["source_type"] for d in findings_dicts], type=_pa.string())
                conf_arr = _pa.array([d["confidence"] for d in findings_dicts], type=_pa.float64())
                ts_arr = _pa.array([d["ts"] for d in findings_dicts], type=_pa.float64())
                payload_arr = _pa.array([_scrub_payload(d.get("payload_text")) for d in findings_dicts], type=_pa.string())
                claims_arr = _pa.array([d.get("claims_json") for d in findings_dicts], type=_pa.string())
            else:
                # Non-enriched path: null claims for all, scrubbed payload_text
                provenance_arr = _pa.array(
                    [_provenance_to_arrow_native(f.provenance) for f in findings], type=_pa.string()
                )
                id_arr = _pa.array([f.finding_id for f in findings], type=_pa.string())
                query_arr = _pa.array([f.query for f in findings], type=_pa.string())
                src_arr = _pa.array([f.source_type for f in findings], type=_pa.string())
                conf_arr = _pa.array([f.confidence for f in findings], type=_pa.float64())
                ts_arr = _pa.array([f.ts for f in findings], type=_pa.float64())
                payload_arr = _pa.array([_scrub_payload(f.payload_text) for f in findings], type=_pa.string())
                claims_arr = _pa.array([None] * len(findings), type=_pa.string())

            record_batch = _pa.RecordBatch.from_arrays(
                [id_arr, query_arr, src_arr, conf_arr, ts_arr, provenance_arr, payload_arr, claims_arr],
                names=["id", "query", "source_type", "confidence", "ts", "provenance_json", "payload_text", "claims_json"],
            )
        except Exception as _e:  # noqa: BLE001 — best-effort; DB build failure
            _logging.getLogger(__name__).error(
                "[P0-4 Arrow] RecordBatch build failed: %s: %s", type(_e).__name__, _e
            )
            return (0, "table_build_failed")

        duckdb_count, duckdb_err = self._qe().insert_findings_bulk_arrow(record_batch)
        if duckdb_err is not None:
            return (0, "duckdb_insert_failed")
        return (duckdb_count, None)

    def _wal_put_many_sync(self, findings: list[CanonicalFinding]) -> bool:
        """
        Sprint P1-2: WAL-only sync helper - DuckDB Single-Writer Variant 2.

        Runs on _wal_executor. LMDB WAL is pure I/O so executor occupancy is brief.
        Caller is responsible for DuckDB step (separate executor, sequential invariant).

        Returns True if WAL succeeded for all findings.
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        try:
            if not hasattr(self, "_wal_manager") or self._wal_manager is None:
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    _logger.warning("[P1-2 WAL] no wal_root")
                    return False
                self._wal_manager = DuckDBWALManager(wal_root=_wal_root)
                self._wal_manager.initialize()
            # B2-FIX: pre-allocate items list — avoids O(n) list reallocation
            # on each append() call when findings grows past CPython's 4× over-allocation.
            n = len(findings)
            items: list[tuple[str, dict]] = [None] * n  # type: ignore[assignment]
            for i, f in enumerate(findings):
                # SEC-01: Scrub secrets from payload_text before LMDB WAL storage.
                raw_payload_text = f.payload_text
                if raw_payload_text:
                    try:
                        from hledac.universal.security.secrets_scrubber import scrub_secrets

                        payload_text = scrub_secrets(raw_payload_text)
                    except Exception:  # noqa: BLE001 — fail-safe: use raw if scrubbing fails
                        payload_text = raw_payload_text
                else:
                    payload_text = raw_payload_text
                items[i] = (f"finding:{f.finding_id}", {
                    "id": f.finding_id,
                    "query": f.query,
                    "source_type": f.source_type,
                    "confidence": f.confidence,
                    "ts": f.ts,
                    "provenance": f.provenance,
                    "payload_text": payload_text,
                })
            if items:
                results = (
                    self._wal_manager.wal_put_many(items)
                    if hasattr(self._wal_manager, "wal_put_many")
                    else [False] * len(items)
                )
                if isinstance(results, list) and (not all(results)):
                    failed = sum((1 for r in results if not r))
                    _logger.warning(f"[P1-2 WAL] Batch WAL: {failed}/{len(items)} items failed")
                    return False
                elif not isinstance(results, list):
                    _logger.warning(f"[P1-2 WAL] Batch WAL: unexpected return type {type(results)}")
                    return False
            return True
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            _logger.error(f"[P1-2 WAL] Batch WAL exception: {e}")
            return False

    def _duckdb_arrow_sync(self, findings: list[CanonicalFinding]) -> tuple[int, str | None]:
        """
        Sprint P1-2: DuckDB Arrow-only sync helper - DuckDB Single-Writer Variant 2.

        Runs on _duckdb_arrow_executor. Caller is responsible for WAL step
        (separate executor, sequential WAL-first invariant).

        Returns (inserted_count, error_type) - same shape as
        _sync_record_canonical_findings_batch_arrow.
        """
        return self._sync_record_canonical_findings_batch_arrow(findings)

    def _sync_record_canonical_findings_batch_arrow_standalone(self, findings: list[CanonicalFinding]) -> list[dict]:
        """
        Arrow zero-copy fallback for legacy batch path (async_record_canonical_findings_batch).

        Combines WAL + DuckDB Arrow into a single sync helper so the legacy fallback
        path also benefits from zero-copy Arrow INSERT. Replaces the tuple-based
        _canonical_findings_batch_to_activation_results path entirely.

        MUST be called on the worker thread.
        Returns list[dict] with 1:1 mapping.
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        if not findings:
            return []
        n = len(findings)
        # B2-FIX: pre-allocate results list — avoids O(n) list reallocation
        # in all 4 code paths (3 error + 1 success).
        ret: list[dict] = [None] * n  # type: ignore[assignment]
        lmdb_ok = False
        try:
            if not hasattr(self, "_wal_manager") or self._wal_manager is None:
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    _logger.error("[Arrow-standalone] _db_path is None - WAL root unavailable")
                    for i in range(n):
                        ret[i] = {"lmdb_success": False, "duckdb_success": None, "error": "no wal root"}
                    return ret
                self._wal_manager = DuckDBWALManager(wal_root=_wal_root)
                self._wal_manager.initialize()
            # B2-FIX: pre-allocate items list — avoids O(n) list reallocation.
            items: list[tuple[str, dict]] = [None] * n  # type: ignore[assignment]
            for i, f in enumerate(findings):
                # SEC-01: Scrub secrets from payload_text before LMDB WAL storage.
                raw_payload_text = f.payload_text
                if raw_payload_text:
                    try:
                        from hledac.universal.security.secrets_scrubber import scrub_secrets

                        payload_text = scrub_secrets(raw_payload_text)
                    except Exception:  # noqa: BLE001 — fail-safe: use raw if scrubbing fails
                        payload_text = raw_payload_text
                else:
                    payload_text = raw_payload_text
                items[i] = (f"finding:{f.finding_id}", {
                    "id": f.finding_id,
                    "query": f.query,
                    "source_type": f.source_type,
                    "confidence": f.confidence,
                    "ts": f.ts,
                    "provenance": f.provenance,
                    "payload_text": payload_text,
                })
            if items:
                lmdb_ok = self._wal_manager.wal_put_many(items) if hasattr(self._wal_manager, "wal_put_many") else False
                if not lmdb_ok:
                    _logger.warning(f"[Arrow-standalone] WAL failed for {len(items)} items")
                    for i in range(len(items)):
                        ret[i] = {"lmdb_success": False, "duckdb_success": None, "error": "lmdb batch failed"}
                    return ret
        except Exception as e:  # noqa: BLE001 — best-effort; Arrow/Parquet operation; non-critical
            _logger.error(f"[Arrow-standalone] WAL exception: {e}")
            for i in range(n):
                ret[i] = {"lmdb_success": False, "duckdb_success": None, "error": str(e)}
            return ret
        duckdb_count, duckdb_err = self._sync_record_canonical_findings_batch_arrow(findings)
        if duckdb_err is not None:
            _logger.error(f"[Arrow-standalone] DuckDB Arrow failed: {duckdb_err}")
            duckdb_all_ok = False
        elif duckdb_count < len(findings):
            _logger.debug(
                f"[Arrow-standalone] DuckDB MERGE deduplication: {duckdb_count}/{len(findings)} inserted (rest were deduplicated by MERGE)"
            )
            duckdb_all_ok = True
        else:
            duckdb_all_ok = True
        for i, f in enumerate(findings):
            ret[i] = {
                "finding_id": f.finding_id,
                "lmdb_success": lmdb_ok,
                "duckdb_success": duckdb_all_ok,
                "error": duckdb_err,
            }
        return ret

    async def aclose(self, timeout_s: float | None = None) -> None:
        """
        P1-9: Async idempotent shutdown with canonical timeout + force-shutdown pattern.

        Delegates to _do_sync_close(emergency=False) for shared synchronous cleanup,
        then performs async-only operations (bg task cancellation).

        Idempotent: safe to call multiple times.
        """
        if self._closed:
            return
        await shutdown_aclose(
            name="DuckDBShadowStore",
            coro=self._do_shutdown(),
            timeout_s=timeout_s if timeout_s is not None else self.DEFAULT_TIMEOUT_S,
        )

    async def _do_shutdown(self) -> None:
        """Inner cleanup — called by aclose() via shutdown_aclose()."""
        if self._checkpoint_task is not None:
            self._checkpoint_task.cancel()
            self._checkpoint_task = None
        _bg = getattr(self, "_bg_tasks", None)
        if _bg is not None:
            await _bg.cancel()
        self._do_sync_close(emergency=False)
        await self._do_async_close()

    async def rrf_rank_findings(self, query: str, k: int = 30) -> list[dict]:
        """
        Sprint 8TC B.1: Reciprocal Rank Fusion přes 4 signály.

        Signály:
          1. semantic_score  - z LanceDB ANN (pokud dostupný)
          2. pattern_count   - počet pattern matche
          3. ioc_degree      - počet navázaných IOC uzlů
          4. recency_score   - inverzní age (novější = vyšší)

        SQL RRF: SUM(1.0 / (k + rank_i)) přes všechny signály.
        Chybějící sloupce se přidávají dynamicky přes ALTER TABLE.

        Args:
            query: Search query string to filter canonical_findings
            k: RRF constant (default 30 - snižuje vliv nízkých ranků)

        Returns:
            list[dict] s keys: finding_id, content, rrf_score, semantic_score,
            pattern_count, ioc_degree, ts
        """
        if not self._initialized or self._closed:
            return []
        self.ensure_connected()
        loop = asyncio.get_running_loop()

        def _sync_rrf_rank() -> list[dict]:
            """ISSUE-008 P1: Uses read pool for parallel analytical queries."""
            try:
                conn = self._get_read_conn()
                if conn is None:
                    return []
                rrf_sql = "\n                WITH\n                  ranked AS (\n                      SELECT id AS finding_id,\n                             ROW_NUMBER() OVER (ORDER BY COALESCE(confidence, 0) DESC) AS r1,\n                             ROW_NUMBER() OVER (ORDER BY COALESCE(ts, 0) DESC) AS r2,\n                             ROW_NUMBER() OVER (ORDER BY source_type ASC) AS r3\n                        FROM canonical_findings\n                       WHERE query = ?1\n                  ),\n                  rrf AS (\n                      SELECT finding_id, r1 AS r FROM ranked\n                      UNION ALL\n                      SELECT finding_id, r2 AS r FROM ranked\n                      UNION ALL\n                      SELECT finding_id, r3 AS r FROM ranked\n                  )\n                SELECT f.id AS finding_id,\n                       f.query AS content,\n                       f.ts,\n                       f.confidence AS semantic_score,\n                       f.source_type,\n                       f.confidence,\n                       SUM(1.0 / (?2 + rrf.r)) AS rrf_score\n                  FROM rrf\n                  JOIN canonical_findings f ON f.id = rrf.finding_id\n                 WHERE f.query = ?1\n                 GROUP BY f.id, f.query, f.ts, f.confidence, f.source_type\n                 ORDER BY rrf_score DESC\n                 LIMIT ?2\n                "
                rows = list(self.arrow_fetch_batch(conn, rrf_sql, [query, k]))
                return [
                    {
                        "finding_id": str(r[0]),
                        "content": r[1] or "",
                        "ts": r[2] or 0.0,
                        "semantic_score": r[3] or 0.0,
                        "source_type": r[4] or "",
                        "confidence": r[5] or 0.0,
                        "rrf_score": r[6] or 0.0,
                    }
                    for r in rows
                ]
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                return []

        return await loop.run_in_executor(self._executor, _sync_rrf_rank)

    async def _bounded_startup_replay(self, replay_pending_limit: int, replay_timeout_s: float) -> None:
        """
        Sprint 8L + FLOW-03: Time-boxed startup replay integrated into async_initialize.

        Scans pending_duckdb_sync:* markers, replays up to replay_pending_limit
        of them, and respects replay_timeout_s wall-time budget.

        FLOW-03: Also scans prewrite:{id} markers without checkpoint:{id} and
        replays the corresponding finding:{id} to DuckDB. On success writes
        checkpoint:{id} and deletes prewrite:{id}.

        Boot barrier: _startup_ready is NOT set during replay, so activation
        writes are held off until replay completes or times out.

        Kooperativní yield: asyncio.sleep(0) between chunks to avoid
        starving the event loop during long replay runs.

        Args:
            replay_pending_limit: Maximum markers to replay
            replay_timeout_s:    Wall-time budget in seconds
        """
        import time as _time

        lock = self._ensure_replay_lock()
        deadline = _time.monotonic() + replay_timeout_s
        all_markers = self._wal_scan_pending_sync_markers()

        # FLOW-03: Also scan for orphaned prewrite markers needing recovery
        prewrites = self._wal_scan_prewrites_without_checkpoint()
        _logger = logging.getLogger(__name__)
        if prewrites:
            _logger.info(f"[FLOW-03] Startup recovery: {len(prewrites)} orphaned prewrite markers found")
        for pw in prewrites:
            fid = pw.get("id", "")
            if not fid:
                continue
            if _time.monotonic() > deadline:
                break
            try:
                # Replay the finding to DuckDB
                wal_record = self._wal_get_finding(fid) if hasattr(self, '_wal_get_finding') else None
                if wal_record is None:
                    # Try via wal_manager
                    mgr = self._ensure_wal_manager()
                    if mgr is not None:
                        wal_record = mgr.wal_get_finding(fid)
                if wal_record is not None:
                    query = wal_record.get("query", "")
                    source_type = wal_record.get("source_type", "unknown")
                    confidence = wal_record.get("confidence", 1.0)
                    db_ok = self._sync_insert_finding(fid, query, source_type, confidence)
                    if db_ok:
                        self._wal_write_checkpoint(fid)
                        self._wal_clear_prewrite(fid)
                        _logger.info(f"[FLOW-03] Recovered finding {fid} from orphaned prewrite")
                    else:
                        _logger.warning(f"[FLOW-03] Failed to recover finding {fid} via DuckDB insert")
                else:
                    _logger.warning(f"[FLOW-03] No WAL truth found for orphaned prewrite {fid}")
            except Exception as e:
                _logger.debug(f"[FLOW-03] Prewrite recovery exception for {fid}: {e}")

        if not all_markers and not prewrites:
            return
        seen_ids: set = set()
        unique_markers: list[dict[str, Any]] = []
        for m in all_markers:
            fid = m.get("id", "")
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                unique_markers.append(m)
        del seen_ids
        markers_to_replay = unique_markers[:replay_pending_limit]
        del unique_markers
        async with lock:
            for i, marker in enumerate(markers_to_replay):
                if _time.monotonic() > deadline:
                    break
                fid = marker.get("id", "")
                if not fid:
                    continue
                if i > 0 and i % self.REPLAY_CHUNK_SIZE == 0:
                    await asyncio.sleep(0)
                try:
                    async with asyncio.timeout(max(deadline - _time.monotonic(), 0.1)):
                        await self.async_replay_single_pending_marker(fid)
                except TimeoutError:
                    break

    def _ensure_replay_lock(self) -> asyncio.Lock:
        """Lazily initialize the replay lock on the current event loop."""
        if self._replay_lock is None:
            self._replay_lock = asyncio.Lock()
        return self._replay_lock

    async def async_replay_single_pending_marker(self, finding_id: str) -> ReplayResult:
        """
        Sprint 8H: Replay a single pending marker by finding_id.

        Recovery semantics per marker:
          1. Marker exists? -> marker_found
          2. WAL finding:{id} truth exists? -> wal_truth_found
          3. If truth missing -> failure (can't recover)
          4. DuckDB write via same safe path as activation
          5. Fresh read-back from new connection confirms durability
          6. Success -> clear pending marker
          7. Failure -> bump retry count; if >= MAX_RETRY_COUNT -> dead-letter

        Idempotency: if DuckDB already has the record, consider it a success.

        Args:
            finding_id: The finding identifier to replay.

        Returns:
            ReplayResult with all fields populated.
        """
        result: ReplayResult = ReplayResult(
            finding_id=finding_id,
            marker_found=False,
            wal_truth_found=False,
            duckdb_written=False,
            marker_cleared=False,
            read_back_verified=False,
            deadlettered=False,
            retry_count=0,
            error=None,
        )
        if self._closed:
            result["error"] = "store closed"
            return result
        marker = self._wal_get_pending_marker(finding_id)
        if marker is None:
            try:
                loop = asyncio.get_running_loop()
                already_there = await loop.run_in_executor(self._executor, self._sync_verify_duckdb_record, finding_id)
                if already_there:
                    result["marker_found"] = False
                    result["wal_truth_found"] = False
                    result["duckdb_written"] = True
                    result["read_back_verified"] = True
                    result["error"] = None
                    return result
            except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                pass
            result["marker_found"] = False
            result["error"] = f"no pending marker found for {finding_id}"
            return result
        result["marker_found"] = True
        try:
            if not hasattr(self, "_wal_lmdb") or self._wal_lmdb is None:
                result["wal_truth_found"] = False
                result["error"] = "WAL not initialized"
                return result
            wal_key = f"finding:{finding_id}"
            wal_record = self._wal_lmdb.get(wal_key)
            if wal_record is None:
                result["wal_truth_found"] = False
                result["error"] = f"WAL truth missing for {finding_id}"
                return result
            result["wal_truth_found"] = True
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            result["wal_truth_found"] = False
            result["error"] = f"WAL lookup failed: {e}"
            return result
        retry_count = marker.get("_retry_count", 0)
        result["retry_count"] = retry_count
        loop = asyncio.get_running_loop()
        try:
            db_written = await loop.run_in_executor(self._executor, self._sync_replay_single_marker, finding_id, marker)
            result["duckdb_written"] = db_written
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            result["duckdb_written"] = False
            result["error"] = f"DuckDB write exception: {e}"
        if not result["duckdb_written"]:
            new_retry = self._get_and_bump_retry_count(finding_id)
            result["retry_count"] = new_retry
            if new_retry >= self.MAX_RETRY_COUNT:
                dl_ok = self._wal_write_deadletter_marker(
                    finding_id=finding_id,
                    query=marker.get("query", ""),
                    source_type=marker.get("source_type", "unknown"),
                    confidence=marker.get("confidence", 1.0),
                    error=result["error"] or "max retries exceeded",
                    retry_count=new_retry,
                )
                if dl_ok:
                    self._wal_clear_pending_sync_marker(finding_id)
                    result["deadlettered"] = True
                    result["marker_cleared"] = True
            return result
        try:
            read_back_ok = await loop.run_in_executor(self._executor, self._sync_verify_duckdb_record, finding_id)
            result["read_back_verified"] = read_back_ok
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            result["read_back_verified"] = False
            result["error"] = f"read-back failed: {e}"
            return result
        if result["read_back_verified"]:
            cleared = self._wal_clear_pending_sync_marker(finding_id)
            result["marker_cleared"] = cleared
        return result

    async def async_replay_all_pending_duckdb_sync(self, limit: int | None = None) -> list[ReplayResult]:
        """
        Sprint 8H: Replay all pending markers with chunking and event-loop yields.

        Uses per-instance replay lock to prevent concurrent replay of same markers.
        Processes markers in chunks of REPLAY_CHUNK_SIZE, yielding to event loop
        between chunks to avoid starving live operations.

        Idempotency: markers that already exist in DuckDB are treated as success.

        Args:
            limit: Optional maximum number of markers to replay. None = all.

        Returns:
            list[ReplayResult], one per processed marker.
        """
        if self._closed:
            return []
        lock = self._ensure_replay_lock()
        all_markers = self._wal_scan_pending_sync_markers()
        if not all_markers:
            return []
        seen_ids: set = set()
        unique_markers: list[dict[str, Any]] = []
        for m in all_markers:
            fid = m.get("id", "")
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                unique_markers.append(m)
        del seen_ids
        if limit is not None:
            unique_markers = unique_markers[:limit]
        results: list[ReplayResult] = []
        chunk_size = self.REPLAY_CHUNK_SIZE
        async with lock:
            for i in range(0, len(unique_markers), chunk_size):
                chunk = unique_markers[i : i + chunk_size]
                for marker in chunk:
                    fid = marker.get("id", "")
                    if not fid:
                        continue
                    result = await self.async_replay_single_pending_marker(fid)
                    results.append(result)
                if i + chunk_size < len(unique_markers):
                    await asyncio.sleep(0)
        return results

    @property
    def is_initialized(self) -> bool:
        """Return True if sidecar was successfully initialized."""
        return self._initialized

    def advance_ioc_sprint(self, sprint_id: int) -> None:
        """
        Advance IOC dedup store to new sprint boundary.

        Issue #14: Delegates to SprintBoundaryCoordinator to keep
        _DuckDBQueryCache pure-cache (no dedup knowledge) and
        DedupManager pure-dedup (no cache knowledge).
        """
        if self._query_cache is None:
            return
        coordinator = SprintBoundaryCoordinator(self._query_cache, self._dedup_manager)
        coordinator.advance(sprint_id)

    @property
    def is_closed(self) -> bool:
        """Return True if sidecar has been shut down."""
        return self._closed

    @property
    def duckdb_mode(self) -> str:
        """
        Returns the active DuckDB runtime mode for sprint telemetry.

        STORAGE-DUP-003: Always "inprocess" — IPC subprocess removed (S-04).
        """
        return "inprocess"

    @property
    def is_subprocess_mode(self) -> bool:
        """Always False — subprocess mode removed (STORAGE-DUP-003)."""
        return False

    @property
    def db_path(self) -> Path | None:
        """Return the database path (None for :memory: mode)."""
        return self._db_path

    @property
    def temp_dir(self) -> Path | None:
        """Return the temp directory path (None if not using RAMDISK)."""
        return self._temp_dir

    @property
    def memory_limit(self) -> str:
        """Return the configured memory limit string."""
        return self._memory_limit

    @property
    def max_temp(self) -> str:
        """Return the configured max temp size string."""
        return self._max_temp

    @property
    def is_ramdisk_mode(self) -> bool:
        """Return True if running in RAMDISK-active mode."""
        return self._temp_dir is not None

    def size_bytes(self) -> int | None:
        """Return the database file size in bytes, or None for :memory: mode."""
        if self._db_path is None:
            return None
        try:
            return self._db_path.stat().st_size
        except OSError:
            return None

    async def vacuum_async(self) -> bool:
        """
        Execute VACUUM ANALYZE on the DuckDB file to reclaim space after deletions.

        Only available for file mode (_db_path is not None). Returns True on success.
        Fail-safe: any error is logged and False is returned.
        """
        if self._db_path is None:
            return False
        try:
            import psutil

            total_ram = psutil.virtual_memory().total
            size = self.size_bytes()
            if size is not None and size > 3 * 1024**3 and (total_ram < 10 * 1024**3):
                logger.warning(
                    "[duckdb_vacuum] CRITICAL: DuckDB %.1fGB on %.1fGB RAM system — vacuum recommended",
                    size / 1024**3,
                    total_ram / 1024**3,
                )
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            pass
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._vacuum_sync)
            return True
        except Exception as e:  # noqa: BLE001 — best-effort; async operation failure; non-critical
            logger.warning(f"[duckdb_vacuum] VACUUM failed: {e}")
            return False

    def _vacuum_sync(self) -> None:
        """Execute VACUUM ANALYZE synchronously on worker thread."""
        if self._db_path is None:
            return
        # F350M-R 4.6: Set BACKGROUND QoS for vacuum thread — lowers priority
        # F350M-R-A3: QoS restore MUST be in finally block — exception path bypassed otherwise
        _restored_qos = False

        def _restore_qos() -> None:
            nonlocal _restored_qos
            if _restored_qos:
                return
            try:
                from hledac.universal.tools.file_cache import apply_thread_qos, QOS_CLASS_USER_INITIATED
                apply_thread_qos(QOS_CLASS_USER_INITIATED)
                _restored_qos = True
            except Exception:  # noqa: BLE001 — best-effort; QoS hinting; non-critical
                pass

        try:
            try:
                from hledac.universal.tools.file_cache import apply_thread_qos, QOS_CLASS_BACKGROUND
                apply_thread_qos(QOS_CLASS_BACKGROUND)
            except Exception:  # noqa: BLE001 — best-effort; QoS hinting; non-critical
                pass
            duckdb = _get_duckdb()
            tmp_conn = duckdb.connect(str(self._db_path), read_only=False)
            try:
                tmp_conn.execute("SET memory_limit = '1GB'")
                tmp_conn.execute("PRAGMA threads = 2")
                tmp_conn.execute("SET preserve_insertion_order = false")
                tmp_conn.execute("VACUUM")
            finally:
                try:
                    tmp_conn.close()
                except Exception:  # noqa: BLE001 — best-effort; connection close; non-critical
                    pass
                _restore_qos()  # A3: restore QoS even on VACUUM exception
        except Exception:  # noqa: BLE001 — best-effort; vacuum failure; non-critical
            _restore_qos()  # A3: restore QoS on outer exception too
            raise

    async def async_vacuum_if_needed(self, threshold_bytes: int = 2 * 1024**3) -> bool:
        """
        Conditionally vacuum if the DB file exceeds threshold_bytes.

        Args:
            threshold_bytes: size above which vacuum is triggered (default 2GB)

        Returns True if vacuum was triggered and succeeded, False otherwise.
        """
        size = self.size_bytes()
        if size is None:
            return False
        if size > threshold_bytes:
            logger.info(f"[duckdb_vacuum] DB size {size / 1024**3:.1f}GB > threshold, running VACUUM")
            return await self.vacuum_async()
        return False

    async def _maybe_auto_maintenance(self) -> None:
        """
        RES-03: Trigger automatic maintenance based on op count (time-based
        triggers are handled by _maintenance_loop).

        Called after each write batch via async_ingest_findings_batch.
        Op-count based triggers: VACUUM every 10K ops, CHECKPOINT every 5K ops.
        Fail-safe: any error is silently caught and logged.
        """
        try:
            current_time = _time.monotonic()

            # Op-count based VACUUM
            if self._write_op_counter >= self._vacuum_interval_ops:
                await self.vacuum_async()
                self._write_op_counter = 0
                self._last_vacuum_time = current_time

            # Op-count based CHECKPOINT
            elif self._write_op_counter >= self._checkpoint_interval_ops:
                await self._checkpoint_async()
                self._write_op_counter = 0
                self._last_checkpoint_time = current_time
        except Exception:  # noqa: BLE001 — best-effort; maintenance failure; non-critical
            pass

    async def _checkpoint_async(self) -> bool:
        """
        Execute CHECKPOINT to flush WAL to main DB.

        Fail-safe: any error is logged and False is returned.
        """
        if self._db_path is None:
            return False
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._checkpoint_sync)
            return True
        except Exception as e:
            logger.warning(f"[duckdb_checkpoint] CHECKPOINT failed: {e}")
            return False

    def _checkpoint_sync(self) -> None:
        """Execute CHECKPOINT synchronously on worker thread."""
        if self._db_path is None:
            return
        duckdb = _get_duckdb()
        tmp_conn = duckdb.connect(str(self._db_path), read_only=False)
        try:
            tmp_conn.execute("CHECKPOINT")
        finally:
            tmp_conn.close()

    @property
    def executor(self) -> ThreadPoolExecutor:
        """Return the internal executor (for test introspection)."""
        return self._executor

    def pending_marker_count(self) -> int:
        """
        Sprint 8L: Return the number of pending_duckdb_sync:* markers in WAL LMDB.

        Cheap O(n) prefix scan - bounded by REPLAY_CHUNK_SIZE scan.
        Used for observability and benchmarking.
        """
        markers = self._wal_scan_pending_sync_markers()
        return len(markers)

    def deadletter_marker_count(self) -> int:
        """
        Sprint 8L: Return the number of deadletter_duckdb_sync:* markers in WAL LMDB.

        Cheap O(n) prefix scan.
        Used for observability and monitoring.
        """
        try:
            if not hasattr(self, "_wal_lmdb") or self._wal_lmdb is None:
                return 0
            env = self._wal_lmdb._env
            if env is None:
                return 0
            count = 0
            prefix = self.DEADLETTER_PREFIX.encode("utf-8")
            with env.begin(write=False, buffers=True) as txn:
                cursor = txn.cursor()
                if cursor.set_range(prefix):
                    for key_bytes, _ in cursor.iternext():
                        key = (
                            key_bytes.decode("utf-8")
                            if isinstance(key_bytes, bytes)
                            else bytes(key_bytes).decode("utf-8")
                        )
                        if not key.startswith(self.DEADLETTER_PREFIX):
                            break
                        count += 1
            return count
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return 0

    @property
    def startup_ready(self) -> bool:
        """Sprint 8L: True if boot barrier has been lifted (store accepts writes)."""
        return self._startup_ready.is_set()

    @property
    def startup_replay_done(self) -> bool:
        """Sprint 8L: True if startup replay has run (regardless of outcome)."""
        return self._startup_replay_done

    @property
    def invariant_memory_limit(self) -> str:
        """Return configured memory_limit string."""
        return self._memory_limit

    @property
    def invariant_max_temp(self) -> str:
        """Return configured max_temp_directory_size string."""
        return self._max_temp

    @property
    def invariant_temp_dir(self) -> Path | None:
        """Return configured temp_directory path (None if :memory: mode)."""
        return self._temp_dir

    def invariant_validate(self) -> dict:
        """
        Validate hardening invariants.

        Returns dict with keys:
            - has_no_gpu_pragma: bool
            - memory_limit_ok: bool (1GB or less)
            - temp_size_ok: bool (1GB or 0GB for :memory:)
            - temp_dir_on_ramdisk: bool (temp_dir under RAMDISK_ROOT if set)
        """
        results = {
            "has_no_gpu_pragma": True,
            "memory_limit_ok": False,
            "temp_size_ok": False,
            "temp_dir_on_ramdisk": False,
        }
        try:
            mem_val = self._memory_limit.strip().upper()
            if mem_val.endswith("GB"):
                mem_gb = float(mem_val[:-2])
                results["memory_limit_ok"] = mem_gb <= 1.0
            elif mem_val.endswith("MB"):
                mem_mb = float(mem_val[:-2])
                results["memory_limit_ok"] = mem_mb <= 1024
            else:
                results["memory_limit_ok"] = True
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            results["memory_limit_ok"] = False
        try:
            temp_val = self._max_temp.strip().upper()
            if temp_val in ("0GB", "0", "0MB"):
                results["temp_size_ok"] = self._temp_dir is None
            elif temp_val.endswith("GB"):
                temp_gb = float(temp_val[:-2])
                results["temp_size_ok"] = temp_gb <= 1.0
            elif temp_val.endswith("MB"):
                temp_mb = float(temp_val[:-2])
                results["temp_size_ok"] = temp_mb <= 1024
            else:
                results["temp_size_ok"] = True
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            results["temp_size_ok"] = False
        if self._temp_dir is not None:
            try:
                from hledac.universal.paths import RAMDISK_ROOT

                results["temp_dir_on_ramdisk"] = str(self._temp_dir).startswith(str(RAMDISK_ROOT))
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                results["temp_dir_on_ramdisk"] = False
        else:
            results["temp_dir_on_ramdisk"] = True
        return results

    def _wal_write_finding(self, finding_id: str, query: str, source_type: str, confidence: float) -> bool:
        """
        Sprint 8A: Write a single finding to LMDB WAL (sync, no await).

        LMDB key format:  finding:{id}
        Value: serialized dict with id, query, source_type, confidence, ts

        Returns True if LMDB write succeeded.

        Delegation: Sprint F233A micro-cleanup - routes through WALManager
        to eliminate the residual direct LMDB WAL path.
        """
        if self._wal_manager is None:
            _wal_root = self._db_path.parent if self._db_path else None
            if _wal_root is None:
                return False
            self._wal_manager = DuckDBWALManager(wal_root=_wal_root)
            self._wal_manager.initialize()
        return self._wal_manager.wal_write_finding(
            finding_id=finding_id, query=query, source_type=source_type, confidence=confidence
        )

    def _ensure_wal_manager(self) -> DuckDBWALManager | None:
        """Ensure WAL manager is initialized. Returns it or None if unavailable."""
        if self._wal_manager is None:
            _wal_root = self._db_path.parent if self._db_path else None
            if _wal_root is None:
                return None
            self._wal_manager = DuckDBWALManager(wal_root=_wal_root)
            self._wal_manager.initialize()
        return self._wal_manager

    def _wal_write_prewrite(self, finding_id: str) -> bool:
        """FLOW-03: Write prewrite marker before DuckDB write."""
        mgr = self._ensure_wal_manager()
        if mgr is None:
            return False
        return mgr.wal_write_prewrite(finding_id)

    def _wal_write_checkpoint(self, finding_id: str) -> bool:
        """FLOW-03: Write checkpoint marker after DuckDB write succeeds."""
        mgr = self._ensure_wal_manager()
        if mgr is None:
            return False
        return mgr.wal_write_checkpoint(finding_id)

    def _wal_clear_prewrite(self, finding_id: str) -> bool:
        """FLOW-03: Clear prewrite marker after checkpoint is written."""
        mgr = self._ensure_wal_manager()
        if mgr is None:
            return False
        return mgr.wal_clear_prewrite(finding_id)

    def _wal_has_checkpoint(self, finding_id: str) -> bool:
        """FLOW-03: Check if checkpoint exists for finding."""
        mgr = self._ensure_wal_manager()
        if mgr is None:
            return False
        return mgr.wal_has_checkpoint(finding_id)

    def _wal_scan_prewrites_without_checkpoint(self) -> list[dict[str, Any]]:
        """FLOW-03: Scan for prewrites needing recovery."""
        mgr = self._ensure_wal_manager()
        if mgr is None:
            return []
        return mgr.wal_scan_prewrites_without_checkpoint()

    def _wal_get_finding(self, finding_id: str) -> dict[str, Any] | None:
        """Get WAL truth record for a finding."""
        mgr = self._ensure_wal_manager()
        if mgr is None:
            return None
        return mgr.wal_get_finding(finding_id)

    def _activation_record_finding(self, finding_id: str, query: str, source_type: str, confidence: float) -> dict:
        """
        Sprint 8A + FLOW-03: Record a structured finding - LMDB WAL first, DuckDB second.

        FLOW-03 Checkpoint Protocol:
            Phase 1: Write prewrite:{id} marker (in-flight signal)
            Phase 2: DuckDB insert
            Phase 3: Write checkpoint:{id} + delete prewrite:{id} on success

        Partial failure semantics:
          - LMDB OK + DuckDB FAIL -> LMDB preserved, pending-sync marker written
          - LMDB FAIL + DuckDB SKIP -> return lmdb_success=False, duckdb_success=None

        Returns dict with keys: lmdb_success, duckdb_success, finding_id, query
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        result = {"lmdb_success": False, "duckdb_success": None, "finding_id": finding_id, "query": query}
        lmdb_ok = self._wal_write_finding(finding_id, query, source_type, confidence)
        result["lmdb_success"] = lmdb_ok
        if not lmdb_ok:
            _logger.warning(f"[Sprint 8A] WAL-DuckDB desync: LMDB write failed for {finding_id}")
            return result
        # FLOW-03 Phase 1: Write prewrite marker before DuckDB write
        try:
            self._wal_write_prewrite(finding_id)
        except Exception:  # noqa: BLE001 — best-effort; prewrite is advisory
            pass
        try:
            db_ok = self._sync_insert_finding(finding_id, query, source_type, confidence)
            result["duckdb_success"] = db_ok
            if db_ok:
                # FLOW-03 Phase 3: Write checkpoint and clear prewrite on success
                try:
                    self._wal_write_checkpoint(finding_id)
                    self._wal_clear_prewrite(finding_id)
                except Exception:  # noqa: BLE001 — best-effort; checkpoint is advisory
                    pass
            else:
                _logger.error(f"[Sprint 8A] WAL-DuckDB desync: DuckDB write failed for {finding_id}, LMDB preserved")
                self._wal_write_pending_sync_marker(finding_id, query, source_type, confidence)
                # Clear prewrite since DuckDB failed — no checkpoint possible
                try:
                    self._wal_clear_prewrite(finding_id)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            result["duckdb_success"] = False
            _logger.error(f"[Sprint 8A] WAL-DuckDB desync: DuckDB exception for {finding_id}: {e}, LMDB preserved")
            self._wal_write_pending_sync_marker(finding_id, query, source_type, confidence)
            try:
                self._wal_clear_prewrite(finding_id)
            except Exception:  # noqa: BLE001
                pass
        return result

    def _wal_evict_oldest_pending_markers(self, keep_count: int) -> int:
        """
        P0-9 fix: Evict oldest pending sync markers to enforce MAX_PENDING_SYNC_MARKERS bound.

        Removes (total_count - keep_count) oldest markers by timestamp.
        Returns number of markers evicted.
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        try:
            if not hasattr(self, "_wal_lmdb") or self._wal_lmdb is None:
                return 0
            env = self._wal_lmdb._env
            if env is None:
                return 0
            prefix = "pending_duckdb_sync:"
            markers: list[tuple[float, str]] = []
            with env.begin(write=False, buffers=True) as txn:
                cursor = txn.cursor()
                if cursor.set_range(prefix.encode("utf-8")):
                    for key_bytes, value_bytes in cursor.iternext():
                        key = (
                            key_bytes.decode("utf-8")
                            if isinstance(key_bytes, bytes)
                            else bytes(key_bytes).decode("utf-8")
                        )
                        if not key.startswith(prefix):
                            break
                        try:
                            # S-02: orjson.loads accepts bytes/bytearray/memoryview directly — zero-copy
                            value = _ORJSON_DECODER(value_bytes)
                            ts = value.get("ts", 0.0)
                            markers.append((ts, key))
                        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
                            continue
            if len(markers) <= keep_count:
                return 0
            markers.sort(key=lambda x: x[0])
            evict_count = len(markers) - keep_count
            evicted = 0
            for i in range(evict_count):
                _, key = markers[i]
                if self._wal_lmdb.delete(key):
                    evicted += 1
            if evicted > 0:
                _logger.warning(f"[P0-9] Evicted {evicted} oldest pending sync markers (limit={keep_count})")
            return evicted
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return 0

    def _wal_write_pending_sync_marker(self, finding_id: str, query: str, source_type: str, confidence: float) -> bool:
        """
        Sprint 8F: Write a pending-sync recovery marker to LMDB.
        P0-9 fix: Enforces MAX_PENDING_SYNC_MARKERS bound via oldest eviction.

        Marker key:  pending_duckdb_sync:{id}
        Value:       same structure as WAL finding (id, query, source_type, confidence, ts)

        This marker is written ONLY when LMDB succeeded but DuckDB failed.
        A future recovery sprint can find it via prefix scan and retry the DuckDB write.
        """
        try:
            import time as _time

            from hledac.universal.tools.lmdb_kv import LMDBKVStore

            if not hasattr(self, "_wal_lmdb"):
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    return False
                self._wal_lmdb = LMDBKVStore(path=str(_wal_root / "shadow_wal.lmdb"))
                try:
                    self._wal_lmdb.put("_schema_init", {"_": "ok"})
                    self._wal_lmdb.delete("_schema_init")
                except (OSError, RuntimeError) as e:
                    logger.debug(f"[DUCKDB] WAL schema init failed: {e}")
            self._wal_evict_oldest_pending_markers(self.MAX_PENDING_SYNC_MARKERS - 1)
            key = f"pending_duckdb_sync:{finding_id}"
            value = {
                "id": finding_id,
                "query": query,
                "source_type": source_type,
                "confidence": confidence,
                "ts": _time.time(),
            }
            return self._wal_lmdb.put(key, value)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return False

    def _wal_scan_pending_sync_markers(self) -> list[dict[str, Any]]:
        """
        Sprint 8F: Efficient prefix scan for all pending_duckdb_sync markers.

        Returns list of marker values (dicts with id, query, source_type, confidence, ts).
        Uses LMDB cursor with prefix iteration - O(n) where n = number of pending markers,
        NOT O(N) full database scan.
        """
        try:
            if not hasattr(self, "_wal_lmdb"):
                return []
            env = self._wal_lmdb._env
            if env is None:
                return []
            results = []
            prefix = "pending_duckdb_sync:"
            with env.begin(write=False, buffers=True) as txn:
                cursor = txn.cursor()
                if cursor.set_range(prefix.encode("utf-8")):
                    for key_bytes, value_bytes in cursor.iternext():
                        key = (
                            key_bytes.decode("utf-8")
                            if isinstance(key_bytes, bytes)
                            else bytes(key_bytes).decode("utf-8")
                        )
                        if not key.startswith(prefix):
                            break
                        try:
                            # S-02: orjson.loads accepts bytes/bytearray/memoryview directly — zero-copy
                            value = _ORJSON_DECODER(value_bytes)
                            results.append(value)
                        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
                            continue
            return results
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            return []

    def _wal_clear_pending_sync_marker(self, finding_id: str) -> bool:
        """
        Sprint 8F: Clear a pending-sync marker after successful recovery.

        Called by a future recovery sprint after the DuckDB write succeeds.
        """
        try:
            if not hasattr(self, "_wal_lmdb"):
                return False
            key = f"pending_duckdb_sync:{finding_id}"
            return self._wal_lmdb.delete(key)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return False

    def _wal_write_deadletter_marker(
        self, finding_id: str, query: str, source_type: str, confidence: float, error: str, retry_count: int
    ) -> bool:
        """
        Sprint 8H: Write a marker to the dead-letter namespace after max retries exceeded.

        Dead-letter key:  deadletter_duckdb_sync:{id}
        Value:            id, query, source_type, confidence, ts, error, retry_count
        """
        try:
            import time as _time

            if not hasattr(self, "_wal_lmdb"):
                return False
            key = f"{self.DEADLETTER_PREFIX}{finding_id}"
            value = {
                "id": finding_id,
                "query": query,
                "source_type": source_type,
                "confidence": confidence,
                "ts": _time.time(),
                "error": error,
                "retry_count": retry_count,
            }
            return self._wal_lmdb.put(key, value)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return False

    def _wal_get_pending_marker(self, finding_id: str) -> dict[str, Any] | None:
        """
        Sprint 8H: Get a single pending marker value by finding_id.

        Returns the marker dict or None if not found.
        """
        try:
            if not hasattr(self, "_wal_lmdb"):
                return None
            key = f"pending_duckdb_sync:{finding_id}"
            return self._wal_lmdb.get(key)
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return None

    def _wal_delete_deadletter_marker(self, finding_id: str) -> bool:
        """
        Sprint 8H: Delete a dead-letter marker (used when replay succeeds later).
        """
        try:
            if not hasattr(self, "_wal_lmdb"):
                return False
            key = f"{self.DEADLETTER_PREFIX}{finding_id}"
            return self._wal_lmdb.delete(key)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return False

    def _sync_replay_single_marker(self, finding_id: str, marker: dict[str, Any]) -> bool:
        """
        Sprint 8H: Synchronous single-marker replay - MUST be called on the worker thread.

        Uses the same _sync_insert_finding path as normal activation.
        Returns True if DuckDB write succeeded.
        """
        try:
            db_ok = self._sync_insert_finding(
                finding_id=marker.get("id", finding_id),
                query=marker.get("query", ""),
                source_type=marker.get("source_type", "unknown"),
                confidence=marker.get("confidence", 1.0),
            )
            return db_ok
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            return False

    def _sync_verify_duckdb_record(self, finding_id: str) -> bool:
        """
        Sprint 8H: Fresh read-back verification from a NEW DuckDB connection.

        Called after write commit to confirm the record is durable.
        Uses a non-read-only fresh connection so the WAL is flushed.
        MUST be called on the worker thread.
        """
        try:
            if self._db_path:
                duckdb = _get_duckdb()
                conn = duckdb.connect(str(self._db_path))
                try:
                    conn.execute("SET memory_limit = '1GB'")
                    conn.execute("PRAGMA threads = 2")
                    conn.execute("SET preserve_insertion_order = false")
                    sql = "SELECT 1 FROM canonical_findings WHERE id = ? LIMIT 1"
                    result = list(self.arrow_fetch_batch(conn, sql, [finding_id]))
                    return bool(result)
                finally:
                    conn.close()
            else:
                sql = "SELECT 1 FROM canonical_findings WHERE id = ? LIMIT 1"
                result = list(self.arrow_fetch_batch(self._persistent_conn, sql, [finding_id]))
                return bool(result)
        except Exception:  # noqa: BLE001 — best-effort; Arrow/Parquet operation; non-critical
            return False

    def _get_and_bump_retry_count(self, finding_id: str) -> int:
        """
        Sprint 8H: Get current retry count from marker metadata and bump it.

        Stores retry count in the marker value under "_retry_count" key.
        Returns the new retry count after bump.
        """
        try:
            marker = self._wal_get_pending_marker(finding_id)
            if marker is None:
                return 0
            current = marker.get("_retry_count", 0)
            new_count = current + 1
            marker["_retry_count"] = new_count
            key = f"pending_duckdb_sync:{finding_id}"
            self._wal_lmdb.put(key, marker)
            return new_count
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            return 0

    def _activation_record_findings_batch(self, findings: list[dict[str, Any]]) -> dict:
        """
        Sprint 8A + FLOW-03: Batch activation - LMDB WAL first, DuckDB second.

        FLOW-03 Checkpoint Protocol (batch):
            Phase 1: Write prewrite:{id} for all findings
            Phase 2: DuckDB bulk insert
            Phase 3: For each successfully inserted finding:
                - Write checkpoint:{id}
                - Delete prewrite:{id}

        Returns dict with keys: lmdb_success, duckdb_success, count,
                                failed_ids (list of ids that failed)
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        result = {"lmdb_success": False, "duckdb_success": False, "count": 0, "failed_ids": [], "orphaned_keys": []}
        if not findings:
            return result
        # FLOW-01: Track LMDB keys for potential compensating transaction
        _written_lmdb_keys: list[str] = []
        _finding_ids: list[str] = []
        try:
            import time as _time

            from hledac.universal.tools.lmdb_kv import LMDBKVStore

            if not hasattr(self, "_wal_lmdb"):
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    return result
                self._wal_lmdb = LMDBKVStore(path=str(_wal_root / "shadow_wal.lmdb"))
            items = []
            for f in findings:
                fid = f.get("id")
                if not fid:
                    continue
                key = f"finding:{fid}"
                _written_lmdb_keys.append(key)
                _finding_ids.append(fid)
                value = {
                    "id": fid,
                    "query": f.get("query", ""),
                    "source_type": f.get("source_type", "unknown"),
                    "confidence": f.get("confidence", 1.0),
                    "ts": _time.time(),
                }
                items.append((key, value))
            if items:
                lmdb_ok = self._wal_lmdb.put_many(items)
                result["lmdb_success"] = lmdb_ok
                if not lmdb_ok:
                    _logger.warning(f"[Sprint 8A] Batch WAL failed for {len(items)} items")
                    return result
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            _logger.error(f"[Sprint 8A] Batch WAL exception: {e}")
            return result

        # FLOW-03 Phase 1b: Write prewrite markers for all findings
        try:
            for fid in _finding_ids:
                try:
                    self._wal_write_prewrite(fid)
                except Exception:  # noqa: BLE001 — best-effort; prewrite is advisory
                    pass
        except Exception as e:
            _logger.debug(f"[FLOW-03] Batch prewrite failed: {e}")

        try:
            db_findings = [
                {
                    "id": f.get("id"),
                    "query": f.get("query", ""),
                    "source_type": f.get("source_type", "unknown"),
                    "confidence": f.get("confidence", 1.0),
                }
                for f in findings
                if f.get("id")
            ]
            if db_findings:
                inserted = self._sync_insert_findings_bulk(db_findings)
                result["duckdb_success"] = inserted >= len(db_findings)
                result["count"] = inserted
                if inserted < len(db_findings):
                    _logger.error(f"[Sprint 8A] Partial DuckDB batch: {inserted}/{len(db_findings)}, LMDB preserved")
                # FLOW-03 Phase 3: Write checkpoint for each successfully inserted finding
                if inserted > 0 and inserted == len(db_findings):
                    # All succeeded — checkpoint all
                    for fid in _finding_ids:
                        try:
                            self._wal_write_checkpoint(fid)
                            self._wal_clear_prewrite(fid)
                        except Exception:  # noqa: BLE001 — best-effort
                            pass
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            _logger.error(f"[Sprint 8A] Batch DuckDB exception: {e}, LMDB preserved")
            # FLOW-01 FIX: LMDB succeeded but DuckDB failed — compensating transaction
            if _written_lmdb_keys:
                result["orphaned_keys"] = _written_lmdb_keys
                self._cleanup_orphaned_lmdb_entries(_written_lmdb_keys)
            # FLOW-03: Clear prewrites since DuckDB failed
            for fid in _finding_ids:
                try:
                    self._wal_clear_prewrite(fid)
                except Exception:  # noqa: BLE001
                    pass
        return result

    def _cleanup_orphaned_lmdb_entries(self, orphaned_keys: list[str]) -> int:
        """
        FLOW-01 FIX: Compensating transaction for orphaned LMDB entries.

        Called when LMDB WAL succeeded but DuckDB failed — removes the orphaned
        LMDB entries to maintain consistency. Uses LMDB transaction delete.

        Args:
            orphaned_keys: List of LMDB keys (e.g. "finding:{id}") to delete

        Returns:
            Number of keys successfully deleted
        """
        if not orphaned_keys:
            return 0
        _logger = logging.getLogger(__name__)
        deleted = 0
        try:
            wal = getattr(self, '_wal_lmdb', None)
            if wal is None:
                _logger.warning('[FLOW-01] _cleanup_orphaned_lmdb_entries: no _wal_lmdb available')
                return 0
            for key in orphaned_keys:
                try:
                    if wal.delete(key):
                        deleted += 1
                except Exception as e:
                    _logger.debug(f'[FLOW-01] LMDB delete failed for {key}: {e}')
            if deleted > 0:
                _logger.info(f'[FLOW-01] Cleaned up {deleted}/{len(orphaned_keys)} orphaned LMDB entries')
        except Exception as e:
            _logger.error(f'[FLOW-01] Orphan cleanup error: {e}')
        return deleted

    def _acquire_process_lock(self) -> tuple[str, str]:
        """
        F269: Process-level lock using GraphLockManager (consolidated from F266-U5).

        Uses GraphLockManager singleton per db_path — same fcntl.flock-based locking
        as DuckPGQGraph. This unifies the 3 independent locking strategies into one.

        Three-tier locking strategy:
        1. 'excl' — we are the exclusive writer (lock acquired)
        2. 'ro'  — another process holds the lock, open READ-ONLY
        3. None  — lock unavailable, fall back to :memory:

        Returns:
            tuple: (lock_mode: str, message: str)
        """
        from hledac.universal.graph.lock_manager import GraphLockManager

        my_pid = os.getpid()
        lock_mgr = GraphLockManager(str(self._db_path))
        if lock_mgr.acquire(timeout_s=2.0):
            try:
                from hledac.universal.monitoring.alert_manager import get_lock_contention_tracker

                get_lock_contention_tracker().record_attempt(acquired=True)
            except Exception:  # noqa: BLE001 — best-effort; lock failure; non-critical
                pass
            return ("excl", f"PID {my_pid} acquired exclusive lock via GraphLockManager")
        denial = lock_mgr.denial_reason
        holder = lock_mgr.holder_pid
        test_conn = None
        try:
            test_conn = _get_duckdb().connect(str(self._db_path), read_only=True)
            test_conn.close()
            test_conn = None
            try:
                from hledac.universal.monitoring.alert_manager import get_lock_contention_tracker

                get_lock_contention_tracker().record_attempt(acquired=False)
            except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                pass
            msg = f"PID {my_pid} opening READ-ONLY (GraphLockManager denied: {denial})"
            if holder:
                msg = f"PID {my_pid} opening READ-ONLY (holder PID {holder}: {denial})"
            return ("ro", msg)
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            if test_conn is not None:
                try:
                    test_conn.close()
                except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                    pass
            return (
                None,
                f"PID {my_pid} GraphLockManager denied ({denial}), READ-ONLY failed ({e}) — using :memory: fallback",
            )

    def _cleanup_orphaned_locks(self) -> None:
        """
        F11C-2: Remove orphaned DuckDB and GraphLockManager lock files at startup.

        Called from async_initialize() before connecting. Uses the same stale
        detection as GraphLockManager to avoid removing locks held by live processes.

        DuckDB WAL lock path is: str(db_path) + ".lock"
        GraphLockManager lock path is: db_path.with_suffix(".lock") — same as DuckDB!
        """
        if self._db_path is None:
            try:
                self._resolve_path()
            except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                return
        if self._db_path is None:
            return
        import pathlib

        from hledac.universal.graph.lock_manager import _is_lock_stale

        try:
            lock_path = pathlib.Path(str(self._db_path) + ".lock")
            if lock_path.exists():
                is_stale, reason = _is_lock_stale(lock_path, self._db_path)
                if is_stale:
                    lock_path.unlink(missing_ok=True)
                    logger.debug(f"[DUCKDB] Removed stale lock {lock_path}: {reason}")
        except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            logger.warning(f"[DUCKDB] Lock cleanup failed: {e}")

    def _do_close(self) -> None:
        """
        Synchronous close helper - idempotent.

        Note: _closed guard removed - close() and _do_close() are always called
        together in the same call chain; close() sets _closed=True first and
        guards against re-entry. _do_close() always runs its cleanup.
        """
        self._closed = True
        self._initialized = False
        try:
            self._startup_ready.clear()
            self._startup_replay_done = False
        except Exception:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
            pass
        try:
            if hasattr(self, "_db_path") and self._db_path is not None:
                _db_str = str(self._db_path)
                if _db_str != ":memory:" and _db_str != "None":
                    import pathlib

                    _lock_path = pathlib.Path(_db_str).expanduser()
                    _lock_file = _lock_path.parent / (_lock_path.name + ".lock")
                    try:
                        _lock_file.unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001 — best-effort; lock failure; non-critical
                        pass
        except Exception:  # noqa: BLE001 — best-effort; lock failure; non-critical
            pass
        try:
            if hasattr(self, "_shared_executor") and self._shared_executor is not None:
                self._shared_executor.shutdown(wait=False)
        except Exception:  # noqa: BLE001 — best-effort; lock failure; non-critical
            pass
        try:
            if self._query_cache is not None:
                self._query_cache.close()
                self._query_cache = None
        except Exception:  # noqa: BLE001 — best-effort; DB query failure; non-critical
            pass

    DEDUP_NAMESPACE: str = "dedup:"

    def _dedup_key_from_fingerprint(self, fp: str) -> bytes:
        """Build dedup namespace key from BLAKE2b fingerprint."""
        return f"{self.DEDUP_NAMESPACE}{fp}".encode()

    def _dedup_lmdb_key_to_fingerprint(self, key: bytes) -> str:
        """Extract fingerprint from dedup namespace key."""
        return key.decode("utf-8")[len(self.DEDUP_NAMESPACE) :]

    def _init_persistent_dedup_lmdb(self) -> None:
        """Deprecated: initialization moved to DedupManager.initialize()."""
        try:
            from hledac.universal.paths import LMDB_STORE_ROOT

            dedup_path = LMDB_STORE_ROOT / "dedup.lmdb"
            dedup_path.mkdir(parents=True, exist_ok=True)
            from hledac.universal.tools.lmdb_kv import LMDBKVStore

            self._dedup_lmdb = LMDBKVStore(path=str(dedup_path), map_size=_DEDUP_LMDB_MAP_SIZE, max_keys=1000000)
            self._dedup_lmdb_path = dedup_path
            self._dedup_lmdb_last_error = None
            self._dedup_lmdb_boot_error = None
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            self._dedup_lmdb = None
            self._dedup_lmdb_path = None
            self._dedup_lmdb_boot_error = str(e)
            self._dedup_lmdb_last_error = str(e)

    def _init_semantic_dedup_cache(self) -> None:
        """
        Initialize semantic dedup cache.

        DEPRECATED (Sprint F222): Semantic dedup is now initialized by DedupManager.initialize().
        This stub exists only for backward compat - calls are no longer emitted.
        """
        pass

    def _lookup_persistent_dedup(self, fp: str) -> str | None:
        """
        Lookup a fingerprint in the persistent dedup LMDB.

        DEPRECATED (Sprint F222): Delegates to DedupManager.lookup_persistent_dedup().
        Kept for backward compat during migration - remove when all callers migrated.
        """
        if self._dedup_manager is None:
            return None
        return self._dedup_manager.lookup_persistent_dedup(fp)

    def _store_persistent_dedup(self, fp: str, finding_id: str) -> None:
        """
        Store a fingerprint -> finding_id mapping in persistent dedup LMDB.

        DEPRECATED (Sprint F222): Delegates to DedupManager.store_persistent_dedup().
        Kept for backward compat during migration - remove when all callers migrated.

        P1-4: Also update Bloom filter in DedupManager when available.
        Falls back to store._dedup_lmdb directly for backward compat with tests
        that mock store._dedup_lmdb without going through DedupManager.
        """
        if self._dedup_manager is not None:
            self._dedup_manager.store_persistent_dedup(fp, finding_id)
            return
        _dedup_lmdb = getattr(self, "_dedup_lmdb", None)
        if _dedup_lmdb is None:
            return
        try:
            key = f"dedup:{fp}".encode()
            value_bytes = finding_id.encode("utf-8")
            with _dedup_lmdb._env.begin(write=True) as txn:
                txn.put(key, value_bytes)
        except Exception:  # noqa: BLE001 — best-effort; export failure; non-critical
            pass

    def _add_to_hot_cache(self, fp: str, finding_id: str) -> None:
        """
        Add entry to bounded hot cache with FIFO eviction.

        DEPRECATED (Sprint F222): Delegates to DedupManager.add_to_hot_cache().
        Kept for backward compat during migration - remove when all callers migrated.
        """
        if self._dedup_manager is not None:
            self._dedup_manager.add_to_hot_cache(fp, finding_id)
            return
        if not hasattr(self, "_hot_cache_fallback"):
            self._hot_cache_fallback: dict[str, str] = {}
        self._hot_cache_fallback[fp] = finding_id

    def _hot_cache_lookup(self, fp: str) -> str | None:
        """
        Bounded hot cache lookup.

        DEPRECATED (Sprint F222): Delegates to DedupManager.hot_cache_lookup().
        Kept for backward compat during migration - remove when all callers migrated.
        """
        if self._dedup_manager is not None:
            return self._dedup_manager.hot_cache_lookup(fp)
        return getattr(self, "_hot_cache_fallback", {}).get(fp)

    def get_dedup_runtime_status(self) -> dict:
        """
        Sprint 8AG §6.17 + 8AK + 8AV + F222: Typed/cheap status surface for dedup subsystem.

        Sprint F222: Now delegates to DedupManager.get_runtime_status() for dedup-specific
        fields. QualityAssessmentState fields still pulled from _quality_state.
        """
        if self._dedup_manager is not None:
            dedup_status = self._dedup_manager.get_runtime_status(self._quality_state)
            return {
                **dedup_status,
                "in_memory_duplicate_count": self._quality_state._quality_duplicate_count,
                "persistent_duplicate_count": self._quality_state._persistent_duplicate_count,
                "accepted_count": self._quality_state._accepted_count,
                "low_information_rejected_count": self._quality_state._quality_rejected_count,
                "in_memory_duplicate_rejected_count": self._quality_state._quality_duplicate_count,
                "persistent_duplicate_rejected_count": self._quality_state._persistent_duplicate_count,
                "other_rejected_count": self._quality_state._quality_fail_open_count,
            }
        fallback_error = getattr(self, "_dedup_lmdb_boot_error", None)
        _dedup_lmdb = getattr(self, "_dedup_lmdb", None)
        _dedup_enabled = _dedup_lmdb is not None and fallback_error is None
        return {
            "persistent_dedup_enabled": _dedup_enabled,
            "bloom_filter_enabled": False,
            "bloom_filter_error": None,
            "last_boot_cleanup_error": fallback_error or getattr(self._dedup_manager, "_dedup_lmdb_boot_error", None)
            if self._dedup_manager
            else fallback_error,
            "last_dedup_error": None,
            "dedup_lmdb_path": str(getattr(self, "_dedup_lmdb_path", "") or ""),
            "dedup_namespace": "dedup:",
            "hot_cache_size": len(getattr(self, "_hot_cache_fallback", {})),
            "hot_cache_capacity": 1000,
            "in_memory_duplicate_count": self._quality_state._quality_duplicate_count,
            "persistent_duplicate_count": self._quality_state._persistent_duplicate_count,
            "accepted_count": self._quality_state._accepted_count,
            "low_information_rejected_count": self._quality_state._quality_rejected_count,
            "in_memory_duplicate_rejected_count": self._quality_state._quality_duplicate_count,
            "persistent_duplicate_rejected_count": self._quality_state._persistent_duplicate_count,
            "other_rejected_count": self._quality_state._quality_fail_open_count,
        }

    def reset_ingest_reason_counters(self) -> None:
        """
        Sprint 8AV: Reset all ingest outcome counters to zero.

        Side-effect free, test-safe, can be called any time.
        Resets all counters on QualityAssessmentState.
        """
        self._quality_state._accepted_count = 0
        self._quality_state._quality_rejected_count = 0
        self._quality_state._quality_duplicate_count = 0
        self._quality_state._persistent_duplicate_count = 0
        self._quality_state._quality_fail_open_count = 0

    def classify_ingest_outcome(self, decision: FindingQualityDecision | ActivationResult) -> str:
        """
        Sprint 8AV: Classify the canonical reason string for an ingest outcome.

        Internal use - maps internal FindingQualityDecision or ActivationResult
        to a human-readable reason string.

        Returns one of:
          - "accepted"                          - finding passed quality gate
          - "low_information_rejected"         - entropy below threshold
          - "in_memory_duplicate_rejected"     - hot-cache duplicate
          - "persistent_duplicate_rejected"   - LMDB cross-source duplicate
          - "other_rejected"                   - fail-open or unknown
          - "error_rejected"                   - store/LMDB error
        """
        if isinstance(decision, FindingQualityDecision):
            if decision.accepted:
                return "accepted"
            reason = decision.reason
            if reason == "low_entropy_rejected":
                return "low_information_rejected"
            if reason == "persistent_duplicate":
                return "persistent_duplicate_rejected"
            if reason == "duplicate_detected":
                return "in_memory_duplicate_rejected"
            return "other_rejected"
        if decision["accepted"]:
            return "accepted"
        error = decision.get("error")
        if error:
            return "error_rejected"
        return "other_rejected"

    def _schedule_graph_update(self, accepted_findings: list[CanonicalFinding]) -> None:
        """
        Fire graph update as non-blocking asyncio task (Python 3.10+ safe).

        Sprint F241: Writes accepted findings to DuckPGQGraph for cross-sprint
        entity accumulation. Graph is ADVISORY ONLY - failures are silently
        swallowed.

        Sprint F-CLEAN fix: replaced `asyncio.coroutine(_graph_update_task)()`
        (removed in Python 3.11) with the modern `async def` +
        `loop.run_in_executor()` pattern. M1 EIGHTGB safe - DuckDB sync ops run
        in the default ThreadPoolExecutor, not a separate process. Bounded
        by `_MAX_INFLIGHT_GRAPH_UPDATES` via the existing `self._bg_tasks`
        set (Sprint 8QA), auto-drained on completion.

        Sync context (no running event loop - tests / sync CLI / F8H worker
        threads) is a no-op; the graph update is advisory and not required
        for correctness.

        LAZY IMPORT: graph_store accessed here to avoid circular deps
        with duckdb_store.
        """
        try:
            def _sync_graph_update() -> None:
                try:
                    truth_graph = self._ensure_graph_attachment().get_truth_write_graph()
                    if truth_graph is None:
                        return
                    # CanonicalFinding has no ioc_value/ioc_type fields.
                    # IOCs are already extracted via Rust batch extraction above (lines 7848-7886)
                    # and buffered via parallel buffer_ioc() calls. That is the authoritative path.
                    # This fallback path uses payload_text for any findings that arrived here
                    # without going through the batch path (e.g. direct accepted_findings injection).
                    rows = []
                    for f in accepted_findings:
                        text = f.payload_text or f.query or ""
                        if text:
                            # Synchronous single-text extraction as fallback for non-Rust path
                            try:
                                import hledac.universal.rust_extensions as _re
                                if hasattr(_re, "extract_iocs_flat"):
                                    extracted = _re.extract_iocs_flat(text)
                                    for ioc_val, ioc_type in extracted:
                                        rows.append((ioc_val, ioc_type, float(f.confidence), f.source_type or ""))
                            except Exception:
                                pass
                    if rows:
                        truth_graph.upsert_ioc_batch(rows)
                except Exception:  # noqa: BLE001 — best-effort; IOC extraction failure; non-critical
                    pass

            tasks = getattr(self, "_bg_tasks", None)
            if tasks is None:
                return
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return

            async def _graph_update_coro() -> None:
                await asyncio.to_thread(_sync_graph_update)

            if tasks.count >= _MAX_INFLIGHT_GRAPH_UPDATES:
                return
            tasks.spawn(_graph_update_coro(), name="duckdb:_schedule_graph_update")
        except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
            pass

    async def _maintenance_loop(self) -> None:
        """
        RES-03: Unified background maintenance loop for DuckDB VACUUM and CHECKPOINT.

        Replaces the old _checkpoint_loop (hardcoded 300s). This loop:
        - Checks time-based VACUUM trigger (every 1h by default)
        - Checks time-based CHECKPOINT trigger (every 30min by default)
        - Handles LMDB compaction via unified store (RES-04)
        - Also triggered opportunistically after writes via _maybe_auto_maintenance

        duckdb_autocheckpoint=51200 provides a secondary safety valve between runs.
        Fail-safe: any error is silently caught and logged.
        Only active for file mode; _checkpoint_task is None for :memory: mode.
        """
        _logger = logging.getLogger(__name__)
        _now = _time.monotonic()
        self._last_vacuum_time = _now
        self._last_checkpoint_time = _now
        self._last_lmdb_compact_time = _now
        _LMDB_COMPACT_INTERVAL = 7200.0  # RES-04: LMDB compact every 2h

        while True:
            try:
                # RES-03: Use configured interval instead of hardcoded 300s
                _check_interval = min(self._vacuum_interval_seconds, self._checkpoint_interval_seconds, _LMDB_COMPACT_INTERVAL)
                await asyncio.sleep(min(_check_interval, 300))  # Cap at 300s to stay responsive
                if self._closed:
                    break
                if self._file_conn is None:
                    continue

                current_time = _time.monotonic()

                # RES-04: LMDB compaction — reclaim deleted pages from unified store
                if (current_time - self._last_lmdb_compact_time) >= _LMDB_COMPACT_INTERVAL:
                    try:
                        if hasattr(self, "_wal_lmdb") and self._wal_lmdb is not None:
                            if hasattr(self._wal_lmdb, "compact_database"):
                                compact_ok = self._wal_lmdb.compact_database()
                                if compact_ok:
                                    _logger.info("[RES-04] LMDB compaction succeeded")
                                else:
                                    _logger.debug("[RES-04] LMDB compaction skipped or failed")
                    except Exception as e:  # noqa: BLE001 — best-effort; LMDB compaction failure; non-critical
                        _logger.debug(f"[RES-04] LMDB compact error: {e}")
                    self._last_lmdb_compact_time = current_time

                # Time-based CHECKPOINT (DuckDB WAL → main file)
                if (current_time - self._last_checkpoint_time) >= self._checkpoint_interval_seconds:
                    try:
                        self._file_conn.execute("PRAGMA checkpoint")
                        self._file_conn.execute("ANALYZE")
                        self._last_checkpoint_time = current_time
                        _logger.debug("[RES-03] CHECKPOINT triggered by time interval")
                    except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                        _logger.debug(f"[RES-03] CHECKPOINT error: {e}")

                # Time-based VACUUM (reclaim deleted pages, shrink file)
                if (current_time - self._last_vacuum_time) >= self._vacuum_interval_seconds:
                    try:
                        _vac_loop = asyncio.get_running_loop()
                        await _vac_loop.run_in_executor(self._executor, self._vacuum_sync)
                        self._last_vacuum_time = current_time
                        _logger.info("[RES-03] VACUUM triggered by time interval")
                    except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                        _logger.debug(f"[RES-03] VACUUM error: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
                _logger.debug(f"[RES-03] maintenance loop error: {e}")

    # ── FTS5: Full-Text Search ────────────────────────────────────────────────

    async def fts_search_findings(
        self,
        query: str,
        k: int = 20,
        min_ts: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        FTS5 full-text search over canonical_findings.payload_text.

        Uses DuckDB FTS5 extension for keyword/phrase search with ranking.
        Falls back to LIKE-based search if FTS5 is unavailable.

        Args:
            query: FTS5 query string (supports AND, OR, phrase "quoted", prefix*)
            k: Number of results to return (default 20)
            min_ts: Optional minimum timestamp filter

        Returns:
            List of dicts: {chunk_id, content, score, source_type, ts}
        """
        await self.async_initialize_schema()
        self.ensure_connected()

        conn = self._conn
        if conn is None:
            return []

        try:
            # Try DuckDB FTS5 MATCH first
            if min_ts is not None:
                sql = """
                    SELECT
                        cf.id,
                        cf.payload_text,
                        cf.source_type,
                        cf.ts,
                        fts.rank,
                        fts.score
                    FROM findings_fts fts
                    JOIN canonical_findings cf ON cf.rowid = fts.fts_rowid
                    WHERE fts.query MATCH ?
                      AND fts.ts >= ?
                    ORDER BY fts.rank ASC
                    LIMIT ?
                """
                rows = await asyncio.to_thread(
                    conn.execute, sql, [query, min_ts, k]
                ).fetchall()
            else:
                sql = """
                    SELECT
                        cf.id,
                        cf.payload_text,
                        cf.source_type,
                        cf.ts,
                        fts.rank,
                        fts.score
                    FROM findings_fts fts
                    JOIN canonical_findings cf ON cf.rowid = fts.fts_rowid
                    WHERE fts.query MATCH ?
                    ORDER BY fts.rank ASC
                    LIMIT ?
                """
                rows = await asyncio.to_thread(
                    conn.execute, sql, [query, k]
                ).fetchall()

            return [
                {
                    "chunk_id": str(r[0]),
                    "content": r[1] or "",
                    "source_type": r[2],
                    "ts": r[3],
                    "rank": r[4],
                    "score": r[5] if len(r) > 5 else 0.0,
                }
                for r in rows
            ]

        except Exception as e:  # noqa: BLE001 — FTS5 unavailable or query error
            _logger.debug(f"[DUCKDB:FTS] FTS5 search failed, falling back to LIKE: {e}")
            # Fallback: LIKE-based search
            return await self._fts_fallback_search(query, k, min_ts)

    async def _fts_fallback_search(
        self,
        query: str,
        k: int = 20,
        min_ts: float | None = None,
    ) -> list[dict[str, Any]]:
        """Fallback FTS using LIKE when FTS5 extension is unavailable."""
        conn = self._conn
        if conn is None:
            return []

        try:
            terms = query.strip().split()
            like_pattern = "%" + "%".join(terms) + "%"

            if min_ts is not None:
                sql = """
                    SELECT id, payload_text, source_type, ts
                    FROM canonical_findings
                    WHERE payload_text LIKE ?
                      AND ts >= ?
                    ORDER BY ts DESC
                    LIMIT ?
                """
                rows = await asyncio.to_thread(
                    conn.execute, sql, [like_pattern, min_ts, k]
                ).fetchall()
            else:
                sql = """
                    SELECT id, payload_text, source_type, ts
                    FROM canonical_findings
                    WHERE payload_text LIKE ?
                    ORDER BY ts DESC
                    LIMIT ?
                """
                rows = await asyncio.to_thread(
                    conn.execute, sql, [like_pattern, k]
                ).fetchall()

            return [
                {
                    "chunk_id": str(r[0]),
                    "content": r[1] or "",
                    "source_type": r[2],
                    "ts": r[3],
                    "rank": idx,
                    "score": 1.0,
                }
                for idx, r in enumerate(rows)
            ]
        except Exception as e:  # noqa: BLE001
            _logger.debug(f"[DUCKDB:FTS] LIKE fallback failed: {e}")
            return []

    # ── FTS5: Entity observations ─────────────────────────────────────────────

    async def fts_search_entities(
        self,
        query: str,
        k: int = 20,
        entity_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        FTS5 full-text search over entity_observations.entity_value.

        Args:
            query: FTS5 query string
            k: Number of results (default 20)
            entity_type: Optional entity type filter (e.g., 'domain', 'ipv4')

        Returns:
            List of dicts: {entity_value, entity_type, sprint_id, ts, rank}
        """
        await self.async_initialize_schema()
        self.ensure_connected()

        conn = self._conn
        if conn is None:
            return []

        try:
            if entity_type is not None:
                sql = """
                    SELECT
                        eo.entity_value,
                        eo.entity_type,
                        eo.sprint_id,
                        eo.ts,
                        eft.rank
                    FROM entity_fts eft
                    JOIN entity_observations eo ON eo.rowid = eft.fts_rowid
                    WHERE eft.entity_value MATCH ?
                      AND eo.entity_type = ?
                    ORDER BY eft.rank ASC
                    LIMIT ?
                """
                rows = await asyncio.to_thread(
                    conn.execute, sql, [query, entity_type, k]
                ).fetchall()
            else:
                sql = """
                    SELECT
                        eo.entity_value,
                        eo.entity_type,
                        eo.sprint_id,
                        eo.ts,
                        eft.rank
                    FROM entity_fts eft
                    JOIN entity_observations eo ON eo.rowid = eft.fts_rowid
                    WHERE eft.entity_value MATCH ?
                    ORDER BY eft.rank ASC
                    LIMIT ?
                """
                rows = await asyncio.to_thread(
                    conn.execute, sql, [query, k]
                ).fetchall()

            return [
                {
                    "entity_value": str(r[0]),
                    "entity_type": r[1],
                    "sprint_id": r[2],
                    "ts": r[3],
                    "rank": r[4],
                }
                for r in rows
            ]

        except Exception as e:  # noqa: BLE001
            _logger.debug(f"[DUCKDB:FTS] entity FTS search failed: {e}")
            return []

    # ── Vector: RAG Embeddings ─────────────────────────────────────────────────

    async def upsert_rag_embeddings(
        self,
        chunks: list[dict[str, Any]],
    ) -> int:
        """
        Batch upsert RAG document chunk embeddings.

        Stores in rag_embeddings table with LIST<FLOAT> vectors.
        Uses INSERT OR REPLACE for idempotent upserts.

        Args:
            chunks: List of dicts with keys:
                - chunk_id: str (primary key)
                - document_id: str
                - content: str
                - metadata: dict (serialized to JSON)
                - embedding: list[float] (384-dim)
                - created_at: float (unix timestamp)

        Returns:
            Number of chunks upserted.
        """
        if not chunks:
            return 0

        await self.async_initialize_schema()
        self.ensure_connected()

        conn = self._conn
        if conn is None:
            return 0

        rows_inserted = 0
        for chunk in chunks:
            try:
                embedding_list = chunk.get("embedding", [])
                metadata_json = _orjson_mod.dumps(chunk.get("metadata", {}))
                conn.execute(
                    """
                    INSERT OR REPLACE INTO rag_embeddings
                    (chunk_id, document_id, content, metadata_json, embedding, embedding_dim, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(chunk["chunk_id"]),
                        str(chunk["document_id"]),
                        str(chunk.get("content", "")),
                        metadata_json,
                        embedding_list,
                        len(embedding_list),
                        float(chunk.get("created_at", 0.0)),
                    ],
                )
                rows_inserted += 1
            except Exception as e:  # noqa: BLE001 — best-effort per chunk
                _logger.debug(f"[DUCKDB:VEC] upsert_rag_embeddings failed for {chunk.get('chunk_id','?')}: {e}")

        return rows_inserted

    async def vector_search_rag(
        self,
        query_vector: list[float],
        k: int = 10,
        document_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        ANN vector search over rag_embeddings using DuckDB HNSW.

        Uses array_cosine_distance with HNSW index (or sequential scan fallback).
        M1 8GB: bounded to k <= 100 to prevent runaway memory.

        Args:
            query_vector: 384-dim query embedding
            k: Number of results (default 10, max 100)
            document_id: Optional filter to specific document

        Returns:
            List of dicts: {chunk_id, document_id, content, metadata, distance}
        """
        await self.async_initialize_schema()
        self.ensure_connected()

        k = min(k, 100)  # M1 8GB safety cap
        conn = self._conn
        if conn is None:
            return []

        try:
            if document_id is not None:
                sql = """
                    SELECT
                        chunk_id,
                        document_id,
                        content,
                        metadata_json,
                        array_cosine_distance(embedding, ?) AS distance
                    FROM rag_embeddings
                    WHERE document_id = ?
                    ORDER BY distance ASC
                    LIMIT ?
                """
                rows = await asyncio.to_thread(
                    conn.execute, sql, [query_vector, document_id, k]
                ).fetchall()
            else:
                sql = """
                    SELECT
                        chunk_id,
                        document_id,
                        content,
                        metadata_json,
                        array_cosine_distance(embedding, ?) AS distance
                    FROM rag_embeddings
                    ORDER BY distance ASC
                    LIMIT ?
                """
                rows = await asyncio.to_thread(
                    conn.execute, sql, [query_vector, k]
                ).fetchall()

            return [
                {
                    "chunk_id": str(r[0]),
                    "document_id": str(r[1]),
                    "content": r[2] or "",
                    "metadata": _ORJSON_DECODER(r[3]) if r[3] else {},
                    "distance": float(r[4]) if r[4] is not None else 1.0,
                }
                for r in rows
            ]

        except Exception as e:  # noqa: BLE001 — HNSW unavailable or query error
            _logger.debug(f"[DUCKDB:VEC] vector_search_rag failed: {e}")
            return []

    async def vector_search_rag_mmr(
        self,
        query_vector: list[float],
        k: int = 10,
        fetch_k: int = 50,
        lambda_mult: float = 0.5,
    ) -> list[dict[str, Any]]:
        """
        ANN search with Maximal Marginal Relevance (MMR) diversity.

        Fetches fetch_k candidates, then reranks using MMR to balance
        relevance (cosine similarity) with document diversity.

        Args:
            query_vector: 384-dim query embedding
            k: Final number of results (after MMR, default 10)
            fetch_k: Number of candidates to fetch before reranking (default 50)
            lambda_mult: MMR diversity weight (0.0=all relevance, 1.0=all diversity)

        Returns:
            List of dicts: {chunk_id, document_id, content, distance}
        """
        await self.async_initialize_schema()
        self.ensure_connected()

        k = min(k, 100)
        fetch_k = min(fetch_k, 200)  # cap for M1 8GB

        # Fetch candidates
        candidates = await self.vector_search_rag(query_vector, k=fetch_k)
        if not candidates:
            return []

        if len(candidates) <= k:
            return candidates

        # MMR reranking
        try:
            from context_optimization.mmr import maximal_marginal_relevance

            # Convert to numpy arrays for MMR
            import numpy as np

            vectors = []
            ids = []
            for c in candidates:
                if c.get("embedding") is not None:
                    vectors.append(np.array(c["embedding"], dtype=np.float32))
                    ids.append(c["chunk_id"])
                elif c.get("distance") is not None:
                    vectors.append(
                        np.array(query_vector, dtype=np.float32) * (1.0 - c["distance"])
                    )
                    ids.append(c["chunk_id"])

            if not vectors:
                return candidates[:k]

            matrix = np.vstack(vectors)
            query_vec = np.array(query_vector, dtype=np.float32)

            # MMR indices
            mmr_indices = maximal_marginal_relevance(
                query_vec, matrix, k=k, lambda_mult=lambda_mult
            )

            return [candidates[i] for i in mmr_indices if i < len(candidates)]

        except Exception as e:  # noqa: BLE001
            _logger.debug(f"[DUCKDB:VEC] MMR reranking failed: {e}")
            return candidates[:k]

    # ── Vector: Entity Embeddings ──────────────────────────────────────────────

    async def upsert_entity_embeddings(
        self,
        entities: list[dict[str, Any]],
    ) -> int:
        """
        Batch upsert entity embeddings for identity resolution.

        Args:
            entities: List of dicts with keys:
                - entity_id: str (primary key)
                - entity_value: str
                - entity_type: str (e.g., 'domain', 'ipv4', 'email')
                - metadata: dict
                - embedding: list[float] (384-dim)
                - updated_at: float (unix timestamp)

        Returns:
            Number of entities upserted.
        """
        if not entities:
            return 0

        await self.async_initialize_schema()
        self.ensure_connected()

        conn = self._conn
        if conn is None:
            return 0

        rows_inserted = 0
        for entity in entities:
            try:
                embedding_list = entity.get("embedding", [])
                metadata_json = _orjson_mod.dumps(entity.get("metadata", {}))
                conn.execute(
                    """
                    INSERT OR REPLACE INTO entity_embeddings
                    (entity_id, entity_value, entity_type, metadata_json, embedding, embedding_dim, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(entity["entity_id"]),
                        str(entity["entity_value"]),
                        str(entity.get("entity_type", "")),
                        metadata_json,
                        embedding_list,
                        len(embedding_list),
                        float(entity.get("updated_at", 0.0)),
                    ],
                )
                rows_inserted += 1
            except Exception as e:  # noqa: BLE001
                _logger.debug(f"[DUCKDB:VEC] upsert_entity_embeddings failed for {entity.get('entity_id','?')}: {e}")

        return rows_inserted

    async def vector_search_entities(
        self,
        query_vector: list[float],
        k: int = 10,
        entity_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        ANN vector search over entity_embeddings.

        Used for entity identity clustering and alias resolution.

        Args:
            query_vector: 384-dim query embedding
            k: Number of results (default 10, max 100)
            entity_type: Optional entity type filter

        Returns:
            List of dicts: {entity_id, entity_value, entity_type, metadata, distance}
        """
        await self.async_initialize_schema()
        self.ensure_connected()

        k = min(k, 100)
        conn = self._conn
        if conn is None:
            return []

        try:
            if entity_type is not None:
                sql = """
                    SELECT
                        entity_id,
                        entity_value,
                        entity_type,
                        metadata_json,
                        array_cosine_distance(embedding, ?) AS distance
                    FROM entity_embeddings
                    WHERE entity_type = ?
                    ORDER BY distance ASC
                    LIMIT ?
                """
                rows = await asyncio.to_thread(
                    conn.execute, sql, [query_vector, entity_type, k]
                ).fetchall()
            else:
                sql = """
                    SELECT
                        entity_id,
                        entity_value,
                        entity_type,
                        metadata_json,
                        array_cosine_distance(embedding, ?) AS distance
                    FROM entity_embeddings
                    ORDER BY distance ASC
                    LIMIT ?
                """
                rows = await asyncio.to_thread(
                    conn.execute, sql, [query_vector, k]
                ).fetchall()

            return [
                {
                    "entity_id": str(r[0]),
                    "entity_value": str(r[1]),
                    "entity_type": r[2],
                    "metadata": _ORJSON_DECODER(r[3]) if r[3] else {},
                    "distance": float(r[4]) if r[4] is not None else 1.0,
                }
                for r in rows
            ]

        except Exception as e:  # noqa: BLE001
            _logger.debug(f"[DUCKDB:VEC] vector_search_entities failed: {e}")
            return []

    # ── Hybrid: FTS + Vector ───────────────────────────────────────────────────

    async def hybrid_search_rag(
        self,
        query_text: str,
        query_vector: list[float],
        k: int = 10,
        fts_weight: float = 0.4,
        vec_weight: float = 0.6,
    ) -> list[dict[str, Any]]:
        """
        Hybrid search combining FTS5 and vector ANN for RAG.

        Fetches fts_k candidates and vec_k candidates, then merges
        using Reciprocal Rank Fusion (RRF).

        Args:
            query_text: Text query for FTS5
            query_vector: 384-dim embedding vector
            k: Final number of results (default 10)
            fts_weight: FTS contribution weight (default 0.4)
            vec_weight: Vector contribution weight (default 0.6)

        Returns:
            List of dicts: {chunk_id, content, fts_score, vec_score, rrf_score}
        """
        await self.async_initialize_schema()
        self.ensure_connected()

        k_actual = min(k, 100)
        fts_k = min(k_actual * 2, 50)
        vec_k = min(k_actual * 2, 50)

        # Parallel fetch — F350M-R: parallel() replaces asyncio.gather
        fts_task = self.fts_search_findings(query_text, k=fts_k)
        vec_task = self.vector_search_rag(query_vector, k=vec_k)

        hybrid_results = await parallel(
            [fts_task, vec_task],
            policy="collect",
            concurrency=2,
            ctx="duckdb_store:hybrid_search",
        )
        fts_results = hybrid_results[0] if len(hybrid_results) > 0 else []
        vec_results = hybrid_results[1] if len(hybrid_results) > 1 else []

        if not fts_results and not vec_results:
            return []

        # Build candidate set
        candidates: dict[str, dict[str, Any]] = {}
        for r in fts_results:
            cid = r["chunk_id"]
            candidates[cid] = {
                "chunk_id": cid,
                "content": r["content"],
                "source_type": r.get("source_type"),
                "ts": r.get("ts"),
                "fts_rank": r.get("rank", 0),
                "fts_score": 1.0 / (r.get("rank", 0) + 1),
                "vec_rank": None,
                "vec_score": 0.0,
                "vec_distance": None,
            }
        for r in vec_results:
            cid = r["chunk_id"]
            if cid in candidates:
                candidates[cid]["vec_rank"] = r.get("rank", 0)
                candidates[cid]["vec_score"] = 1.0 / (r.get("rank", 0) + 1)
                candidates[cid]["vec_distance"] = r.get("distance")
            else:
                candidates[cid] = {
                    "chunk_id": cid,
                    "content": r["content"],
                    "source_type": None,
                    "ts": None,
                    "fts_rank": None,
                    "fts_score": 0.0,
                    "vec_rank": r.get("rank", 0),
                    "vec_score": 1.0 / (r.get("rank", 0) + 1),
                    "vec_distance": r.get("distance"),
                }

        # Reciprocal Rank Fusion
        rrf_k = 60  # standard RRF parameter
        for cand in candidates.values():
            rrf = 0.0
            if cand["fts_score"] > 0:
                rrf += fts_weight * (1.0 / (rrf_k + cand["fts_rank"]))
            if cand["vec_score"] > 0:
                rrf += vec_weight * (1.0 / (rrf_k + cand["vec_rank"]))
            cand["rrf_score"] = rrf

        sorted_candidates = sorted(
            candidates.values(), key=lambda x: x["rrf_score"], reverse=True
        )

        return sorted_candidates[:k_actual]


def create_owned_store() -> DuckDBShadowStore:
    """
    Sprint 8AM C.3.a: Create an owned DuckDBShadowStore instance.

    Uses paths.py SSOT for RAMDisk-aware path resolution.
    RAMDISK_ACTIVE=True: db at DUCKDB_STORE_ROOT, temp at RAMDISK_ROOT/duckdb_tmp
    RAMDISK_ACTIVE=False: degraded :memory: fallback

    This is the ONE place in main.py where DuckDBShadowStore is instantiated
    for the owned runtime path. Avoids coupling __main__.py to DuckDBShadowStore
    internals.

    Returns:
        DuckDBShadowStore: initialized store ready for async_initialize()
    """
    try:
        from hledac.universal.paths import DUCKDB_STORE_ROOT, RAMDISK_ACTIVE, RAMDISK_ROOT

        if RAMDISK_ACTIVE:
            db_path = DUCKDB_STORE_ROOT / "shadow_analytics.duckdb"
            temp_dir = RAMDISK_ROOT / "duckdb_tmp"
            return DuckDBShadowStore(db_path=db_path, temp_dir=temp_dir)
        else:
            return DuckDBShadowStore()
    except Exception:  # noqa: BLE001 — best-effort; DuckDB operation failure; non-critical
        return DuckDBShadowStore()


# ── S-04: DuckDB Shadow Store Factory (replaces duckdb_subprocess_adapter.py) ─────
# STORAGE-DUP-003: Single in-process path only. Subprocess isolation removed.
#
# DuckDBSubprocessAdapter is absorbed here. The adapter was a pure wrapper that
# delegated to DuckDBShadowStore — no subprocess isolation benefit on M1 8GB UMA.
# Use make_shadow_store() instead of direct DuckDBShadowStore() instantiation for
# all canonical sprint paths.


def make_shadow_store(
    db_path: "Path | str | None" = None,
    temp_dir: "Path | str | None" = None,
    uma_state: "str | None" = None,
) -> DuckDBShadowStore:
    """
    Factory: create DuckDBShadowStore for canonical sprint paths.

    Replaces DuckDBSubprocessAdapter (STORAGE-DUP-003 absorbed).

    M1 8GB: always in-process via DuckDBShadowStore (Arrow zero-copy, WAL, internal batching).
    Subprocess mode offers no RAM benefit on UMA architecture.

    Args:
        db_path: Optional explicit DB path. If None, resolves via paths.py RAMDisk convention.
        temp_dir: Optional explicit temp directory. If None, resolves via paths.py.
        uma_state: Optional UMA state hint passed to DuckDBShadowStore.

    Returns:
        DuckDBShadowStore: initialized in-process store (not a wrapper).
    """
    from pathlib import Path

    _db_path: "Path | None" = Path(db_path) if db_path is not None else None
    _temp_dir: "Path | None" = Path(temp_dir) if temp_dir is not None else None

    # Resolve defaults if not provided — mirrors DuckDBSubprocessAdapter._resolve_path()
    if _db_path is None:
        try:
            from hledac.universal.paths import DUCKDB_STORE_ROOT, RAMDISK_ACTIVE, RAMDISK_ROOT

            if RAMDISK_ACTIVE:
                _db_path = DUCKDB_STORE_ROOT / "shadow_analytics.duckdb"
                _temp_dir = RAMDISK_ROOT / "duckdb_tmp"
            else:
                _db_path = DUCKDB_STORE_ROOT / "analytics.duckdb"
        except Exception:  # noqa: BLE001 — degraded; will use :memory:
            pass

    return DuckDBShadowStore(
        db_path=_db_path,
        temp_dir=_temp_dir,
        uma_state=uma_state,
        lazy=False,
    )
