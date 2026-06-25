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
import importlib.util
import multiprocessing as mp
import multiprocessing.shared_memory as shm
import os
import queue
import sys
import threading
import uuid
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

# Zero-copy IPC: threshold for shared memory transfer (64 KiB)
# Below this size: msgspec.json.encode() direct (lower latency overhead)
# Above this size: shared memory block + handle in queue message
_SHM_PAYLOAD_THRESHOLD_BYTES = 65536

# Arrow IPC + shared memory fast path
# Arrow batch fast path activates when batch has >= this many findings
# F290C: Lowered from 10 to 5 — Arrow IPC overhead (~50μs fixní) se vyplatí
#   i pro 5 řádků (~2-5KB). Paired s size-gated SHM threshold (32MiB).
_ARROW_BATCH_MIN_ROWS = 5

# Shared memory block name prefix for Arrow IPC blocks
_SHM_ARROW_PREFIX = "hledac_arrow_"

# Maximum shared memory block size for Arrow IPC (default 32 MiB)
# Can be overridden via HLEDAC_ARROW_SHM_MAX_MB env var
_SHM_ARROW_MAX_BYTES = int(os.environ.get("HLEDAC_ARROW_SHM_MAX_MB", "32")) * 1024 * 1024

# PyArrow availability (lazy, fail-soft)
_PYARROW_SPEC = importlib.util.find_spec("pyarrow")


# ---------------------------------------------------------------------------
# Zero-Copy Shared Memory Helpers (main process)
# ---------------------------------------------------------------------------
# _shm_blocks: block_id → shm.SharedMemory (lives in main process only)
_shm_blocks: dict[str, shm.SharedMemory] = {}


def _create_shm_block(data: bytes) -> str:
    """
    Create a POSIX shared memory block for zero-copy cross-process transfer.

    Large payload_text fields (>64KiB) are transferred via shared memory
    instead of msgspec.json encoding, avoiding CPU serialization overhead.

    Returns block_id that is passed via queue message. Subprocess reads
    directly from shared memory (no serialization copy on either side).
    """
    if len(data) <= _SHM_PAYLOAD_THRESHOLD_BYTES:
        # Below threshold: shared memory overhead not worth it
        return ""

    # Use hex-based name (macOS POSIX shm name max ~30 chars including leading /)
    block_id = uuid.uuid4().hex[:28]
    try:
        shared_mem = shm.SharedMemory(create=True, size=len(data))
        shared_mem.buf[:len(data)] = data  # type: ignore[assignment]
        _shm_blocks[block_id] = shared_mem
        return block_id
    except Exception:
        # Fallback: caller uses msgspec encode path
        return ""


def _attach_shm_block(block_id: str) -> bytes | None:
    """Attach to an existing shared memory block (subprocess side)."""
    if not block_id:
        return None
    try:
        existing = shm.SharedMemory(name=block_id)
        data = bytes(existing.buf[:existing.size])  # type: ignore[operator]
        existing.close()
        return data
    except Exception:
        return None


def _cleanup_shm_block(block_id: str) -> None:
    """
    Cleanup a shared memory block after use (main process side).

    Called from main process after subprocess confirms ingest complete.
    Safe to call multiple times for same block_id (idempotent).
    """
    if not block_id or block_id not in _shm_blocks:
        return
    try:
        shared_mem = _shm_blocks.pop(block_id)
        shared_mem.close()
        shared_mem.unlink()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Arrow IPC + Shared Memory Helpers (main process)
# ---------------------------------------------------------------------------

