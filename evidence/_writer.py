"""
evidence/_writer.py — Evidence Writer for event creation and persistence.

Write path: event creation, serialization, MPSC enqueuing.

Architecture (Sprint Split-Brain):
- EvidenceWriter: Write path (create_event, persist, chain hash)
- EvidenceQuery: Read path (get, query, verify)
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import logging
import os
import secrets
import threading
import time
import uuid
import zlib
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec
import orjson

from hledac.universal.utils.asyncx import safe_create_task, safe_wait_for
from core import aclose

logger = logging.getLogger(__name__)

# ─── EvidenceEvent ──────────────────────────────────────────────────────────────


class EvidenceEvent(msgspec.Struct, frozen=False, gc=False):
    """
    Immutable evidence packet stored in the ledger.

    Chain: event_id → source_ids → chain_hash
    Tamper-evident: hash chain links events to prior state.
    """
    __slots__ = ('event_id', 'event_type', 'timestamp', 'payload', 'run_id',
                 'source_ids', 'confidence', 'content_hash', 'seq_no',
                 'prev_chain_hash', 'chain_hash')

    event_id: str
    event_type: str
    timestamp: float
    payload: dict[str, Any]
    run_id: str
    source_ids: list[str]
    confidence: float
    content_hash: str
    seq_no: int
    prev_chain_hash: str
    chain_hash: str

    @classmethod
    def create(
        cls, event_id: str, event_type: str, payload: dict[str, Any],
        run_id: str, source_ids: list[str] | None = None,
        confidence: float = 1.0, seq_no: int = 0, prev_chain_hash: str | None = None,
    ) -> EvidenceEvent:
        """Create a new evidence event with hash chain."""
        timestamp = datetime.now(UTC).timestamp()
        source_ids = source_ids or []
        content_hash = cls._calculate_hash(event_id, event_type, timestamp, payload, source_ids, confidence, run_id)
        chain_hash = cls._compute_chain_hash(prev_chain_hash, content_hash, event_id)
        return cls(
            event_id=event_id, event_type=event_type, timestamp=timestamp,
            payload=payload, run_id=run_id, source_ids=source_ids,
            confidence=confidence, content_hash=content_hash, seq_no=seq_no,
            prev_chain_hash=prev_chain_hash or '', chain_hash=chain_hash,
        )

    @staticmethod
    def _calculate_hash(
        event_id: str, event_type: str, timestamp: float,
        payload: dict[str, Any], source_ids: list[str], confidence: float, run_id: str,
    ) -> str:
        """Calculate content hash."""
        data = f"{event_id}|{event_type}|{timestamp}|{orjson.dumps(payload)}|{source_ids}|{confidence}|{run_id}"
        return hashlib.sha256(data.encode()).hexdigest()[:32]

    @staticmethod
    def _compute_chain_hash(prev_hash: str | None, content_hash: str, event_id: str) -> str:
        """Compute chain hash linking to previous event."""
        data = f"{(prev_hash or '')}|{content_hash}|{event_id}"
        return hashlib.sha256(data.encode()).hexdigest()[:32]

    def calculate_hash(self) -> str:
        """Recalculate content hash for verification."""
        return self._calculate_hash(
            self.event_id, self.event_type, self.timestamp,
            self.payload, self.source_ids, self.confidence, self.run_id,
        )

    def payload_dict(self) -> dict[str, Any]:
        """Return payload as dict."""
        return self.payload

    def verify_integrity(self) -> bool:
        """Verify content hash matches."""
        return self.calculate_hash() == self.content_hash

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return msgspec.convert(self, dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceEvent:
        """Deserialize from dict."""
        return msgspec.convert(data, cls)

    def to_jsonl_line(self) -> str:
        """Serialize to JSONL line."""
        return orjson.dumps(self.to_dict()).decode('utf-8', errors='replace')

    def to_bytes(self) -> bytes:
        """Serialize to msgpack bytes."""
        return msgspec.msgpack.encode(self)

    @classmethod
    def from_bytes(cls, data: bytes) -> EvidenceEvent:
        """Deserialize from msgpack bytes."""
        return msgspec.msgpack.decode(data, type=cls)


# ─── _RustMPSCBytes ──────────────────────────────────────────────────────────────


class _RustMPSCBytes:
    """
    Rust MPSC pool for high-throughput async event ingestion.

    ISSUE-006: bytes-only — serialization is caller's responsibility.
    Falls back to asyncio.Queue when Rust is unavailable.
    """
    __slots__ = ('_impl', '_pool', '_sender_ptr', '_queue', '_capacity',
                 '_retry_handle', '_pending_retry', '_retry_delay',
                 '_rust_unavailable', '_fallback', '_wake_fd')

    def __init__(self, capacity: int = 2048, asyncio_fallback: bool = False) -> None:
        self._impl = 'rust'
        self._pool: Any = None
        self._sender_ptr: int = 0
        self._queue: asyncio.Queue[bytes] | None = None
        self._capacity = capacity
        self._retry_handle: Any = None
        self._pending_retry = False
        self._retry_delay = 5.0
        self._rust_unavailable = True
        self._fallback = asyncio_fallback
        self._wake_fd = 0
        self._init_rust(capacity, asyncio_fallback)

    def _init_rust(self, capacity: int, asyncio_fallback: bool) -> None:
        """Try to initialize Rust MPSC."""
        try:
            from hledac.universal.core.rust_backend import rust
            _MPSC = rust.raw.MPSCPool  # type: ignore[import]
            pool = _MPSC(capacity=capacity)
            sender_ptr = pool.add_sender()
            wake_fd = pool.wake_fd()
            self._pool = pool
            self._sender_ptr = sender_ptr
            self._wake_fd = wake_fd
            self._rust_unavailable = False
            self._impl = 'rust'
        except Exception:
            self._fallback = True
            self._impl = 'asyncio'
            self._queue = asyncio.Queue(maxsize=capacity)

    def send(self, item: bytes) -> bool:
        """Send raw bytes to the pool."""
        if self._impl == 'rust' and self._pool is not None:
            try:
                return self._pool.send(self._sender_ptr, item)
            except Exception:
                return False
        elif self._queue is not None:
            try:
                self._queue.put_nowait(item)
                return True
            except asyncio.QueueFull:
                return False
        return False

    def send_batch(self, items: list[bytes]) -> int:
        """Send multiple items via Rust send_batch()."""
        if not items:
            return 0
        if self._impl == "rust" and self._pool is not None:
            try:
                return self._pool.send_batch(self._sender_ptr, items)
            except Exception:
                return 0
        elif self._queue is not None:
            sent = 0
            for item in items:
                try:
                    self._queue.put_nowait(item)
                    sent += 1
                except asyncio.QueueFull:
                    break
            return sent
        return 0

    async def send_async(self, item: bytes, *, timeout: float = 1.0) -> bool:
        """Async send with backpressure."""
        if self._impl == 'rust' and self._pool is not None:
            return self.send(item)
        elif self._queue is not None:
            try:
                await safe_wait_for(self._queue.put(item), timeout=timeout)
                return True
            except asyncio.TimeoutError:
                return False
            except Exception:
                return False
        return False

    def recv_batch(self, max_items: int | None = None) -> list[bytes]:
        """Drain up to max_items as raw bytes (non-blocking)."""
        if self._impl == 'rust' and self._pool is not None:
            try:
                return self._pool.recv_batch(max_items)
            except Exception:
                return []
        elif self._queue is not None:
            batch = []
            while len(batch) < (max_items or 9999):
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            return batch
        return []

    def wake_fd(self) -> int:
        """Pipe read fd for asyncio reader registration."""
        return self._wake_fd

    def __len__(self) -> int:
        if self._impl == 'rust' and self._pool is not None:
            return self._pool.len()
        elif self._queue is not None:
            return self._queue.qsize()
        return 0

    def is_empty(self) -> bool:
        return len(self) == 0


# ─── EvidenceWriter ──────────────────────────────────────────────────────────────


class EvidenceWriter:
    """
    Write path for evidence events.

    Sprint Split-Brain: Extracted from EvidenceLog to isolate
    write path from read path. Enables independent testing and
    write-only workflows.
    """

    __slots__ = (
        '_mpsc', '_run_id', '_seq', '_chain_head', '_genesis_hash',
        '_total_count', '_persist_path', '_persist_file',
        '_flush_interval_s', '_last_flush', '_flush_task',
        '_shutdown_event', '_shutdown', '_running',
    )

    def __init__(self, run_id: str, persist_path: Path | None = None) -> None:
        self._mpsc = _RustMPSCBytes(capacity=2048)
        self._run_id = run_id
        self._seq = 0
        self._chain_head: str | None = None
        self._genesis_hash = hashlib.sha256(run_id.encode()).hexdigest()[:32]
        self._total_count = 0
        self._persist_path = persist_path
        self._persist_file = None
        self._flush_interval_s = 1.5
        self._last_flush = time.monotonic()
        self._flush_task: asyncio.Task | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._shutdown = False
        self._running = False

        if persist_path:
            persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_file = open(persist_path, 'ab')

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def size(self) -> int:
        return self._total_count

    @property
    def chain_head(self) -> str | None:
        return self._chain_head

    def _send_to_mpsc(self, event: EvidenceEvent) -> None:
        """Send event to MPSC."""
        self._mpsc.send(event.to_bytes())

    def _persist_event(self, event: EvidenceEvent) -> None:
        """Persist event to file."""
        if self._persist_file:
            line = event.to_jsonl_line() + '\n'
            self._persist_file.write(line.encode('utf-8'))

    def _compute_chain_hash(self, prev_hash: str | None, content_hash: str, event_id: str) -> str:
        """Compute chain hash."""
        data = f"{(prev_hash or '')}|{content_hash}|{event_id}"
        return hashlib.sha256(data.encode()).hexdigest()[:32]

    def write_event(self, event: EvidenceEvent) -> None:
        """Write event to MPSC and persist."""
        self._seq += 1
        self._total_count += 1
        self._chain_head = event.chain_hash
        self._send_to_mpsc(event)
        self._persist_event(event)

    def create_event(
        self, event_type: str, payload: dict[str, Any],
        source_ids: list[str] | None = None, confidence: float = 1.0,
    ) -> EvidenceEvent:
        """Create and write a new evidence event."""
        event_id = f"ev_{self._run_id[:8]}_{self._seq + 1:06d}"
        event = EvidenceEvent.create(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            run_id=self._run_id,
            source_ids=source_ids or [],
            confidence=confidence,
            seq_no=self._seq + 1,
            prev_chain_hash=self._chain_head,
        )
        self.write_event(event)
        return event

    def flush(self) -> None:
        """Flush persist file."""
        if self._persist_file:
            self._persist_file.flush()

    def close(self) -> None:
        """Close writer."""
        self._shutdown = True
        if self._persist_file:
            try:
                self._persist_file.flush()
                self._persist_file.close()
            except Exception:
                pass
            self._persist_file = None

    @property
    def is_frozen(self) -> bool:
        return self._shutdown
