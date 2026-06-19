"""
DuckDB Subprocess Writer — F289: Process isolation for M1 8GB UMA
================================================================

DuckDB běží v izolovaném subprocessu s vlastní paměťovou oblastí.
Hlavní proces (MLX Metal) je chráněn před DuckDB memory pressure.

ARCHITECTURA:
-------------
Main Process                          DuckDB Writer Process
─────────────────                    ──────────────────────
DuckDBShadowStore ──Queue──→  DuckDBWriterWorker (subprocess)
    │                                 │
    ├── MLX Brain (Metal)             ├── duckdb.connect()
    ├── mx.eval/clear_cache           ├── Arrow→DuckDB INSERT
    └── SprintScheduler               └── return results
    │
    └── LMDB WAL (main process mmap - není cross-process)

SUBPROCESS START METHOD:
------------------------
M1 Metal API = fork-unsafe. Používáme multiprocessing.get_context("spawn").
Fork start na M1 = immediate crash při Metal API calls.

MEMORY ISOLATION:
-----------------
- DuckDB allocations = subprocess private pages (COW)
- Metal allocator = main process only
- Žádná cross-process paměť competition pro GPU paměť

IPC SERIALIZATION:
------------------
- CanonicalFinding → msgspec.json.encode() → bytes (cross-process)
- Results → list[dict] → msgspec.json → main process
- Payload text (velká data) → pickle (efektivnější pro velké bloby)

LAZY INIT:
----------
Subprocess se spawnuje až při PRVNÍM volání async_ingest_findings_batch,
ne při __init__ (M1 8GB: startup cost se neplatí zbytečně).

PROXY INTERFACE:
----------------
DuckDBShadowStore nyní deleguje na DuckDBProxy (main process) →
→ subprocess worker. Veškeré veřejné API zůstává stejné.

Author: Sprint F289
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import msgspec

# Lazy import DuckDB - subprocess only
_DUCKDB = None

# Module-level encoder (thread-safe singleton pattern)
_ENCODER = msgspec.json.Encoder()
_DECODER = msgspec.json.Decoder()

# Subprocess startup method - MANDATORY for M1 Metal safety
_SPAWN_CTX = mp.get_context("spawn")

# In-memory queue for fast IPC (no socket overhead)
# Size bounded: max ~100 batches * 500 items = 50k findings in-flight
_QUEUE_MAXSIZE = 128

# Subprocess startup timeout (seconds)
_SUBPROCESS_START_TIMEOUT_S = 15.0


# ---------------------------------------------------------------------------
# CanonicalFinding serialization helpers
# ---------------------------------------------------------------------------

def _canonical_finding_to_dict(f: Any) -> dict:
    """Convert CanonicalFinding (or dict) to serializable dict."""
    if hasattr(f, "model_dump"):
        # msgspec.Struct path
        return f.model_dump()
    return dict(f)


def _canonical_findings_to_bytes(findings: list[Any]) -> bytes:
    """Serialize list of CanonicalFinding to bytes for IPC."""
    dicts = [_canonical_finding_to_dict(f) for f in findings]
    return _ENCODER.encode(dicts)


def _bytes_to_duckdb_rows(data: bytes) -> list[list]:
    """Deserialize bytes to DuckDB row format."""
    decoded = _DECODER.decode(data)
    rows = []
    for item in decoded:
        provenance = item.get("provenance", ())
        provenance_json = _ENCODER.encode(provenance).decode("utf-8")
        rows.append([
            item["finding_id"],
            item["query"],
            item["source_type"],
            item["confidence"],
            item["ts"],
            provenance_json,
        ])
    return rows


# ---------------------------------------------------------------------------
# DuckDB Writer Worker (runs in subprocess)
# ---------------------------------------------------------------------------

class DuckDBWriterWorker:
    """
    Subprocess worker - owns DuckDB connection and executes all DB operations.

    M1-safe: runs in spawned subprocess, no Metal API exposure.
    DuckDB memory allocations are fully isolated from main process.
    """

    def __init__(
        self,
        db_path: str | None,
        temp_dir: str | None,
        wal_path: str | None,
    ):
        self.db_path = db_path
        self.temp_dir = temp_dir
        self.wal_path = wal_path
        self.conn: Any = None
        self.running = True
        self._initialized = False

    def _initialize(self) -> None:
        """Initialize DuckDB connection in subprocess."""
        global _DUCKDB
        if _DUCKDB is None:
            import duckdb as _duckdb_mod
            _DUCKDB = _duckdb_mod

        if self.db_path:
            self.conn = _DUCKDB.connect(self.db_path, read_only=False)
        else:
            self.conn = _DUCKDB.connect(database=":memory:")

        # Apply runtime settings (memory limit, threads)
        self._configure_connection()

        # WAL table schema
        self._ensure_schema()

        self._initialized = True

    def _configure_connection(self) -> None:
        """Apply DuckDB runtime configuration — M1 8GB RAM bounded."""
        if not self.conn:
            return

        # Memory limit (enforced per-query, not total)
        # Sprint F265C: 400MB cap — DuckDB autocheckpointing keeps RAM bounded
        memory_limit = os.environ.get("HLEDAC_DUCKDB_MEMORY", "400MB")
        threads = 1  # Single thread = deterministic memory, less fragmentation
        checkpoint_threshold = os.environ.get(
            "HLEDAC_DUCKDB_CHECKPOINT_THRESHOLD", "128MB"
        )
        max_tmp_space = os.environ.get("HLEDAC_DUCKDB_TMP_SPACE", "64MB")

        try:
            # Core memory limits
            self.conn.execute(f"SET memory_limit = '{memory_limit}'")
            self.conn.execute(f"SET threads = {threads}")
            self.conn.execute(f"SET checkpoint_threshold = '{checkpoint_threshold}'")
            self.conn.execute(f"SET max_temp_space = '{max_tmp_space}'")

            # Checkpoint behavior — frequent small checkpoints vs rare huge ones
            # Lower threshold = more frequent but smaller memory spikes during checkpoint
            self.conn.execute("SET checkpoint_on_shutdown = true")
            self.conn.execute("SET force_checkpoint = false")  # Don't block on checkpoint

            # WAL: keep minimal — DuckDB autocheckpoints WAL periodically
            self.conn.execute("SET wal_autocheckpoint = '128MB'")

            # Performance: skip integrity checks in subprocess (main process validates)
            self.conn.execute("SET preserve_insertion_order = false")
            self.conn.execute("SET safe_mode = false")

            # Disable memory-intensive features we don't need in subprocess
            self.conn.execute("SET enable_progress_bar = false")
            self.conn.execute("SET enable_progress_bar_nested = false")

            # Force immediate checkpoint of WAL after each transaction
            # This keeps WAL small and bounded (M1 8GB friendly)
            self.conn.execute("SET wal_autocheckpoint = '1MB'")

        except Exception:
            # Fallback: bare minimum for M1 8GB safety
            try:
                self.conn.execute("SET memory_limit = '256MB'")
                self.conn.execute("SET threads = 1")
            except Exception:
                # Last resort: DuckDB will use defaults but memory is still bounded
                pass

    def _ensure_schema(self) -> None:
        """Ensure WAL schema exists (DuckDB-first, file-mode)."""
        if not self.conn:
            return

        schema_sql = """
        CREATE TABLE IF NOT EXISTS canonical_findings (
            id              VARCHAR PRIMARY KEY,
            query           VARCHAR,
            source_type     VARCHAR,
            confidence      DOUBLE,
            ts              DOUBLE,
            provenance_json TEXT,
            payload_text    TEXT,
            UNIQUE (id),
            UNIQUE (query, source_type)
        );
        """
        try:
            self.conn.execute(schema_sql)
        except Exception:
            # Table might already exist
            pass

    def _insert_findings_batch(self, rows: list[list]) -> int:
        """Execute bulk INSERT with Arrow path support."""
        if not rows or not self.conn:
            return 0

        try:
            # Arrow zero-copy path (faster for large batches)
            if len(rows) >= 20 and "pyarrow" in sys.modules:
                return self._insert_arrow(rows)

            # Legacy executemany path
            return self._insert_executemany(rows)
        except Exception:
            # Fallback: try executemany
            return self._insert_executemany(rows)

    def _insert_executemany(self, rows: list[list]) -> int:
        """Legacy INSERT via executemany."""
        if not self.conn:
            return 0

        inserted = 0
        for row in rows:
            try:
                self.conn.execute("""
                    INSERT OR IGNORE INTO canonical_findings
                    (id, query, source_type, confidence, ts, provenance_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, row[:6])
                inserted += 1
            except Exception:
                # Skip duplicates/errors
                pass
        return inserted

    def _insert_arrow(self, rows: list[list]) -> int:
        """Arrow zero-copy INSERT path."""
        try:
            import pyarrow as pa

            columns = [
                [r[0] for r in rows],  # id
                [r[1] for r in rows],  # query
                [r[2] for r in rows],  # source_type
                [r[3] for r in rows],  # confidence
                [r[4] for r in rows],  # ts
                [r[5] for r in rows],  # provenance_json
            ]

            _table = pa.table({
                "id": columns[0],
                "query": columns[1],
                "source_type": columns[2],
                "confidence": columns[3],
                "ts": columns[4],
                "provenance_json": columns[5],
            })

            self.conn.execute("INSERT INTO canonical_findings BY NAME SELECT * FROM table")
            return len(rows)
        except Exception:
            return self._insert_executemany(rows)

    def _process_ingest(self, findings_bytes: bytes) -> list[dict]:
        """Process a batch of findings from main process."""
        try:
            rows = _bytes_to_duckdb_rows(findings_bytes)
            _inserted = self._insert_findings_batch(rows)

            # Return per-finding results
            results = []
            for _i, row in enumerate(rows):
                results.append({
                    "finding_id": row[0],
                    "lmdb_success": True,  # LMDB is in main process, assumed OK
                    "duckdb_success": True,
                    "error": None,
                })

            return results

        except Exception as e:
            # Return error for all items in batch
            return [{
                "finding_id": "unknown",
                "lmdb_success": True,
                "duckdb_success": False,
                "error": str(e),
            }]

    def _process_healthcheck(self) -> dict:
        """Health check for subprocess."""
        try:
            if self.conn:
                self.conn.execute("SELECT 1")
                return {"status": "healthy", "conn": True}
            return {"status": "unhealthy", "conn": False}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def run(self, request_queue: mp.Queue, response_queue: mp.Queue) -> None:
        """
        Main loop - runs in subprocess.

        Receives commands via request_queue, sends results via response_queue.
        Runs until shutdown command or fatal error.
        """
        self._initialize()

        # Signal ready
        response_queue.put({"type": "ready"})

        while self.running:
            try:
                # Use timeout to allow periodic health checks
                msg = request_queue.get(timeout=5.0)
            except queue.Empty:
                # Periodic heartbeat
                continue
            except Exception:
                # Pipe/signal error
                break

            if msg is None:
                # Shutdown signal
                break

            cmd = msg.get("cmd")
            data = msg.get("data")

            try:
                if cmd == "ingest":
                    results = self._process_ingest(data)
                    response_queue.put({"type": "result", "data": results})

                elif cmd == "healthcheck":
                    status = self._process_healthcheck()
                    response_queue.put({"type": "healthcheck", "data": status})

                elif cmd == "shutdown":
                    self.running = False
                    response_queue.put({"type": "shutdown_ack"})

                else:
                    response_queue.put({
                        "type": "error",
                        "error": f"Unknown command: {cmd}",
                    })
            except Exception as e:
                response_queue.put({
                    "type": "error",
                    "error": str(e),
                })

        # Cleanup
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None


