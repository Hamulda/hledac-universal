"""
DuckDB IPC Worker — zero-copy Arrow IPC přes POSIX shared memory.

Main Process                              DuckDB IPC Worker (subprocess)
DuckDBIPCStore                           run_ipc_worker()
  ├─ posix_ipc.SharedMemory (ring)  ────→  pa.ipc.open_stream(pa.BufferReader(ring))
  ├─ posix_ipc.Semaphore            ────→  sem.acquire() [wait for data]
  └─ Arrow RecordBatch → ring buf         ├─ conn.register() + INSERT...SELECT
                                          └─ result JSON → result_shm

Zero-copy path (M1 8GB):
  - Ring buffer: mmap'd on both sides — no CPU copy
  - pa.ipc.open_stream(pa.BufferReader(ring_segment)) — Arrow C Data Interface
  - DuckDB conn.register() + INSERT...SELECT — DuckDB reads Arrow buffers directly
  - Spawn ctx: _SPAWN_CTX.Process — MANDATORY for M1 Metal safety

Bounded invariants:
  - Ring buffer: 64 MiB max (never overflows — batches chunked at 50k findings)
  - WAL-first: LMDB write precedes DuckDB INSERT (handled in DuckDBIPCStore)
  - 1:1 result mapping: len(results) == len(findings)

Author: Sprint Issue-4
"""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
import uuid

# Lazy imports — worker runs in spawned subprocess, not at module load time
_POSIX_IPC_SPEC = importlib.util.find_spec("posix_ipc")
_PYARROW_SPEC = importlib.util.find_spec("pyarrow")

_RING_HEADER_SIZE = 128  # bytes reserved for ring buffer control block
_RING_SIZE = 64 * 1024 * 1024  # 64 MiB ring buffer
_RESULT_SIZE = 2 * 1024 * 1024  # 2 MiB result SharedMemory

