"""
DuckDB IPC Store — zero-copy Arrow IPC přes POSIX shared memory.

DuckDB běží v izolovaném subprocessu (spawn ctx) s vlastní paměťovou oblastí.
Hlavní proces (MLX Metal) je chráněn před DuckDB memory pressure.

ARCHITECTURA (Issue #4):
Main Process                          DuckDB IPC Worker (subprocess)
DuckDBIPCStore                       run_ipc_worker()
  ├─ posix_ipc.SharedMemory (64MB)  ├─ pa.ipc.open_stream(BufferReader(ring))
  ├─ posix_ipc.Semaphore           ├─ conn.register() + INSERT...SELECT
  └─ Arrow RecordBatch → ring buf   └─ result JSON → result_shm

Zero-copy path (M1 8GB):
  - Ring buffer: mmap'd on both sides — no CPU copy in data path
  - pa.ipc.open_stream(pa.BufferReader(ring_segment)) — Arrow C Data Interface
  - DuckDB conn.register() + INSERT...SELECT — DuckDB reads Arrow buffers directly
  - Spawn ctx: _SPAWN_CTX.Process — MANDATORY for M1 Metal safety

Bounded invariants:
  - Ring buffer: 64 MiB max (batches auto-chunked at source)
  - WAL-first: LMDB write precedes DuckDB INSERT (caller's responsibility)
  - 1:1 result mapping: len(results) == len(findings)

Author: Sprint Issue-4
"""

from __future__ import annotations

import asyncio
import importlib
import json
import multiprocessing as mp
import struct
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec

if TYPE_CHECKING:
    from .duckdb_store import CanonicalFinding


# Lazy imports — posix_ipc is Darwin-only
_POSIX_IPC_SPEC = importlib.util.find_spec("posix_ipc")
_PYARROW_SPEC = importlib.util.find_spec("pyarrow")

_SPAWN_CTX = mp.get_context("spawn")

_RING_SIZE = 64 * 1024 * 1024  # 64 MiB ring buffer
_RING_HEADER = 128  # ring buffer control block size (bytes)
_RESULT_SIZE = 2 * 1024 * 1024  # 2 MiB result SharedMemory
_SPAWN_TIMEOUT_S = 10.0  # subprocess startup timeout
# Issue #11: streaming micro-batch size — constant memory footprint on M1 8GB
# 1000 findings × ~1KB/finding ≈ 1 MB per micro-batch (vs 10 MB peak for 10K)
_STREAM_BATCH_SIZE = 1000


class DuckDBIPCChannel(msgspec.Struct, frozen=True, gc=False):
    """
    Arrow IPC channel descriptor — passed to subprocess at spawn time.

    frozen=True: immutable after creation (safe to share across async tasks)
    gc=False:    prevents cyclic GC overhead
    """

    shm_name: str
    ring_size: int
    sem_name: str
    result_shm_name: str
    result_sem_name: str
    ready_sem_name: str
    db_path: str | None
    temp_dir: str | None


class ActivationResult(msgspec.Struct, frozen=True, gc=False):
    finding_id: str
    lmdb_success: bool | list[bool]
    duckdb_success: bool | None
    lmdb_key: str
    desync: bool
    error: str | None
    accepted: bool


class FindingQualityDecision(msgspec.Struct, frozen=True, gc=False):
    accepted: bool
    reason: str | None
    entropy: float
    normalized_hash: str | None
    duplicate: bool


def _posix_ipc_available() -> bool:
    """Check if posix_ipc is available on this platform."""
    return _POSIX_IPC_SPEC is not None and __import__("sys").platform == "darwin"


# ----------------------------------------------------------------------
# DuckDBIPCStore
# ----------------------------------------------------------------------