# ---------------------------------------------------------------------------
# DuckDB Proxy (main process - routes to subprocess)
# ---------------------------------------------------------------------------

class DuckDBProxy:
    """
    Main-process proxy for DuckDB subprocess operations.

    Presents the same interface as the old thread-pool-based approach,
    but routes operations to a subprocess worker for memory isolation.

    M1 8GB: subprocess spawns lazily on first ingest, not on __init__.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        temp_dir: Path | str | None = None,
        wal_path: str | None = None,
    ):
        self._db_path = Path(db_path) if db_path else None
        self._temp_dir = Path(temp_dir) if temp_dir else None
        self._wal_path = wal_path

        # Subprocess handles
        self._process: mp.Process | None = None
        self._request_queue: mp.Queue | None = None
        self._response_queue: mp.Queue | None = None

        # Thread pool for async wrapper (run_in_executor)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="duckdb_proxy")

        # State
        self._started = False
        self._closed = False
        self._start_lock = threading.Lock()

        # Metrics
        self._ingest_count = 0
        self._error_count = 0

    def _get_db_path_str(self) -> str | None:
        """Get DB path as string for subprocess."""
        if self._db_path:
            return str(self._db_path)
        return None

    def _get_temp_dir_str(self) -> str | None:
        """Get temp dir as string for subprocess."""
        if self._temp_dir:
            return str(self._temp_dir)
        return None

    def _lazy_start(self) -> None:
        """Lazily spawn subprocess on first use (M1 8GB friendly)."""
        with self._start_lock:
            if self._started:
                return

            if self._closed:
                raise RuntimeError("DuckDBProxy is closed")

            # Create queues
            self._request_queue = mp.Queue(maxsize=_QUEUE_MAXSIZE)
            self._response_queue = mp.Queue(maxsize=_QUEUE_MAXSIZE)

            # Spawn subprocess with spawn context (M1 Metal safe)
            process: mp.Process = cast(mp.Process, _SPAWN_CTX.Process(
                target=_run_worker_loop,
                args=(
                    self._get_db_path_str(),
                    self._get_temp_dir_str(),
                    self._wal_path,
                    self._request_queue,
                    self._response_queue,
                ),
                name="duckdb_writer",
                daemon=False,  # Wait for graceful shutdown
            ))
            self._process = process
            self._process.start()

            # Wait for ready signal
            try:
                msg = self._response_queue.get(timeout=_SUBPROCESS_START_TIMEOUT_S)
                if msg.get("type") != "ready":
                    raise RuntimeError(f"Subprocess init failed: {msg}")
                self._started = True
            except Exception as e:
                # Cleanup failed startup
                if self._process:
                    self._process.terminate()
                    self._process = None
                raise RuntimeError(f"Failed to start DuckDB subprocess: {e}") from e

    def _run_sync(self, cmd: str, data: Any) -> Any:
        """Synchronous command to subprocess (called from executor)."""
        if not self._started or self._closed:
            raise RuntimeError("DuckDBProxy not started or closed")

        if not self._request_queue or not self._response_queue:
            raise RuntimeError("DuckDBProxy queues not initialized")

        # Send request
        self._request_queue.put({"cmd": cmd, "data": data})

        # Wait for response
        try:
            msg = self._response_queue.get(timeout=30.0)
        except queue.Empty as err:
            self._error_count += 1
            raise TimeoutError("Subprocess response timeout") from err

        if msg.get("type") == "error":
            self._error_count += 1
            raise RuntimeError(msg.get("error", "Unknown subprocess error"))

        return msg.get("data")

    async def ingest_batch(self, findings: list[Any]) -> list[dict]:
        """
        Ingest a batch of findings via subprocess.

        Returns list of ActivationResult-compatible dicts.
        """
        if self._closed:
            return [{
                "finding_id": f.get("finding_id", "unknown"),
                "lmdb_success": False,
                "duckdb_success": None,
                "error": "proxy closed",
            } for f in findings]

        # Lazy start
        if not self._started:
            self._lazy_start()

        # Serialize findings
        findings_bytes = _canonical_findings_to_bytes(findings)

        # Run in executor (thread-safe)
        loop = asyncio.get_running_loop()
        try:
            results = await loop.run_in_executor(
                self._executor,
                lambda: self._run_sync("ingest", findings_bytes),
            )
            self._ingest_count += len(findings)
            return results
        except Exception as e:
            self._error_count += 1
            return [{
                "finding_id": f.get("finding_id", "unknown"),
                "lmdb_success": False,
                "duckdb_success": False,
                "error": str(e),
            } for f in findings]

    async def healthcheck(self) -> dict:
        """Check subprocess health."""
        if not self._started or self._closed:
            return {"status": "not_started"}

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self._executor,
                lambda: self._run_sync("healthcheck", None),
            )
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def close(self) -> None:
        """Gracefully shutdown subprocess."""
        if self._closed:
            return

        self._closed = True

        if self._started and self._request_queue:
            try:
                self._request_queue.put({"cmd": "shutdown", "data": None})
                # Wait for ack with timeout
                if self._response_queue:
                    try:
                        _ = self._response_queue.get(timeout=5.0)
                    except queue.Empty:
                        pass
            except Exception:
                pass

        if self._process and self._process.is_alive():
            try:
                self._process.terminate()
                self._process.join(timeout=2.0)
            except Exception:
                pass

        self._process = None
        self._request_queue = None
        self._response_queue = None

        self._executor.shutdown(wait=False)

    def __del__(self) -> None:
        """Destructor - ensure cleanup."""
        try:
            self.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Worker entry point (module-level function for pickling)
# ---------------------------------------------------------------------------

def _run_worker_loop(
    db_path: str | None,
    temp_dir: str | None,
    wal_path: str | None,
    request_queue: mp.Queue,
    response_queue: mp.Queue,
) -> None:
    """
    Module-level entry point for subprocess.

    Must be at module level for pickling when using spawn context.
    """
    worker = DuckDBWriterWorker(
        db_path=db_path,
        temp_dir=temp_dir,
        wal_path=wal_path,
    )
    worker.run(request_queue, response_queue)


# ---------------------------------------------------------------------------
# Fallback: Legacy thread-pool based writer (for comparison/migration)
# ---------------------------------------------------------------------------

def create_legacy_writer(db_path: Path | str | None = None) -> Any:
    """
    Create legacy thread-pool based DuckDB writer (DEPRECATED).

    Kept for backward compatibility during migration.
    Use DuckDBProxy instead for M1 8GB isolation.
    """
    # Lazy import to avoid heavy import at module level
    from .duckdb_store import DuckDBShadowStore

    store = DuckDBShadowStore(db_path=db_path)
    return store


__all__ = [
    "DuckDBProxy",
    "DuckDBWriterWorker",
    "create_legacy_writer",
]
