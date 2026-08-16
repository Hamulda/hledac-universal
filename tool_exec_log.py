"""
from _core import aclose
ToolExecLog - Tamper-evident tool execution logging
===================================================



This module implements append-only logging for tool execution events.
Unlike EvidenceLog (which stores research evidence), ToolExecLog tracks
tool invocations with hashes for forensic audit.

M1 8GB Optimization:
- Ring buffer in RAM (max 100 events)
- SQLite WAL for batched persistence (one transaction per second)
- orjson for 3-5× faster serialization vs json
- Async write worker (non-blocking I/O, never swap on M1)
- silent_failure flag to bypass logging in pre-flight

S1-13 Fix (2026-07-30):
- Write queue capacity: 500 → 2000 (4× SQLite batch, 2× flush_interval)
- Overflow counter: _overflow_count tracks drops; warning on first overflow
- Change: QueueFull now logs warning once then increments counter (was debug/drop)

CRITICAL INVARIANTS (Issue 8.3):
- silent_failure=True → all log() calls return None, no I/O
- silent_failure=False (default) → async queue, batched fsync
- Never call blocking I/O in hot path (tool execution context)
"""
from __future__ import annotations
import asyncio
import hashlib
from hledac.universal.utils.asyncx import safe_create_task, safe_wait_for
import logging
import os
from collections import deque
from dataclasses import dataclass
import msgspec
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import orjson
from operator import attrgetter, itemgetter
from _core import aclose
logger = logging.getLogger(__name__)
BOUNDED_ERROR_CLASSES = frozenset(['TimeoutError', 'ConnectionError', 'HTTPError', 'ValueError', 'TypeError', 'AttributeError', 'KeyError', 'IOError', 'RuntimeError', 'CancelledError', 'AuthenticationError', 'PermissionError', 'NotFoundError', 'ValidationError', 'RateLimitError', 'CircuitBreakerError', 'Unknown'])
BOUNDED_STATUSES = frozenset(['success', 'error', 'cancelled'])
SHARED_CORRELATION_KEYS = frozenset(['run_id', 'branch_id', 'provider_id', 'action_id'])
"\nShared correlation key grammar.\n\nAll ledger planes MUST use these key names for cross-component correlation.\nDeviation = silent correlation loss in cross-plane queries.\n\nSCOPE SEMANTICS (intentional, NOT drift):\n- ToolExecLog:        per-event  — each log() call carries its own correlation dict\n- MetricsRegistry:    per-registry — set at __init__, batched on flush\n- EvidenceLog:        per-event  — each create_event carries correlation\n\nThese are efficiency trade-offs, not inconsistency. Tool audit events are\ndiscrete; metrics are aggregated. Unifying scope would sacrifice one plane's\ndesign for the other's convenience.\n"

def normalize_correlation(corr: dict[str, str | None] | None) -> dict[str, str | None] | None:
    """
    Normalize correlation dict to shared grammar.

    Returns canonical form:
    - Only keys in SHARED_CORRELATION_KEYS are present
    - Values are either str or None
    - Keys not in grammar are silently dropped (fail-soft hardening)

    This is a grammar seam for cross-plane correlation queries,
    NOT a general validator.
    """
    if corr is None:
        return None
    return {k: corr.get(k) for k in SHARED_CORRELATION_KEYS if k in corr}