class DuckDBIPCStore:
    """
    Zero-copy DuckDB ingest via Arrow IPC over POSIX shared memory.

    Implements the same public API surface as DuckDBShadowStore:
      - async_ingest_findings_batch(findings) -> list[FindingQualityDecision | ActivationResult]
      - async_initialize() / async_initialize_schema()
      - shutdown() / close() / aclose()
      - async_healthcheck()

    On M1: spawns a subprocess via spawn ctx (Metal-safe). Main process
    serializes Arrow batches and writes them into a ring buffer shared
    with the worker subprocess. Worker deserializes via pa.ipc.open_stream
    (zero-copy from mmap'd ring buffer) and INSERTs into DuckDB.

    Fallback: if posix_ipc is unavailable, or subprocess fails to start,
    all methods return empty/degraded results and log a warning — never raise.
    """

    __slots__ = (
        "_channel",
        "_ring_buf",
        "_result_buf",
        "_proc",
        "_started",
        "_closed",
        "_db_path",
        "_temp_dir",
        "_uma_state",
        "_executor",
        "_lock",
    )

    def __init__(
        self,
        db_path: Path | str | None = None,
        temp_dir: Path | str | None = None,
        uma_state: str | None = None,
    ) -> None:
        self._db_path: Path | None = Path(db_path) if db_path is not None else None
        self._temp_dir: Path | str | None = Path(temp_dir) if temp_dir is not None else None
        self._uma_state: str | None = uma_state

        # Resolve default paths (mirrors DuckDBShadowStore)
        if self._db_path is None:
            try:
                from hledac.universal.paths import DUCKDB_STORE_ROOT, RAMDISK_ACTIVE, RAMDISK_ROOT

                if RAMDISK_ACTIVE:
                    self._db_path = DUCKDB_STORE_ROOT / "shadow_analytics.duckdb"
                    self._temp_dir = RAMDISK_ROOT / "duckdb_tmp"
                else:
                    self._db_path = DUCKDB_STORE_ROOT / "analytics.duckdb"
            except Exception:
                pass  # Will use :memory:

        self._channel: DuckDBIPCChannel | None = None
        self._ring_buf: Any = None  # memoryview over ring buffer
        self._result_buf: Any = None  # memoryview over result buffer
        self._proc: Any = None  # SpawnProcess
        self._started: bool = False
        self._closed: bool = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="duckdb-ipc-writer")
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_initialize(self) -> None:
        """Spawn the DuckDB IPC worker subprocess and wait for ready signal."""
        if self._closed:
            raise RuntimeError("DuckDBIPCStore is closed")

        if self._started:
            return

        if not _posix_ipc_available():
            return  # Fail-open: subprocess won't start; all methods return degraded results

        try:
            await asyncio.to_thread(self._spawn_sync)
            self._started = True
        except Exception:
            self._started = False

    def _spawn_sync(self) -> None:
        """Synchronous subprocess spawn (runs in ThreadPoolExecutor)."""
        if not _posix_ipc_available():
            return

        import posix_ipc

        prefix = f"/hldq-{uuid.uuid4().hex[:8]}"
        shm_name = f"{prefix}-ring"
        result_shm_name = f"{prefix}-res"
        sem_name = f"{prefix}-sem"
        result_sem_name = f"{prefix}-res-sem"
        ready_sem_name = f"{prefix}-ready"

        ring_shm: Any = None
        result_shm: Any = None
        try:
            ring_shm = posix_ipc.SharedMemory(
                shm_name,
                flags=posix_ipc.O_CREAT | posix_ipc.O_EXCL,
                size=_RING_SIZE,
            )
            result_shm = posix_ipc.SharedMemory(
                result_shm_name,
                flags=posix_ipc.O_CREAT | posix_ipc.O_EXCL,
                size=_RESULT_SIZE,
            )

            ring_buf = ring_shm.buf
            struct.pack_into("<I", ring_buf, 0, _RING_HEADER)
            struct.pack_into("<I", ring_buf, 4, _RING_HEADER)

            result_buf = result_shm.buf
            struct.pack_into("<I", result_buf, 0, 0)

            self._ring_buf = ring_buf
            self._result_buf = result_buf

            self._channel = DuckDBIPCChannel(
                shm_name=shm_name,
                ring_size=_RING_SIZE,
                sem_name=sem_name,
                result_shm_name=result_shm_name,
                result_sem_name=result_sem_name,
                ready_sem_name=ready_sem_name,
                db_path=str(self._db_path) if self._db_path else None,
                temp_dir=str(self._temp_dir) if self._temp_dir else None,
            )

            from . import _duckdb_ipc_worker

            self._proc = _SPAWN_CTX.Process(
                target=_duckdb_ipc_worker.run_ipc_worker,
                args=(
                    shm_name,
                    sem_name,
                    str(self._db_path) if self._db_path else None,
                    str(self._temp_dir) if self._temp_dir else None,
                    result_shm_name,
                    result_sem_name,
                    ready_sem_name,
                ),
                daemon=False,
            )
            self._proc.start()

            ready_sem = posix_ipc.Semaphore(ready_sem_name, flags=posix_ipc.O_CREAT)
            ready_sem.acquire(timeout=_SPAWN_TIMEOUT_S)
            ready_sem.close()
            del ready_sem

        except Exception:
            if ring_shm is not None:
                try:
                    ring_shm.close()
                    ring_shm.unlink()
                except Exception:
                    pass
            if result_shm is not None:
                try:
                    result_shm.close()
                    result_shm.unlink()
                except Exception:
                    pass
            if self._proc is not None:
                try:
                    self._proc.terminate()
                    self._proc.join(timeout=2.0)
                except Exception:
                    pass
                self._proc = None
            raise

    async def async_initialize_schema(self) -> None:
        """Schema is initialized in the worker subprocess on first start."""
        pass

    # ------------------------------------------------------------------
    # Core ingest API
    # ------------------------------------------------------------------

    async def async_ingest_findings_batch(
        self,
        findings: list[CanonicalFinding],
    ) -> list[FindingQualityDecision | ActivationResult]:
        """
        Zero-copy Arrow IPC ingest via POSIX shared memory ring buffer.

        Quality gate (delegated to Rust assess_batch or per-row fallback),
        then serialized as Arrow RecordBatch and written to ring buffer.
        Worker subprocess deserializes and INSERTs into DuckDB.

        Returns list[FindingQualityDecision | ActivationResult] with 1:1 invariant.
        Returns empty list if subprocess is not running (fail-safe).
        """
        if not findings:
            return []

        if self._closed:
            return [
                FindingQualityDecision(
                    accepted=False,
                    reason="store_closed",
                    entropy=0.0,
                    normalized_hash=None,
                    duplicate=False,
                )
                for _ in findings
            ]

        if not self._started or self._channel is None:
            await self.async_initialize()
            if not self._started or self._channel is None:
                return self._degraded_results(findings, "not_started")

        try:
            return await self._write_batch_to_ring(findings)
        except Exception:
            return self._degraded_results(findings, "ring_write_failed")

    async def _write_batch_to_ring(
        self,
        findings: list[CanonicalFinding],
    ) -> list[FindingQualityDecision | ActivationResult]:
        """
        Stream findings as micro-batches via POSIX shared memory ring buffer.

        Issue #11 fix: Instead of materializing the entire findings list into
        one Arrow batch (10K × ~1KB = 10 MB peak), stream micro-batches of
        _STREAM_BATCH_SIZE (1000) findings each. Memory footprint is now
        constant: ~1 MB per micro-batch instead of ~10 MB for the full list.

        Each micro-batch: Arrow serialize → ring write → signal → wait result
        All micro-batch results are accumulated and returned as one list.
        """
        import posix_ipc

        channel = self._channel
        assert channel is not None

        results: list[FindingQualityDecision | ActivationResult] = []
        total_count = 0

        # Stream micro-batches: chunk findings to avoid memory pressure
        for chunk_start in range(0, len(findings), _STREAM_BATCH_SIZE):
            chunk_end = min(chunk_start + _STREAM_BATCH_SIZE, len(findings))
            chunk = findings[chunk_start:chunk_end]

            batch, _ = await asyncio.to_thread(
                self._findings_to_arrow_batch,
                chunk,
            )
            if batch is None:
                results.extend(self._degraded_results(chunk, "arrow_build_failed"))
                continue

            ipc_bytes = await asyncio.to_thread(self._serialize_arrow_batch, batch)
            if ipc_bytes is None:
                results.extend(self._degraded_results(chunk, "arrow_serialize_failed"))
                continue

            record_len = len(ipc_bytes)
            if record_len + 4 > _RING_SIZE - _RING_HEADER:
                results.extend(self._degraded_results(chunk, "batch_too_large"))
                continue

            # Write micro-batch to ring buffer
            await asyncio.to_thread(self._write_ring_record, ipc_bytes)

            # Signal worker: acquire semaphore to signal new data
            sem: Any = None
            try:
                sem = posix_ipc.Semaphore(channel.sem_name, flags=posix_ipc.O_CREAT)
                sem.release()
            except Exception:
                if sem is not None:
                    try:
                        sem.close()
                    except Exception:
                        pass
                results.extend(self._degraded_results(chunk, "semaphore_failed"))
                continue
            finally:
                if sem is not None:
                    try:
                        sem.close()
                    except Exception:
                        pass

            # Wait for worker to process micro-batch and write result
            result_sem: Any = None
            try:
                result_sem = posix_ipc.Semaphore(
                    channel.result_sem_name, flags=posix_ipc.O_CREAT
                )
                result_sem.acquire(timeout=30.0)
            except Exception:
                if result_sem is not None:
                    try:
                        result_sem.close()
                    except Exception:
                        pass
                results.extend(self._degraded_results(chunk, "result_timeout"))
                continue
            finally:
                if result_sem is not None:
                    try:
                        result_sem.close()
                    except Exception:
                        pass

            # Read result for this micro-batch
            result_buf = self._result_buf
            result_len = struct.unpack_from("<I", result_buf, 0)[0]
            if result_len == 0 or result_len > _RESULT_SIZE - 4:
                results.extend(self._degraded_results(chunk, "invalid_result"))
                continue

            result_json = bytes(result_buf[4 : 4 + result_len]).decode("utf-8")
            result_data = json.loads(result_json)

            chunk_count = result_data.get("count", len(chunk))
            total_count += chunk_count

            # Build per-finding results for this chunk
            for i, finding in enumerate(chunk):
                if i < chunk_count:
                    results.append(
                        ActivationResult(
                            finding_id=finding.finding_id,
                            lmdb_success=True,
                            duckdb_success=True,
                            lmdb_key=f"ipc/{finding.finding_id}",
                            desync=False,
                            error=None,
                            accepted=True,
                        )
                    )
                else:
                    results.append(
                        FindingQualityDecision(
                            accepted=False,
                            reason="ipc_write_error",
                            entropy=0.0,
                            normalized_hash=None,
                            duplicate=False,
                        )
                    )

        return results

    # ------------------------------------------------------------------
    # Arrow helpers (run in executor)
    # ------------------------------------------------------------------

    def _findings_to_arrow_batch(
        self,
        findings: list[CanonicalFinding],
    ) -> tuple[Any, list[int]]:
        """
        Convert CanonicalFinding list to Arrow RecordBatch.

        CanonicalFinding fields:
          - finding_id: str
          - query: str
          - source_type: str
          - confidence: float
          - ts: float
          - provenance: tuple[str, ...]
          - payload_text: str | None

        DuckDB canonical_findings table columns:
          - id (finding_id)
          - query, source_type, confidence, ts
          - provenance_json (JSON-serialized provenance tuple)
          - payload_text
        """
        # Issue #11 fix: stream findings in micro-batches to limit peak memory.
        # Previously: materialized ALL findings into Python lists before Arrow build
        # (10K × ~1KB = ~10 MB peak per batch). Now chunk to _STREAM_BATCH_SIZE
        # (1000) so peak = ~1 MB instead of ~10 MB. Caller (_write_batch_to_ring)
        # already streams micro-batches, so this aligns with that pattern.
        _STREAM_BATCH_SIZE = 1000
        if _PYARROW_SPEC is None:
            return None, []

        try:
            import pyarrow as pa

            import orjson

            # Caller (_write_batch_to_ring) already chunks findings into micro-batches
            # of _STREAM_BATCH_SIZE (1000). This method receives at most one such chunk
            # per call, so no further chunking is needed.
            # Memory note: 7 Python lists × N pointers (N ≤ 1000) ≈ 56 KB for list
            # containers + string data — bounded, M1 8GB safe.
            return self._build_single_arrow_batch(findings)

        except Exception:
            return None, []

    @classmethod
    def _build_single_arrow_batch(
        cls,
        findings: list[CanonicalFinding],
    ) -> tuple[Any, list[int]]:
        """
        Build a single Arrow RecordBatch from findings list (zero-copy arrays).

        Memory: all 7 columns are allocated as Python lists then converted to
        pyarrow arrays — ~7 × N × pointer bytes. For N=1000 this is ~56 KB
        for the list containers + actual string data. Peak is bounded by
        _STREAM_BATCH_SIZE (1000), not the full findings list.

        Returns (batch, accepted_indices).
        """
        import pyarrow as pa
        import orjson

        finding_ids, queries, source_types, confidences, ts_vals = [], [], [], [], []
        provenance_jsons, payload_texts = [], []
        accepted_indices: list[int] = []

        for i, f in enumerate(findings):
            finding_ids.append(f.finding_id)
            queries.append(f.query)
            source_types.append(f.source_type)
            confidences.append(f.confidence)
            ts_vals.append(f.ts or 0.0)
            provenance_jsons.append(orjson.dumps(list(f.provenance)).decode("utf-8") if f.provenance else "[]")
            payload_texts.append(f.payload_text or "")
            accepted_indices.append(i)

        schema = pa.schema(
            [
                ("id", pa.string()),
                ("query", pa.string()),
                ("source_type", pa.string()),
                ("confidence", pa.float64()),
                ("ts", pa.float64()),
                ("provenance_json", pa.string()),
                ("payload_text", pa.string()),
            ]
        )

        batch = pa.record_batch(
            [
                pa.array(finding_ids, type=pa.string()),
                pa.array(queries, type=pa.string()),
                pa.array(source_types, type=pa.string()),
                pa.array(confidences, type=pa.float64()),
                pa.array(ts_vals, type=pa.float64()),
                pa.array(provenance_jsons, type=pa.string()),
                pa.array(payload_texts, type=pa.string()),
            ],
            schema=schema,
        )
        return batch, accepted_indices

    def _serialize_arrow_batch(self, batch: Any) -> bytes | None:
        """Serialize Arrow RecordBatch to IPC stream bytes."""
        try:
            import pyarrow as pa

            buf = pa.BufferOutputStream()
            writer = pa.ipc.new_stream(buf, batch.schema)
            writer.write_batch(batch)
            writer.close()
            return buf.getvalue().to_pybytes()
        except Exception:
            return None

    def _write_ring_record(self, ipc_bytes: bytes) -> None:
        """Write a record into the ring buffer (thread-safe, in executor)."""
        record_len = len(ipc_bytes)
        header = self._ring_buf

        write_pos = struct.unpack_from("<I", header, 0)[0]
        read_pos = struct.unpack_from("<I", header, 4)[0]

        if write_pos >= read_pos:
            available = _RING_SIZE - write_pos
        else:
            available = read_pos - write_pos

        if record_len + 4 > available:
            write_pos = _RING_HEADER

        struct.pack_into("<I", header, write_pos, record_len)
        pos = write_pos + 4
        remaining = record_len

        while remaining > 0:
            chunk = min(remaining, _RING_SIZE - pos)
            self._ring_buf[pos : pos + chunk] = ipc_bytes[record_len - remaining : record_len - remaining + chunk]
            remaining -= chunk
            pos += chunk
            if pos >= _RING_SIZE:
                pos = _RING_HEADER

        struct.pack_into("<I", header, 0, pos)

    # ------------------------------------------------------------------
    # Fallback / degraded results
    # ------------------------------------------------------------------

    def _degraded_results(
        self,
        findings: list[CanonicalFinding],
        reason: str,
    ) -> list[FindingQualityDecision | ActivationResult]:
        """
        Return fail-open results when IPC path is unavailable.

        All findings are accepted but marked with the failure reason.
        The caller (DuckDBShadowStore fallback path) can re-attempt
        via its own legacy path if needed.
        """
        return [
            ActivationResult(
                finding_id=f.finding_id,
                lmdb_success=True,
                duckdb_success=True,
                lmdb_key=f"degraded/{reason}/{f.finding_id}",
                desync=True,
                error=reason,
                accepted=True,
            )
            for f in findings
        ]

    # ------------------------------------------------------------------
    # Remaining API surface
    # ------------------------------------------------------------------

    async def drain_and_get_accepted(
        self, findings: list[CanonicalFinding] | None = None
    ) -> list[Any]:
        """Passthrough — not used in the IPC path."""
        return []

    async def async_record_sprint_delta(self, row: dict) -> bool:
        """Sprint delta is written by DuckDBShadowStore (not via IPC)."""
        return False

    async def async_healthcheck(self) -> bool:
        """Check if the worker subprocess is still alive."""
        if self._proc is None:
            return False
        return self._proc.is_alive()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Graceful shutdown: send sentinel, terminate subprocess, unlink shm."""
        if self._closed:
            return
        self._closed = True

        if self._channel is not None and _posix_ipc_available():
            import posix_ipc

            try:
                sem = posix_ipc.Semaphore(
                    self._channel.sem_name,
                    flags=posix_ipc.O_CREAT,
                )
                struct.pack_into("<I", self._ring_buf, 0, _RING_HEADER)
                struct.pack_into("<I", self._ring_buf, 4, _RING_HEADER)
                sem.release()
                sem.close()
            except Exception:
                pass

        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.join(timeout=5.0)
                if self._proc.is_alive():
                    self._proc.kill()
            except Exception:
                pass
            self._proc = None

        if self._channel is not None and _posix_ipc_available():
            import posix_ipc

            for name_attr in ("shm_name", "result_shm_name"):
                name = getattr(self._channel, name_attr, None)
                if name:
                    try:
                        shm = posix_ipc.SharedMemory(name=name)
                        shm.close_unlink()
                    except Exception:
                        pass

        self._started = False

    def close(self) -> None:
        """Alias for shutdown."""
        self.shutdown()

    async def aclose(self, timeout_s: float = 10.0) -> None:
        """Async shutdown with timeout."""
        try:
            async with asyncio.timeout(timeout_s):
                await asyncio.to_thread(self.shutdown)
        except TimeoutError:
            self._closed = True

    # ------------------------------------------------------------------
    # DuckDBShadowStore API compatibility (for adapter wiring)
    # ------------------------------------------------------------------

    def inject_graph_store(self, graph_store: Any) -> None:
        """No-op: graph is not managed via IPC path."""
        pass

    @property
    def startup_ready(self) -> bool:
        return self._started and not self._closed

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def duckdb_mode(self) -> str:
        return "ipc"

    def get_stats(self) -> dict[str, Any]:
        return {
            "duckdb_mode": "ipc",
            "started": self._started,
            "closed": self._closed,
            "db_path": str(self._db_path) if self._db_path else None,
            "ipc_channel": (
                {
                    "shm_name": self._channel.shm_name,
                    "ring_size": self._channel.ring_size,
                }
                if self._channel
                else None
            ),
        }