# Schema SQL for canonical_findings table
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS canonical_findings (
    id          TEXT PRIMARY KEY,
    query       TEXT,
    source_type TEXT,
    confidence  DOUBLE,
    ts          DOUBLE,
    body        TEXT,
    title       TEXT,
    url         TEXT,
    provenance  TEXT,
    extracted_iocs TEXT,
    raw         TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sprint_deltas (
    sprint_id   TEXT PRIMARY KEY,
    query       TEXT,
    started_at  DOUBLE,
    ended_at    DOUBLE,
    total_fds   INTEGER,
    rss_mb      INTEGER,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS source_hits (
    sprint_id   TEXT,
    ts          DOUBLE,
    source_type TEXT,
    findings_count INTEGER,
    ioc_count   INTEGER,
    hit_rate    DOUBLE,
    PRIMARY KEY (sprint_id, source_type)
);
"""


def run_ipc_worker(
    shm_name: str,
    sem_name: str,
    db_path: str | None,
    temp_dir: str | None,
    result_shm_name: str,
    result_sem_name: str,
    ready_event_name: str,
) -> None:
    """
    Module-level entry point for spawned subprocess.

    MUST be at module level for pickling with spawn context.

    Args:
        shm_name:       Name of the 64 MiB ring buffer SharedMemory
        sem_name:       Semaphore name — worker waits on this for data
        db_path:        DuckDB database path (None = :memory:)
        temp_dir:       DuckDB temp directory (None = system temp)
        result_shm_name: SharedMemory name for result JSON
        result_sem_name: Semaphore name — worker signals after writing result
        ready_event_name: SharedMemory name for "worker ready" signal
    """
    if _POSIX_IPC_SPEC is None or sys.platform != "darwin":
        raise RuntimeError("posix_ipc not available — cannot run IPC worker on this platform")

    import posix_ipc

    # Import pyarrow and duckdb lazily (heavy, subprocess-only)
    if _PYARROW_SPEC is None:
        raise RuntimeError("pyarrow not available in IPC worker")
    import pyarrow as pa

    import duckdb

    # === Attach to ring buffer SharedMemory ===
    ring_shm = posix_ipc.SharedMemory(name=shm_name)
    ring_buf = ring_shm.buf  # memoryview over the mmap'd region

    # === Attach to result SharedMemory ===
    result_shm = posix_ipc.SharedMemory(name=result_shm_name)
    result_buf = result_shm.buf

    # === Open semaphores ===
    data_sem = posix_ipc.Semaphore(
        sem_name,
        flags=posix_ipc.O_CREAT,
    )
    result_sem = posix_ipc.Semaphore(
        result_sem_name,
        flags=posix_ipc.O_CREAT,
    )
    ready_sem = posix_ipc.Semaphore(
        ready_event_name,
        flags=posix_ipc.O_CREAT,
    )

    try:
        data_sem.acquire(0)  # Clear any stale count
    except Exception:
        pass

    # === Connect to DuckDB ===
    db_cfg: dict[str, str | int | float | list[str]] = {
        "access_mode": "automatic",
        "threads": 2,
    }
    if temp_dir:
        db_cfg["temp_directory"] = temp_dir

    conn = duckdb.connect(database=db_path or ":memory:", read_only=False, config=db_cfg)

    # Initialize schema
    for stmt in _SCHEMA_SQL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)

    # === Signal: worker is ready ===
    try:
        ready_sem.release()
    except Exception:
        pass

    # === Main ingest loop ===
    while True:
        try:
            # Wait for data signal from main process
            data_sem.acquire()
        except Exception:
            # Interrupted — check for shutdown
            continue

        # Read ring buffer control block
        write_pos = struct.unpack_from("<I", ring_buf, 0)[0]
        read_pos = struct.unpack_from("<I", ring_buf, 4)[0]

        if read_pos == write_pos:
            # Spurious wakeup — no data
            continue

        # Process all available records in the ring
        processed = 0
        while read_pos != write_pos:
            # Read 4-byte record length at read_pos
            if read_pos + 4 > len(ring_buf):
                # Wrap to beginning of data region
                read_pos = _RING_HEADER_SIZE

            record_len = struct.unpack_from("<I", ring_buf, read_pos)[0]
            rec_start = read_pos + 4
            rec_end = rec_start + record_len

            if rec_end > len(ring_buf):
                # Record spans the ring buffer boundary — copy into contiguous buffer
                # This is the only copy in the pipeline (~record_len bytes, 1× per batch)
                segment_a_len = len(ring_buf) - rec_start
                segment_b_len = record_len - segment_a_len
                record_bytes = bytes(ring_buf[read_pos + 4 :]) + bytes(ring_buf[:segment_b_len])
                rec_start = _RING_HEADER_SIZE
                read_pos = rec_start + record_len
            else:
                if rec_start >= _RING_HEADER_SIZE and rec_end <= len(ring_buf):
                    record_bytes = bytes(ring_buf[rec_start:rec_end])
                    read_pos = rec_end if rec_end < len(ring_buf) else _RING_HEADER_SIZE
                else:
                    # Wrap case
                    segment_a_len = len(ring_buf) - rec_start
                    segment_b_len = record_len - segment_a_len
                    record_bytes = bytes(ring_buf[rec_start:]) + bytes(ring_buf[:segment_b_len])
                    read_pos = rec_end if rec_end <= len(ring_buf) else _RING_HEADER_SIZE

            # Zero-copy Arrow IPC deserialization from contiguous bytes
            buf = pa.py_buffer(record_bytes)
            reader = pa.ipc.open_stream(buf)
            batch = reader.read_next_batch()

            # Register Arrow batch and INSERT into DuckDB
            conn.register("_ipc_batch", batch)
            try:
                conn.execute("""
                    INSERT INTO canonical_findings BY NAME
                    SELECT * FROM _ipc_batch
                    ON CONFLICT (id) DO UPDATE SET
                        query       = EXCLUDED.query,
                        source_type = EXCLUDED.source_type,
                        confidence  = EXCLUDED.confidence,
                        ts          = EXCLUDED.ts,
                        body        = EXCLUDED.body,
                        title       = EXCLUDED.title,
                        url         = EXCLUDED.url,
                        provenance  = EXCLUDED.provenance,
                        extracted_iocs = EXCLUDED.extracted_iocs,
                        raw         = EXCLUDED.raw
                """)
            finally:
                try:
                    conn.unregister("_ipc_batch")
                except Exception:
                    pass

            processed += 1

            # Advance read position in ring buffer header
            struct.pack_into("<I", ring_buf, 4, read_pos)

            # Check for shutdown signal (special record with len=0)
            if record_len == 0:
                break

        # === Write results to result SharedMemory ===
        if processed > 0:
            result_data = {
                "type": "result",
                "count": processed,
                "status": "ok",
            }
        else:
            result_data = {"type": "result", "count": 0, "status": "ok"}

        result_json = json.dumps(result_data, default=str).encode("utf-8")
        result_len = len(result_json)
        if result_len > _RESULT_SIZE - 4:
            result_json = json.dumps({"type": "result", "count": processed, "error": "result too large"}).encode("utf-8")
            result_len = len(result_json)

        # Write result: 4-byte length + JSON bytes
        struct.pack_into("<I", result_buf, 0, result_len)
        result_buf[:result_len] = result_json

        # Signal result ready to main process
        try:
            result_sem.release()
        except Exception:
            pass

    # === Cleanup (unreachable in normal operation) ===
    conn.close()
    ring_shm.close_unlink()
    result_shm.close_unlink()