def _findings_to_arrow_batch(findings_dicts: list[dict]) -> Any:
    """
    Convert a list of finding dicts to a PyArrow RecordBatch.

    Schema: id (utf8), query (utf8), source_type (utf8),
            confidence (float64), ts (float64), provenance_json (utf8)

    Returns a pa.RecordBatch, or None if pyarrow is unavailable.
    Fail-soft: any error returns None so caller falls back to JSON path.
    """
    if _PYARROW_SPEC is None:
        return None

    try:
        import pyarrow as pa

        ids = [f.get("id", f.get("finding_id", "")) for f in findings_dicts]
        queries = [f.get("query", "") for f in findings_dicts]
        source_types = [f.get("source_type", "") for f in findings_dicts]
        confidences = [float(f.get("confidence", 0.0)) for f in findings_dicts]
        timestamps = [float(f.get("ts", 0.0)) for f in findings_dicts]
        provenances = [f.get("provenance_json", "") or _ENCODER.encode(f.get("provenance", [])).decode("utf-8") for f in findings_dicts]

        schema = pa.schema([
            ("id", pa.utf8()),
            ("query", pa.utf8()),
            ("source_type", pa.utf8()),
            ("confidence", pa.float64()),
            ("ts", pa.float64()),
            ("provenance_json", pa.utf8()),
        ])

        batch = pa.record_batch([
            pa.array(ids, type=pa.utf8()),
            pa.array(queries, type=pa.utf8()),
            pa.array(source_types, type=pa.utf8()),
            pa.array(confidences, type=pa.float64()),
            pa.array(timestamps, type=pa.float64()),
            pa.array(provenances, type=pa.utf8()),
        ], schema=schema)

        return batch
    except Exception:
        return None


def _arrow_batch_to_shm(batch: Any) -> tuple[Any, int] | None:
    """
    Serialize a PyArrow RecordBatch to a shared memory block.

    Uses Arrow IPC RecordBatchStream format.
    Returns (SharedMemory, n_bytes) on success, or None on failure.
    The SharedMemory block is created with the hledac_arrow_ prefix.
    Subprocess is responsible for unlinking the block.

    Maximum block size is _SHM_ARROW_MAX_BYTES (default 32 MiB).
    If serialized data exceeds this limit, returns None (caller uses JSON path).
    """
    if _PYARROW_SPEC is None:
        return None

    try:
        import pyarrow as pa

        # Serialize RecordBatch to IPC stream bytes
        buffer = pa.BufferOutputStream()
        writer = pa.ipc.RecordBatchStreamWriter(buffer, batch.schema)
        writer.write_batch(batch)
        writer.close()
        ipc_bytes = buffer.getvalue().to_pybytes()

        n_bytes = len(ipc_bytes)

        # Size guard: reject blocks above configured limit
        if n_bytes > _SHM_ARROW_MAX_BYTES:
            return None

        # Create shared memory block with prefixed name (macOS POSIX safe)
        # Name limit: macOS POSIX shm ~30 chars total; prefix=13 + hex=14 = 27 chars
        block_name = _SHM_ARROW_PREFIX + uuid.uuid4().hex[:14]
        shared_mem = shm.SharedMemory(create=True, name=block_name, size=n_bytes)

        try:
            shared_mem.buf[:n_bytes] = ipc_bytes  # type: ignore[assignment]
        except Exception:
            shared_mem.close()
            shared_mem.unlink()
            return None

        return (shared_mem, n_bytes)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# CanonicalFinding serialization helpers
# ---------------------------------------------------------------------------

def _canonical_finding_to_dict(f: Any) -> tuple[dict, list[str]]:
    """
    Convert CanonicalFinding (or dict) to serializable dict.

    Returns (dict, shm_block_ids) where shm_block_ids are block_ids
    for large payload_text fields transferred via shared memory.
    """
    if hasattr(f, "model_dump"):
        d = f.model_dump()
    else:
        d = dict(f)

    # Extract and replace large payload_text with block_id reference
    block_ids: list[str] = []
    payload_text = d.get("payload_text")
    if payload_text and isinstance(payload_text, (str, bytes)):
        raw = payload_text.encode("utf-8") if isinstance(payload_text, str) else payload_text
        if len(raw) > _SHM_PAYLOAD_THRESHOLD_BYTES:
            block_id = _create_shm_block(raw)
            if block_id:
                d["payload_text"] = f"__shm:{block_id}__"
                block_ids.append(block_id)
            # else: block creation failed, keep original (msgspec will encode it)

    return d, block_ids