class ToolExecEvent(msgspec.Struct, frozen=True, gc=False):
    """
    Tool execution event - bounded metadata only.

    Stores hashes instead of actual data to maintain:
    - Tamper-evidence (hash chain)
    - No sensitive data in logs
    - Forensic audit capability

    Migrated from @dataclass to msgspec.Struct (Issue #11):
    - 5× faster (de)serialization vs dataclass
    - Native JSON/MessagePack encode/decode (zero-copy from bytes)
    - Zero memory overhead vs dict-based storage
    """
    event_id: str
    ts: float
    tool_name: str
    input_hash: str
    output_hash: str
    output_len: int
    status: str
    error_class: str | None = None
    seq_no: int = 0
    prev_chain_hash: str | None = None
    chain_hash: str | None = None
    correlation: dict[str, str | None] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON (backward compatibility)."""
        result: dict[str, Any] = {'event_id': self.event_id, 'ts': datetime.fromtimestamp(self.ts, UTC).isoformat(), 'tool_name': self.tool_name, 'input_hash': self.input_hash, 'output_hash': self.output_hash, 'output_len': self.output_len, 'status': self.status, 'error_class': self.error_class, 'seq_no': self.seq_no, 'prev_chain_hash': self.prev_chain_hash, 'chain_hash': self.chain_hash}
        if self.correlation:
            result['correlation'] = self.correlation
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolExecEvent:
        """Deserialize from dict (backward compatibility)."""
        ts_val = data.get('ts')
        if isinstance(ts_val, str):
            ts_val = datetime.fromisoformat(ts_val).timestamp()
        elif isinstance(ts_val, datetime):
            ts_val = ts_val.timestamp()
        elif ts_val is None:
            ts_val = datetime.now(UTC).timestamp()
        return cls(event_id=data['event_id'], ts=ts_val, tool_name=data['tool_name'], input_hash=data['input_hash'], output_hash=data['output_hash'], output_len=data['output_len'], status=data['status'], error_class=data.get('error_class'), seq_no=data.get('seq_no', 0), prev_chain_hash=data.get('prev_chain_hash'), chain_hash=data.get('chain_hash'), correlation=data.get('correlation'))

class ToolExecLog:
    """
    Append-only tool execution log with hash-chain.

    ROLE (Sprint 8VF + Issue 8.3):
    ════════════════════════════════════════════════════════
    AUDIT/LOGGING boundary — NOT an execution authority.
    This class logs tool execution events for forensic audit
    and correlation. It does NOT execute tools.

    Issue 8.3 — M1 8GB Optimization:
    - SQLite WAL with async write worker (non-blocking I/O)
    - Batched transactions (one per second, not per event)
    - orjson for 3-5× faster serialization
    - silent_failure flag to bypass logging in pre-flight
    - Ring buffer in RAM (max 100 events)

    CORRELATION BOUNDARY:
    - Designed to wrap ToolRegistry.execute_with_limits() calls
    - ToolExecEvent.correlation dict carries: run_id, branch_id,
      provider_id, action_id for cross-referencing
    - Hash-chain provides tamper-evidence for audit

    Execution vs Audit separation:
    - ToolRegistry.execute_with_limits() → executes tools (canonical)
    - ToolExecLog.log()                → records execution (audit)

    DO NOT:
    - Execute tools here — use ToolRegistry for that
    - Make this a second execution authority
    - Store raw inputs/outputs (hashes only)

    RELATED COMPONENTS:
    - ToolRegistry:    CANONICAL execution (controls what runs)
    - GhostExecutor:   DONOR/COMPAT (legacy actions)
    - CapabilityRouter: SIGNAL mapping (capability recommendations)
    ════════════════════════════════════════════════════════

    Design principles:
    - NEVER store raw tool inputs/outputs
    - Store only hashes for tamper evidence
    - Bounded metadata (sizes, error types)
    - Disk-first with RAM ring buffer
    """
    MAX_RAM_EVENTS = 100
    MAX_OUTPUT_LEN = 1024 * 1024
    _SQLITE_BATCH_SIZE = 100
    _SQLITE_FLUSH_INTERVAL = 1.0
    # S1-13 fix: 500→2000 (2× batch × flush_interval = headroom for burst)
    _WRITE_QUEUE_MAXSIZE = 2000
    __slots__ = tuple(('_chain_head', '_closed', '_db', '_db_path', '_initialized', '_log', '_loop', '_persist_enabled', '_run_dir', '_run_id', '_seq', '_silent_failure', '_write_queue', '_write_shutdown', '_write_task', '_overflow_count'))

    def __init__(self, run_dir: Path, enable_persist: bool=True, run_id: str='default', silent_failure: bool=False):
        """
        Initialize ToolExecLog.

        Args:
            run_dir: Directory for SQLite persistence
            enable_persist: Whether to persist to disk (SQLite WAL)
            run_id: Run identifier for this execution
            silent_failure: If True, all log() calls return None without I/O.
                           Use for pre-flight / dry-run modes.
        """
        self._run_dir = run_dir
        self._persist_enabled = enable_persist
        self._run_id = run_id
        self._silent_failure = silent_failure
        self._seq = 0
        self._chain_head = 'genesis'
        self._log: deque = deque(maxlen=self.MAX_RAM_EVENTS)
        self._db_path: Path | None = None
        self._db: Any | None = None
        self._initialized = False
        self._write_queue: asyncio.Queue = asyncio.Queue(maxsize=self._WRITE_QUEUE_MAXSIZE)
        self._write_task: asyncio.Task | None = None
        self._write_shutdown: asyncio.Event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._overflow_count: int = 0  # S1-13: metric for queue overflow events
        if enable_persist and (not silent_failure):
            self._init_persist()
        logger.info(f'ToolExecLog initialized: run_id={run_id}, persist={enable_persist}, silent_failure={silent_failure}')

    def _init_persist(self) -> None:
        """Initialize SQLite WAL persistence."""
        log_dir = self._run_dir / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = log_dir / 'tool_exec.db'

    def _hash_bytes(self, data: bytes) -> str:
        """Compute SHA256 hash of bytes"""
        return hashlib.sha256(data).hexdigest()

    def _bound_error_class(self, error: Exception | None) -> str | None:
        """Bound error class name to safe set"""
        if error is None:
            return None
        error_name = type(error).__name__
        return error_name if error_name in BOUNDED_ERROR_CLASSES else 'Unknown'

    async def initialize(self) -> None:
        """
        Initialize async SQLite components and start write worker.

        Idempotent: safe to call multiple times.
        """
        if self._silent_failure or not self._persist_enabled:
            return
        if self._initialized:
            if self._write_task is None or self._write_task.done():
                self._write_task = safe_create_task(self._write_worker())
            return
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        import aiosqlite
        self._db = await aiosqlite.connect(str(self._db_path), check_same_thread=False)
        await self._db.execute('PRAGMA busy_timeout=30000')
        await self._db.execute('PRAGMA journal_mode=WAL')
        await self._db.execute('PRAGMA synchronous=NORMAL')
        await self._db.execute('PRAGMA wal_autocheckpoint=1000')
        await self._db.execute('PRAGMA cache_size=-8192')
        await self._db.execute('\n            CREATE TABLE IF NOT EXISTS events (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                seq_no INTEGER NOT NULL,\n                tool_name TEXT NOT NULL,\n                data TEXT NOT NULL,\n                hash TEXT NOT NULL,\n                ts REAL NOT NULL\n            )\n        ')
        await self._db.execute('\n            CREATE INDEX IF NOT EXISTS idx_events_seq ON events(seq_no)\n        ')
        await self._db.commit()
        self._write_task = safe_create_task(self._write_worker())
        self._initialized = True

    async def _write_worker(self) -> None:
        """Background worker that writes events to SQLite in batches."""
        import aiosqlite
        db: aiosqlite.Connection | None = None
        if self._db_path:
            try:
                db = await aiosqlite.connect(str(self._db_path), check_same_thread=False)
                await db.execute('PRAGMA busy_timeout=30000')
                await db.execute('PRAGMA journal_mode=WAL')
                await db.execute('PRAGMA synchronous=NORMAL')
            except Exception as e:
                logger.warning(f'[ToolExecLog] SQLite connect failed: {e}')
                db = None
        batch: list[tuple] = []
        last_flush = datetime.now(UTC)
        while True:
            try:
                try:
                    async with asyncio.timeout(1.0):
                        item = await self._write_queue.get()
                    batch.append(item)
                except TimeoutError:  # noqa: BLE001
                    pass
                if self._write_shutdown.is_set():
                    while True:
                        try:
                            item = self._write_queue.get_nowait()
                            batch.append(item)
                        except asyncio.QueueEmpty:
                            break
                    break
                if len(batch) >= self._SQLITE_BATCH_SIZE or (batch and (datetime.now(UTC) - last_flush).total_seconds() >= self._SQLITE_FLUSH_INTERVAL):
                    if db is not None:
                        try:
                            await db.executemany('INSERT INTO events (seq_no, event_type, data, hash, ts) VALUES (?, ?, ?, ?, ?)', batch)
                            await db.commit()
                        except Exception as e:
                            logger.warning(f'[ToolExecLog] Batch insert failed: {e}')
                    batch = []
                    last_flush = datetime.now(UTC)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f'[ToolExecLog] Write worker error: {e}')
        if batch and db is not None:
            try:
                await db.executemany('INSERT INTO events (seq_no, event_type, data, hash, ts) VALUES (?, ?, ?, ?, ?)', batch)
                await db.commit()
            except Exception as e:
                logger.warning(f'[ToolExecLog] Final batch flush failed: {e}')
        if self._overflow_count > 0:
            logger.warning(f'[ToolExecLog] Worker shutdown: {self._overflow_count} events overflowed during run')
        if db:
            await db.close()

    def log(self, tool_name: str, input_data: bytes, output_data: bytes, status: str, error: Exception | None=None, correlation: dict[str, str | None] | None=None) -> ToolExecEvent | None:
        """
        Log a tool execution event.

        Args:
            tool_name: Name of the tool executed
            input_data: Raw input bytes (will be hashed, not stored)
            output_data: Raw output bytes (will be hashed, not stored)
            status: "success" | "error" | "cancelled"
            error: Optional exception if status is "error"
            correlation: Optional correlation dict with keys:
                run_id, branch_id, provider_id, action_id

        Returns:
            The created ToolExecEvent, or None if silent_failure is True

        Raises:
            RuntimeError: If log has been finalized/closed and can no longer
                truthfully persist events.
        """
        import uuid
        if self._closed:
            raise RuntimeError('ToolExecLog.log() called after finalize()/close(): audit trail is closed, refusing to log event')
        if self._silent_failure:
            return None
        input_hash = self._hash_bytes(input_data) if input_data else ''
        output_len = min(len(output_data), self.MAX_OUTPUT_LEN)
        output_hash = self._hash_bytes(output_data[:self.MAX_OUTPUT_LEN]) if output_data else ''
        error_class = self._bound_error_class(error)
        if status not in BOUNDED_STATUSES:
            status = 'error'
        correlation = normalize_correlation(correlation)
        self._seq += 1
        event_id = f'tool_{self._seq}_{uuid.uuid4().hex[:8]}'
        chain_input = f'{self._chain_head}:{event_id}:{input_hash}:{output_hash}:{status}:{error_class}'
        chain_hash = hashlib.sha256(chain_input.encode()).hexdigest()
        event = ToolExecEvent(event_id=event_id, ts=datetime.now(UTC).timestamp(), tool_name=tool_name, input_hash=input_hash, output_hash=output_hash, output_len=output_len, status=status, error_class=error_class, seq_no=self._seq, prev_chain_hash=self._chain_head, chain_hash=chain_hash, correlation=correlation)
        self._chain_head = chain_hash
        if self._persist_enabled and self._write_task is not None and (not self._write_task.done()):
            try:
                record: tuple[int, str, str, str, float] = (event.seq_no, tool_name, orjson.dumps(event.to_dict()).decode(), chain_hash, event.ts)
                self._write_queue.put_nowait(record)
            except asyncio.QueueFull:
                self._overflow_count += 1
                if self._overflow_count == 1:
                    logger.warning(
                        f'[ToolExecLog] Write queue overflow ({self._WRITE_QUEUE_MAXSIZE} full), '
                        f'counting overflow events (last seq={event.seq_no})'
    )
            except RuntimeError:  # noqa: BLE001
                pass
        self._log.append(event)
        return event

    def verify_all(self) -> dict[str, Any]:
        """
        Verify the entire chain for tampering.

        Returns:
            Dict with:
                - chain_valid: bool
                - head_hash: str
                - event_count: int
                - first_seq: int
                - errors: list of issues
        """
        import sqlite3
        events: list[ToolExecEvent] = []
        if self._db_path and self._db_path.exists():
            try:
                conn = sqlite3.connect(str(self._db_path))
                conn.execute('PRAGMA busy_timeout=30000')
                conn.execute('PRAGMA journal_mode=WAL')
                cursor = conn.execute('SELECT seq_no, tool_name, data, hash, ts FROM events ORDER BY seq_no')
                for row in cursor:
                    seq_no, _tool_name, data, hash_val, ts = row
                    event_data = orjson.loads(data)
                    event_data['seq_no'] = seq_no
                    event_data['chain_hash'] = hash_val
                    event_data['timestamp'] = ts
                    events.append(ToolExecEvent.from_dict(event_data))
                conn.close()
            except Exception as e:
                logger.warning(f'[ToolExecLog] verify_all failed to read DB: {e}')
        ram_seqs = {e.seq_no for e in self._log}
        for event in self._log:
            if event.seq_no not in ram_seqs:
                events.append(event)
        events.sort(key=attrgetter("seq_no"))
        errors = []
        expected_head = 'genesis'
        for event in events:
            if event.prev_chain_hash != expected_head:
                errors.append(f'Chain break at seq {event.seq_no}: expected prev={expected_head}, got {event.prev_chain_hash}')
            chain_input = f'{expected_head}:{event.event_id}:{event.input_hash}:{event.output_hash}:{event.status}:{event.error_class}'
            expected_chain = hashlib.sha256(chain_input.encode()).hexdigest()
            if event.chain_hash != expected_chain:
                errors.append(f"Hash mismatch at seq {event.seq_no}: expected {expected_chain[:16]}..., got {(event.chain_hash or '')[:16]}...")
            expected_head = event.chain_hash
        return {'chain_valid': not errors, 'head_hash': self._chain_head, 'event_count': len(events), 'first_seq': events[0].seq_no if events else 0, 'errors': errors}

    def get_head_hash(self) -> str:
        """Get current chain head hash"""
        return self._chain_head

    def get_stats(self) -> dict[str, Any]:
        """Get log statistics"""
        return {'seq': self._seq, 'ram_events': len(self._log), 'head_hash': self._chain_head, 'run_id': self._run_id, 'persist_enabled': self._persist_enabled, 'silent_failure': self._silent_failure, 'closed': self._closed}

    async def aclose(self) -> None:
        """Async close — signals shutdown, waits for worker, closes DB."""
        if self._closed:
            return
        self._closed = True
        self._write_shutdown.set()
        if self._write_task and (not self._write_task.done()):
            try:
                await safe_wait_for(self._write_task, timeout=5.0, label='_write_task')
            except (TimeoutError, asyncio.CancelledError):
                self._write_task.cancel()
                try:
                    await self._write_task
                except asyncio.CancelledError:  # noqa: BLE001
                    pass
        if self._db:
            try:
                await self._db.close()
            except Exception:  # noqa: BLE001
                pass
            self._db = None

    def close(self) -> None:
        """Close log (sync alias for aclose)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            safe_create_task(self.aclose())
        else:
            try:
                if loop:
                    loop.run_until_complete(self.aclose())
            except Exception:  # noqa: BLE001
                pass
        self._closed = True

    def finalize(self) -> None:
        """Finalize log - alias for close."""
        self.close()

    def __enter__(self) -> ToolExecLog:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

def create_tool_exec_log(run_dir: Path, run_id: str='default', silent_failure: bool=False) -> ToolExecLog:
    """Create a ToolExecLog instance"""
    return ToolExecLog(run_dir=run_dir, run_id=run_id, silent_failure=silent_failure)