def _canonical_findings_to_bytes(
    findings: list[Any],
) -> tuple[bytes, list[str]]:
    """
    Serialize list of CanonicalFinding to bytes for IPC.

    Returns (bytes, shm_block_ids) where shm_block_ids are block_ids
    for large payload_text fields transferred via shared memory.
    Caller MUST call _cleanup_shm_block for each block_id after ingest completes.
    """
    all_block_ids: list[str] = []
    dicts: list[dict] = []
    for f in findings:
        d, block_ids = _canonical_finding_to_dict(f)
        dicts.append(d)
        all_block_ids.extend(block_ids)
    return _ENCODER.encode(dicts), all_block_ids


def _bytes_to_duckdb_rows(
    data: bytes, shm_payloads: dict[str, bytes] | None = None
) -> list[list]:
    """
    Deserialize bytes to DuckDB row format.

    Args:
        data: JSON-encoded findings list
        shm_payloads: block_id → actual bytes (from shared memory reads).
                      None means no shared memory payloads present.
    """
    decoded = _DECODER.decode(data)
    shm_payloads = shm_payloads or {}
    rows = []
    for item in decoded:
        provenance = item.get("provenance", ())
        provenance_json = _ENCODER.encode(provenance).decode("utf-8")

        # Restore payload_text from shared memory if referenced
        payload_text = item.get("payload_text", "")
        if isinstance(payload_text, str) and payload_text.startswith("__shm:") and payload_text.endswith("__"):
            block_id = payload_text[6:-4]
            restored = shm_payloads.get(block_id)
            if restored is not None:
                payload_text = restored.decode("utf-8")
            else:
                payload_text = ""

        rows.append([
            item["finding_id"],
            item["query"],
            item["source_type"],
            item["confidence"],
            item["ts"],
            provenance_json,
            payload_text,
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
        # F290B: Prepared statement cache for zero-parse INSERT
        self._insert_stmt: Any = None
        self._insert_arrow_stmt: Any = None

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

        # Apply runtime settings (memory limit, threads, temp directory)
        self._configure_connection()

        # Apply temp_directory if configured (file mode or :memory: with RAM disk)
        # Sprint P1-1: HLEDAC_DUCKDB_RAMDISK_TEMP env var takes precedence
        ramdisk_temp = os.environ.get("HLEDAC_DUCKDB_RAMDISK_TEMP")
        temp_to_use = ramdisk_temp or self.temp_dir
        if temp_to_use:
            try:
                self.conn.execute(f"SET temp_directory = '{temp_to_use}'")
                self.conn.execute("SET max_temp_space = '4GB'")
            except Exception:
                pass  # Fallback to default temp handling

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

        # F290C: Prepare INSERT statements after connection is configured.
        # Previously this was called recursively from _prepare_statements itself
        # (a bug — removed). Now called once here after _configure_connection.
        self._prepare_statements()

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

    def _prepare_statements(self) -> None:
        """F290B: Prepare INSERT statements once for zero-parse reuse."""
        if not self.conn:
            return
        try:
            # Prepared INSERT for row-by-row (small batches)
            self._insert_stmt = self.conn.prepare("""
                INSERT OR IGNORE INTO canonical_findings
                (id, query, source_type, confidence, ts, provenance_json, payload_text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """)
            # Prepared INSERT for Arrow batch (large batches)
            self._insert_arrow_stmt = self.conn.prepare("""
                INSERT OR IGNORE INTO canonical_findings BY NAME SELECT * FROM _arrow_batch
            """)
        except Exception:
            # Fallback: will use dynamic execute on errors
            pass

    def _insert_findings_batch(self, rows: list[list]) -> int:
        """Execute bulk INSERT with Arrow path support."""
        if not rows or not self.conn:
            return 0

        try:
            # Arrow zero-copy path (faster for large batches)
            if len(rows) >= 5 and "pyarrow" in sys.modules:
                return self._insert_arrow(rows)

            # Legacy executemany path
            return self._insert_executemany(rows)
        except Exception:
            # Fallback: try executemany
            return self._insert_executemany(rows)

    def _insert_executemany(self, rows: list[list]) -> int:
        """F290B: INSERT via prepared statement (zero-parse per row)."""
        if not self.conn:
            return 0

        # F290B: Use prepared statement if available
        if self._insert_stmt is not None:
            return self._insert_executemany_prepared(rows)

        # Fallback: dynamic execute
        return self._insert_executemany_dynamic(rows)

    def _insert_executemany_prepared(self, rows: list[list]) -> int:
        """F290B: Prepared statement INSERT — DuckDB parses SQL only once."""
        inserted = 0
        for row in rows:
            try:
                self._insert_stmt.execute(row)  # type: ignore[union-attr]
                inserted += 1
            except Exception:
                # Skip duplicates/errors
                pass
        return inserted

    def _insert_executemany_dynamic(self, rows: list[list]) -> int:
        """Legacy dynamic INSERT — parses SQL every row (slow)."""
        inserted = 0
        for row in rows:
            try:
                self.conn.execute("""
                    INSERT OR IGNORE INTO canonical_findings
                    (id, query, source_type, confidence, ts, provenance_json, payload_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, row)  # row has exactly 7 elements
                inserted += 1
            except Exception:
                # Skip duplicates/errors
                pass
        return inserted

    def _insert_arrow(self, rows: list[list]) -> int:
        """Arrow zero-copy INSERT path — correct DuckDB register() protocol."""
        try:
            import pyarrow as pa

            columns = [
                [r[0] for r in rows],  # id
                [r[1] for r in rows],  # query
                [r[2] for r in rows],  # source_type
                [r[3] for r in rows],  # confidence
                [r[4] for r in rows],  # ts
                [r[5] for r in rows],  # provenance_json
                [r[6] if len(r) > 6 else "" for r in rows],  # payload_text
            ]

            table = pa.table({
                "id": columns[0],
                "query": columns[1],
                "source_type": columns[2],
                "confidence": columns[3],
                "ts": columns[4],
                "provenance_json": columns[5],
                "payload_text": columns[6],
            })

            # Register Arrow table with DuckDB connection (zero-copy view)
            self.conn.register("_arrow_batch", table)
            try:
                # F290B: Use prepared Arrow INSERT if available
                if self._insert_arrow_stmt is not None:
                    self._insert_arrow_stmt.execute()
                else:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO canonical_findings BY NAME "
                        "SELECT * FROM _arrow_batch"
                    )
                return len(rows)
            finally:
                # Always unregister to avoid connection state leak
                try:
                    self.conn.unregister("_arrow_batch")
                except Exception:
                    pass

        except Exception:
            return self._insert_executemany(rows)

    def _process_ingest_shm(
        self, shm_name: str, n_bytes: int, n_rows: int
    ) -> list[dict]:
        """
        Process a batch of findings from a shared memory Arrow IPC block.

        Subprocess owns the shm block — always unlinks on completion or error.
        """
        shm_block: Any = None
        try:
            # Attach to the shared memory block (subprocess side)
            shm_block = shm.SharedMemory(name=shm_name, create=False)

            # Read IPC bytes from shared memory
            ipc_bytes = bytes(shm_block.buf[:n_bytes])

            # Deserialize Arrow IPC stream to RecordBatch
            if _PYARROW_SPEC is None:
                raise RuntimeError("pyarrow not available for Arrow IPC deserialization")

            import pyarrow as pa

            reader = pa.ipc.open_stream(pa.py_buffer(ipc_bytes))
            batch = reader.read_next_batch()

            # Register Arrow batch with DuckDB (zero-copy)
            self.conn.register("_shm_batch", batch)
            try:
                self.conn.execute(
                    "INSERT OR IGNORE INTO canonical_findings BY NAME "
                    "SELECT * FROM _shm_batch"
                )
            finally:
                try:
                    self.conn.unregister("_shm_batch")
                except Exception:
                    pass

            # Return per-finding results
            results = []
            for i in range(n_rows):
                results.append({
                    "finding_id": f"_shm_row_{i}",
                    "lmdb_success": True,
                    "duckdb_success": True,
                    "error": None,
                })
            return results

        except Exception as e:
            return [{
                "finding_id": "unknown",
                "lmdb_success": True,
                "duckdb_success": False,
                "error": str(e),
            }]
        finally:
            # ALWAYS cleanup shm block in subprocess — never leave orphan /dev/shm entries
            if shm_block is not None:
                try:
                    shm_block.close()
                    shm_block.unlink()
                except Exception:
                    pass

    def _process_ingest(
        self, findings_bytes: bytes, shm_block_ids: list[str] | None = None
    ) -> list[dict]:
        """Process a batch of findings from main process."""
        try:
            # Read shared memory payloads if any
            shm_payloads: dict[str, bytes] = {}
            if shm_block_ids:
                for block_id in shm_block_ids:
                    data = _attach_shm_block(block_id)
                    if data is not None:
                        shm_payloads[block_id] = data

            rows = _bytes_to_duckdb_rows(findings_bytes, shm_payloads)
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
            shm_block_ids = msg.get("shm_block_ids")

            try:
                if cmd == "ingest":
                    results = self._process_ingest(data, shm_block_ids)
                    response_queue.put({"type": "result", "data": results})

                elif cmd == "ingest_shm":
                    results = self._process_ingest_shm(
                        msg["shm_name"],
                        msg["n_bytes"],
                        msg["n_rows"],
                    )
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

    def _run_sync(
        self, cmd: str, data: Any, shm_block_ids: list[str] | None = None, **kwargs: Any
    ) -> Any:
        """Synchronous command to subprocess (called from executor)."""
        if not self._started or self._closed:
            raise RuntimeError("DuckDBProxy not started or closed")

        if not self._request_queue or not self._response_queue:
            raise RuntimeError("DuckDBProxy queues not initialized")

        # ── Arrow IPC + Shared Memory fast path ─────────────────────────────────
        # When ingesting bytes (JSON-encoded findings list) with >= 5 rows
        # (F290C: lowered from 10), serialize via Arrow IPC into a shared memory
        # block and send the handle to the subprocess. This avoids 2× JSON
        # encode/decode copy overhead.
        if cmd == "ingest" and isinstance(data, bytes):
            row_count = kwargs.get("row_count", 0)
            if row_count >= _ARROW_BATCH_MIN_ROWS:
                try:
                    findings_dicts: list[dict] = _DECODER.decode(data)
                    batch = _findings_to_arrow_batch(findings_dicts)
                    if batch is not None:
                        shm_result = _arrow_batch_to_shm(batch)
                        if shm_result is not None:
                            shm_block, n_bytes = shm_result
                            # Transfer ownership to subprocess; main process releases reference
                            self._request_queue.put({
                                "cmd": "ingest_shm",
                                "shm_name": shm_block.name,
                                "n_bytes": n_bytes,
                                "n_rows": len(findings_dicts),
                            })
                            shm_block.close()  # main process no longer references the block
                            msg = self._response_queue.get(timeout=30.0)
                            if msg.get("type") == "error":
                                self._error_count += 1
                                raise RuntimeError(msg.get("error", "Subprocess error"))
                            return msg.get("data")
                except Exception:
                    # Fall through to legacy JSON path
                    pass
        # ── End Arrow fast path ─────────────────────────────────────────────────

        # Send request with optional shared memory block IDs
        self._request_queue.put({
            "cmd": cmd,
            "data": data,
            "shm_block_ids": shm_block_ids or [],
        })

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

        Zero-copy path: large payload_text fields (>64KiB) are transferred
        via POSIX shared memory instead of msgspec.json serialization.

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

        # Serialize findings + collect shared memory block IDs for large payloads
        findings_bytes, shm_block_ids = _canonical_findings_to_bytes(findings)
        row_count = len(findings)

        # Run in executor (thread-safe)
        loop = asyncio.get_running_loop()
        try:
            results = await loop.run_in_executor(
                self._executor,
                lambda: self._run_sync("ingest", findings_bytes, shm_block_ids, row_count=row_count),
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
        finally:
            # Cleanup shared memory blocks after ingest completes (success or failure)
            for block_id in shm_block_ids:
                _cleanup_shm_block(block_id)

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
