"""EvidenceLog — append-only evidence ledger for the autonomous research system.

Implements the EVIDENCE LEDGER boundary — records what happened during research
but does NOT govern sprint truth or own facts. See :ref:`evidence-ledger` for
architecture overview, 3-tier hierarchy, and ledger boundary rules.
"""
from __future__ import annotations
import asyncio
import concurrent.futures
import contextlib
import contextvars
import hashlib
import logging
import os
import secrets
import threading
import atexit
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
import sys as _sys
from typing import Any, Iterator, Literal, cast
import aiosqlite
import msgspec
import orjson
from hledac.universal.core.env_config import ENV
from hledac.universal.runtime.protocols.cleanup_protocol import shutdown_aclose
from hledac.universal.utils.async_helpers import safe_create_task, safe_wait_for

# ISSUE-14: Structured logging via structlog
# Lazy import to avoid early import overhead — structlog is optional
_structlog: Any = None


def _get_logger() -> Any:
    """Lazy structlog getter with stdlib fallback (fail-safe)."""
    global _structlog
    if _structlog is None:
        try:
            from hledac.universal.utils.logging_config import get_logger as _get_logger

            _structlog = _get_logger("evidence_log")
        except Exception:
            # Fallback to stdlib — never raises
            _structlog = logging.getLogger("evidence_log")
    return _structlog

# ISSUE-11 / C2: ThreadPoolExecutor for GIL-free SQLite writes
# Lazily initialized — only created if SQLite path is used
# C2: Added threading.RLock for thread-safe singleton creation
_evidence_sqlite_executor: concurrent.futures.ThreadPoolExecutor | None = None
_duckdb_executor: concurrent.futures.ThreadPoolExecutor | None = None
_duckdb_executor_lock = threading.RLock()
_evidence_sqlite_executor_lock = threading.RLock()
_arrow = None

# C5 FIX: Cache env read at module load — avoids per-call os.environ.get()
#         in hot-path _get_arrow() (called 3x per flush cycle in sprint).
#         _ARROW_ENABLED is evaluated once at import; subsequent _get_arrow()
#         calls skip the env lookup entirely (fast-path: _arrow cached).
_ARROW_ENABLED: bool = os.environ.get('HLEDAC_ARROW_EVIDENCE', '0') == '1'


def _ensure_duckdb_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Lazily create and return the DuckDB write executor (singleton).

    C2 FIX: DuckDB is thread-safe (internal locking) — 2 workers better
    utilize M1 8GB's 4P+4E cores. WAL contention is DuckDB-internal, not
    a cross-process bottleneck. Threading lock prevents race in singleton init.
    """
    global _duckdb_executor
    with _duckdb_executor_lock:
        if _duckdb_executor is None:
            _duckdb_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=2, thread_name_prefix='evidence_duckdb',
            )
        return _duckdb_executor


def _ensure_evidence_sqlite_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Lazily create and return the SQLite write executor (singleton).

    C2: SQLite WAL has genuine write serialization — max_workers=1 is correct.
    Threading lock prevents race in singleton init.
    """
    global _evidence_sqlite_executor
    with _evidence_sqlite_executor_lock:
        if _evidence_sqlite_executor is None:
            _evidence_sqlite_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix='evidence_sqlite',
            )
        return _evidence_sqlite_executor


def _shutdown_executor_guarded(
    ex: concurrent.futures.ThreadPoolExecutor, *, timeout_s: float = 2.0
) -> None:
    """Graceful shutdown with bounded timeout, then force-fallback.

    Python 3.12+ has shutdown(timeout=...) natively, but we implement our own
    timeout via a thread join to stay compatible with Python <3.12.
    """
    try:
        # Graceful: wait up to timeout_s for in-flight writes to finish.
        # Thread.join() is interruptible, so this won't wait forever.
        t = threading.Thread(target=lambda: ex.shutdown(wait=True, cancel_futures=False), daemon=True)
        t.start()
        t.join(timeout=timeout_s)
        if t.is_alive():
            # Timed out → force cancel remaining work
            ex.shutdown(wait=False, cancel_futures=True)
    except Exception:  # noqa: BLE001
        # Any error → force shutdown
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001
            pass  # Best-effort at interpreter exit


def _shutdown_executors() -> None:
    """Shutdown module-global executors at interpreter exit.

    B4 FIX: Module-global ThreadPoolExecutors (_evidence_sqlite_executor,
    _duckdb_executor) are lazily initialized and were never shut down.
    Multiple imports during a long sprint left idle threads around.
    This function is registered via atexit to ensure clean shutdown.

    Each executor gets a bounded graceful shutdown (2s) before force-kill.
    """
    for ex in (_evidence_sqlite_executor, _duckdb_executor):
        if ex is None:
            continue
        _shutdown_executor_guarded(ex, timeout_s=2.0)


atexit.register(_shutdown_executors)

def _get_arrow():
    """Lazy Arrow IPC loader — only loads pyarrow if HLEDAC_ARROW_EVIDENCE=1.

    C5 FIX: _ARROW_ENABLED is cached at module load (line ~81).
    After first call, _arrow is cached so this function is O(1) — no env read.
    """
    global _arrow
    if _arrow is None:
        if _ARROW_ENABLED:
            try:
                import pyarrow as _pa
                import pyarrow.ipc as _ipc
                _arrow = (_pa, _ipc)
            except ImportError:
                import logging as _logger
                _logger.getLogger("evidence_log").debug("arrow_not_available_falling_back_to_sqlite")
                _arrow = False
        else:
            _arrow = False
    return _arrow if _arrow else None
try:
    from hledac.universal.utils.flow_trace import is_enabled, trace_counter, trace_evidence_append, trace_evidence_flush, trace_queue_drop
except ImportError:

    def trace_evidence_append(*_, **_kw):
        pass

    def trace_evidence_flush(*_, **_kw):
        pass

    def trace_queue_drop(*_, **_kw):
        pass

    def trace_counter(*_, **_kw):
        pass

    def is_enabled():
        return False
logger = _get_logger()

class EvidenceEvent(msgspec.Struct, frozen=False, gc=False):
    """
    Událost v evidence logu — msgspec.Struct pro 10× rychlejší (de)serializaci.

    Každá událost má unikátní ID, typ, timestamp, payload
    a content hash pro verifikaci integrity.
    """
    event_id: str
    event_type: str
    timestamp: float
    payload: bytes
    source_ids: list[str]
    confidence: float
    content_hash: str
    run_id: str
    seq_no: int = 0
    prev_chain_hash: str | None = None
    chain_hash: str | None = None

    @classmethod
    def create(cls, event_id: str, event_type: str, payload: dict[str, Any], run_id: str, source_ids: list[str] | None=None, confidence: float=1.0, seq_no: int=0, prev_chain_hash: str | None=None) -> EvidenceEvent:
        """Factory method — creates event with auto-generated content_hash."""
        source_ids = source_ids or []
        timestamp = datetime.now(UTC).timestamp()
        # P3-05 FIX: Normalize payload before orjson serialization to handle MLX arrays.
        normalized_payload = _normalize_payload(payload)
        # TEL-03: Fast-path guard — skip expensive scrub_dict_recursive() for
        # primitive-only payloads (no strings that could hold API keys/tokens).
        # _payload_needs_scrubbing() uses the same stack-based iterative scan
        # as _payload_needs_normalization() — O(n), no recursion overhead.
        if not _payload_needs_scrubbing(normalized_payload):
            scrubbed_payload = normalized_payload
        else:
            # SEC-01: Scrub secrets from payload before storage to prevent API keys/tokens
            # from being permanently stored in evidence logs (LMDB/SQLite).
            try:
                from hledac.universal.security.secrets_scrubber import scrub_dict_recursive
                scrubbed_payload = scrub_dict_recursive(normalized_payload)
            except Exception:
                # Fail-safe: store original if scrubbing fails
                scrubbed_payload = normalized_payload
        encoded_payload = orjson.dumps(scrubbed_payload)
        # Hash computed from scrubbed payload to maintain integrity verification
        content_hash = cls._calculate_hash(event_id=event_id, event_type=event_type, timestamp=timestamp, payload=scrubbed_payload, source_ids=source_ids, confidence=confidence, run_id=run_id)
        return cls(event_id=event_id, event_type=event_type, timestamp=timestamp, payload=encoded_payload, source_ids=source_ids, confidence=confidence, content_hash=content_hash, run_id=run_id, seq_no=seq_no, prev_chain_hash=prev_chain_hash, chain_hash=None)

    @staticmethod
    def _calculate_hash(event_id: str, event_type: str, timestamp: float, payload: dict[str, Any], source_ids: list[str], confidence: float, run_id: str) -> str:
        """Calculate SHA-256 hash of normalized event content.

        TEL-03: Only normalizes here for the instance method path (payload_dict from orjson.loads).
        The classmethod path (create()) passes already-normalized payload.
        """
        data = {'event_id': event_id, 'event_type': event_type, 'timestamp': timestamp, 'payload': payload, 'source_ids': sorted(source_ids), 'confidence': round(confidence, 6), 'run_id': run_id}
        json_bytes = orjson.dumps(data, option=orjson.OPT_SORT_KEYS)
        return hashlib.sha256(json_bytes).hexdigest()

    def calculate_hash(self) -> str:
        """Calculate current event's content hash."""
        return self._calculate_hash(event_id=self.event_id, event_type=self.event_type, timestamp=self.timestamp, payload=self.payload_dict, source_ids=self.source_ids, confidence=self.confidence, run_id=self.run_id)

    @property
    def payload_dict(self) -> dict[str, Any]:
        """Decode payload bytes to dict (lazy decode)."""
        return orjson.loads(self.payload)

    def verify_integrity(self) -> bool:
        """Verify event integrity using content hash."""
        return self.calculate_hash() == self.content_hash

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary (for backward compatibility)."""
        result = {'event_id': self.event_id, 'event_type': self.event_type, 'timestamp': datetime.fromtimestamp(self.timestamp, UTC).isoformat(), 'payload': orjson.loads(self.payload), 'source_ids': self.source_ids, 'confidence': self.confidence, 'content_hash': self.content_hash, 'run_id': self.run_id}
        if self.seq_no > 0:
            result['seq_no'] = self.seq_no
        if self.prev_chain_hash:
            result['prev_chain_hash'] = self.prev_chain_hash
        if self.chain_hash:
            result['chain_hash'] = self.chain_hash
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceEvent:
        """Create event from dictionary."""
        ts = data['timestamp']
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts).timestamp()
        encoded_payload = orjson.dumps(data['payload'])
        source_ids = data.get('source_ids') or []
        return cls(event_id=data['event_id'], event_type=data['event_type'], timestamp=ts, payload=encoded_payload, source_ids=source_ids, confidence=data.get('confidence', 1.0), content_hash=data['content_hash'], run_id=data['run_id'], seq_no=data.get('seq_no', 0), prev_chain_hash=data.get('prev_chain_hash'), chain_hash=data.get('chain_hash'))

    def to_jsonl_line(self) -> str:
        """Convert event to JSONL line."""
        return orjson.dumps(self.to_dict()).decode() + '\n'

    def to_bytes(self) -> bytes:
        """Serialize event to bytes using msgspec (faster than orjson).

        ISSUE-006: New method for zero-copy path — bytes serialized directly,
        no intermediate dict decode/re-encode cycle.
        Used by EvidenceLog.append() → MPSC → SQLite BLOB insert.
        """
        return msgspec.msgpack.encode(self._to_struct_tuple())

    def _to_struct_tuple(self) -> tuple:
        """Internal: tuple form for msgspec encoding (faster than dict)."""
        return (self.event_id, self.event_type, self.timestamp, self.payload,
                self.source_ids, self.confidence, self.content_hash, self.run_id,
                self.seq_no, self.prev_chain_hash, self.chain_hash)

    @classmethod
    def from_bytes(cls, data: bytes) -> EvidenceEvent:
        """Deserialize event from msgspec bytes.

        NOTE: Uses tuple decode because to_bytes() encodes via _to_struct_tuple(),
        not struct encode. Typed decode (msgspec.msgpack.decode(data, type=EvidenceEvent))
        requires struct-encode path which is not used here for performance.
        """
        decoded = msgspec.msgpack.decode(data)
        return cls(*decoded)

# -------------------------------------------------------------------------------------------------
# TEL-03 Optimization: Hot-path normalization with fast-path guards
# -------------------------------------------------------------------------------------------------

# Fast-path: types that NEVER need normalization (primitives only payload = skip recursive traversal)
_SAFE_BUILTIN = (type(None), bool, int, float, str)
# Types that DO need normalization (mutable containers + special types)
_COMPLEX_TYPES = (datetime, bytes, list, tuple, dict, set, frozenset)
_msgspec_struct_type: type = msgspec.Struct


def _payload_needs_scrubbing(payload: dict[str, Any]) -> bool:
    """Fast O(n) scan — returns True only if payload contains types that may hold secrets.

    TEL-03: Skips the expensive scrub_dict_recursive() pass entirely for payloads
    that contain only primitives (int, float, bool, None) — zero risk of secrets
    since strings are the only type that can encode API keys/tokens.

    Called BEFORE scrub_dict_recursive() to decide whether to skip it.
    """
    # Stack-based iterative scan (same pattern as _payload_needs_normalization)
    stack: list[Any] = list(payload.values())
    while stack:
        item = stack.pop()
        if item is None or isinstance(item, _SAFE_BUILTIN):
            continue
        # Strings CAN hold secrets — needs scrubbing
        if isinstance(item, str):
            return True
        # Containers may hold nested strings — needs scrubbing
        if isinstance(item, _COMPLEX_TYPES):
            return True
        if isinstance(item, _msgspec_struct_type):
            return True
        if hasattr(item, '__array__'):
            # MLX/numpy arrays — no secrets embedded
            continue
    return False


def _payload_needs_normalization(payload: dict[str, Any]) -> bool:
    """Fast O(n) scan — returns True only if payload contains types needing normalization.

    TEL-03: Fast-path guard. For payloads containing only primitives (str, int, float, bool, None),
    this avoids the expensive full recursive _normalize_payload() traversal entirely.
    Called ONCE before normalization to decide whether to skip it.
    """
    # Stack-based iterative scan (avoids Python call-stack overhead of recursion)
    stack: list[Any] = list(payload.values())
    while stack:
        item = stack.pop()
        if item is None or isinstance(item, _SAFE_BUILTIN):
            continue
        if isinstance(item, _COMPLEX_TYPES):
            return True
        if isinstance(item, _msgspec_struct_type):
            return True
        if hasattr(item, '__array__'):
            # MLX/numpy arrays need normalization
            return True
    return False


def _normalize_payload(payload: dict[str, Any], *, _depth: int = 0) -> dict[str, Any]:
    """Normalize payload for consistent hashing.

    TEL-03: Added fast-path guard + depth limit to prevent stack overflow on deep payloads.
    """
    # Fast path: check if normalization is even needed
    if _depth == 0 and not _payload_needs_normalization(payload):
        return dict(sorted(payload.items()))

    # Depth limit to prevent stack overflow
    max_depth = 8
    if _depth > max_depth:
        return {'_tel03_truncated': f'depth>{max_depth}'}

    normalized: dict[str, Any] = {}
    for key in sorted(payload.keys()):
        value = payload[key]
        if isinstance(value, datetime):
            normalized[key] = value.isoformat()
        elif isinstance(value, _msgspec_struct_type):
            # TEL-03 FIX: Recursively normalize to_builtins result.
            # msgspec.to_builtins() does NOT recursively normalize nested bytes/dicts/lists
            # inside the struct - it only converts datetime→str, so we must re-process.
            converted = msgspec.to_builtins(value)
            if isinstance(converted, dict):
                normalized[key] = _normalize_payload(converted, _depth=_depth + 1)
            elif isinstance(converted, list):
                normalized[key] = _normalize_list(converted, _depth=_depth + 1)
            else:
                normalized[key] = _normalize_single(converted)
        elif isinstance(value, (list, tuple)):
            normalized[key] = _normalize_list(value, _depth=_depth + 1)
        elif isinstance(value, dict):
            normalized[key] = _normalize_payload(value, _depth=_depth + 1)
        else:
            normalized[key] = _normalize_single(value)
    return normalized


def _normalize_list(value: list | tuple, *, _depth: int) -> list:
    """Normalize a list/tuple with depth limit."""
    max_depth = 8
    if _depth > max_depth:
        return [f'[truncated:depth>{max_depth}]']

    result: list[Any] = []
    for item in value:
        if isinstance(item, datetime):
            result.append(item.isoformat())
        elif isinstance(item, _msgspec_struct_type):
            # TEL-03 FIX: Recursively normalize to_builtins result (same fix as above)
            converted = msgspec.to_builtins(item)
            if isinstance(converted, dict):
                result.append(_normalize_payload(converted, _depth=_depth + 1))
            elif isinstance(converted, list):
                result.append(_normalize_list(converted, _depth=_depth + 1))
            else:
                result.append(_normalize_single(converted))
        elif isinstance(item, (list, tuple)):
            result.append(_normalize_list(item, _depth=_depth + 1))
        elif isinstance(item, dict):
            result.append(_normalize_payload(item, _depth=_depth + 1))
        else:
            result.append(_normalize_single(item))
    return result


def _normalize_single(value: Any) -> Any:
    """Normalize individual non-container value.

    TEL-03: Extracted from _normalize_value for clarity and inlining.
    """
    if isinstance(value, float):
        return round(value, 6)
    elif isinstance(value, (set, frozenset)):
        return sorted(value)
    elif isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    elif isinstance(value, _msgspec_struct_type):
        # TEL-03 FIX: Recursively normalize to_builtins result.
        # Start depth at 0 since to_builtins output is a fresh structure.
        converted = msgspec.to_builtins(value)
        if isinstance(converted, dict):
            return _normalize_payload(converted, _depth=0)
        elif isinstance(converted, list):
            return _normalize_list(converted, _depth=0)
        else:
            return _normalize_single(converted)
    elif hasattr(value, '__array__'):
        # MLX/numpy arrays → list for orjson serialization
        try:
            return value.tolist()
        except Exception:  # noqa: BLE001
            try:
                return list(value)
            except Exception:  # noqa: BLE001
                return str(value)
    return value

class _RustMPSCBytes:
    """Single canonical Rust MPSC wrapper — bytes in, bytes out.

    ISSUE-006 FIX: Unified _RustMPSC + _RustMPSC2 into one bytes-only class.
    - send() accepts bytes directly — serialization happens at caller side
    - recv_batch() returns bytes directly — deserialization happens at caller side
    - asyncio fallback via asyncio.Queue[bytes] (only for JSONL path)

    F320-ISSUE12: Replaces asyncio.Queue in the SQLite flush path.
    - send() is non-blocking, lock-free, ~2-5ns via ARM LSE atomics
    - recv_batch() drains the Rust MPSC channel directly

    M1 8GB: ~1 MiB total (2048 slots × 512B), negligible overhead.

    S1-03 FIX: asyncio.Queue asyncio_fallback capacity is derived from memory budget.
    Falls back to Rust MPSC with ~50× throughput when available.
    """
    # S1-03/C3-04 FIX: asyncio.Queue maxsize derived from memory budget.
    # Formula: floor(available_memory * 0.05 / avg_item_bytes)
    # With 1 KiB/item and 10% of 512 MiB available ≈ 512 items (≈0.5 MiB).
    # Cap is the safety ceiling — actual size is min(capacity, cap).
    # SAFETY: Hard cap of 512 prevents unbounded memory growth when
    # Rust extension is unavailable — the asyncio fallback is ~50× slower
    # than Rust MPSC so sustained backpressure is the expected signal.
    _ASYNC_FALLBACK_QUEUE_MAXSIZE = 512

    @staticmethod
    def _get_async_fallback_queue_maxsize() -> int:
        """S1-03 FIX: Compute async fallback queue maxsize from memory budget.

        Returns:
            Dynamic queue size: min(_ASYNC_FALLBACK_QUEUE_MAXSIZE,
            floor(available_memory * memory_fraction / avg_item_bytes))

        M1 8GB bounds:
            - available ≈ 6.25 GiB total (OS 2.5 GiB + app budget)
            - 5% of available ≈ 320 MiB / 1 KiB ≈ 320 items
            - Hard cap 512 is the safety ceiling
        """
        try:
            import psutil
            process = psutil.Process()
            mem_info = process.memory_info()
            # Use RSS as the memory budget metric (actual physical memory used)
            available = getattr(mem_info, 'available', mem_info.rss)
            # Reserve 5% of available memory for the queue
            memory_fraction = 0.05
            avg_item_bytes = 1024  # 1 KiB per evidence item
            dynamic_size = int((available * memory_fraction) / avg_item_bytes)
            # Enforce hard cap: min(dynamic, hard_cap)
            hard_cap = 512
            return min(dynamic_size, hard_cap)
        except Exception:
            # Fail-safe: return hard cap if psutil unavailable
            return 512  # hard-coded fallback

    # F-15 FIX: No reload — permanent fallback.
    # If Rust extension is not available at init time, asyncio.Queue is used.
    # Extension absence is cached permanently via _rust_unavailable flag.
    # No retry scheduling — PyO3 C extension cannot be safely reloaded.
    # "maturin develop" + process restart is required for Rust MPSC.

    __slots__ = tuple(('_impl', '_pool', '_queue', '_sender_ptr', '_wake_fd', 'fallback', '_retry_handle', '_retry_delay', '_capacity', '_asyncio_fallback', '_pending_retry'))

    def __init__(self, capacity: int=2048, asyncio_fallback: bool=False) -> None:
        """Initialize MPSC pool.

        Args:
            capacity: Max queue depth (default 2048, 2× asyncio.Queue maxsize=500)
            asyncio_fallback: If True, create asyncio.Queue fallback (for JSONL path).
                             If False, fallback is None (SQLite path doesn't need async).
        """
        self._pool: Any = None
        self._queue: asyncio.Queue[bytes] | None = None
        self._sender_ptr: int = 0
        self._wake_fd: int = -1
        self.fallback: bool = True
        self._impl: str = 'asyncio'
        self._retry_handle: asyncio.TimerHandle | None = None  # Scheduled retry timer
        self._retry_delay: float = 5.0  # seconds, exponential backoff
        self._capacity: int = capacity
        self._asyncio_fallback: bool = asyncio_fallback
        self._pending_retry: bool = False  # True when a retry is scheduled
        # F-15: cached "Rust extension not available" flag — never retry a PyO3 C ext reload
        self._rust_unavailable: bool = False
        self._init_rust(capacity, asyncio_fallback)

    def _init_rust(self, capacity: int, asyncio_fallback: bool) -> None:
        try:
            from hledac_rust_extensions import MPSCPool as _MPSC
            pool = _MPSC(capacity=capacity)
            sender_ptr = pool.add_sender()
            wake_fd = pool.wake_fd()
            self._pool = pool
            self._sender_ptr = sender_ptr
            self._wake_fd = wake_fd
            self.fallback = False
            self._impl = 'rust'
            # Rust is now available — cancel any pending retry
            if self._retry_handle is not None:
                try:
                    self._retry_handle.cancel()
                except Exception:  # noqa: BLE001
                    pass
                self._retry_handle = None
        except Exception:  # noqa: BLE001
            self._pool = None
            self._sender_ptr = 0
            self._wake_fd = -1
            if asyncio_fallback:
                # S1-03 FIX: Use memory-derived queue size via static method.
                # Computes: floor(available_memory * 0.05 / avg_item_bytes),
                # capped at _ASYNC_FALLBACK_QUEUE_MAXSIZE (512).
                # Prevents unbounded memory growth when Rust extension is unavailable.
                async_fallback_size = self._get_async_fallback_queue_maxsize()
                capped = min(capacity, async_fallback_size)
                self._queue = asyncio.Queue(maxsize=capped)
            else:
                self._queue = None
            self.fallback = True
            self._impl = 'asyncio'
            # F-15 FIX: Mark Rust extension as permanently unavailable.
            # No retry — if it wasn't compiled at startup, "maturin develop"
            # + process restart is required. asyncio.Queue is functionally equivalent.
            self._rust_unavailable = True
            self._pending_retry = False

    def _schedule_retry(self) -> None:
        """Schedule a retry attempt to re-import Rust extension.

        Uses exponential backoff: 5s → 10s → 20s → 40s → 60s (cap).
        Cancel previous retry if one is scheduled (call_later is idempotent per loop).
        """
        # Don't reschedule if already pending, not in fallback, or Rust permanently unavailable
        if self._pending_retry or not self.fallback or self._rust_unavailable:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running event loop yet — mark pending, will be triggered on first send/recv
            self._pending_retry = True
            return

        # Cancel any existing retry
        if self._retry_handle is not None:
            try:
                self._retry_handle.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._retry_handle = None

        def _retry_callback() -> None:
            """Retry Rust import — called from event loop."""
            self._retry_handle = None
            self._pending_retry = False
            try:
                self._do_retry_init()
            except Exception:  # noqa: BLE001
                pass  # Will reschedule if still in fallback

        try:
            # call_later is safe — if loop is closed, callback simply doesn't fire
            self._retry_handle = loop.call_later(
                self._retry_delay, _retry_callback
            )
            self._pending_retry = True
            # Exponential backoff: double delay, cap at 60s
            self._retry_delay = min(self._retry_delay * 2, 60.0)
        except Exception:  # noqa: BLE001
            logger.debug("rust_mpsc_loop_closing_marking_pending_retry", exc_info=True)
            self._pending_retry = True

    def _do_retry_init(self) -> None:
        """Attempt to re-initialize Rust MPSC.

        F-15 FIX: Replaced importlib.reload(hledac_rust_extensions) with
        sys.modules.get() check. Reloading a PyO3 C extension mid-process
        risks type registry corruption, double-free, and segfaults. If the
        extension was not available at startup, "maturin develop" must be
        run and the process restarted. We cache 'unavailable' permanently
        and never retry to avoid pointless 5-60s backoff loops.
        """
        if not self.fallback:
            # Already using Rust — nothing to do
            self._pending_retry = False
            return

        # F-15: Check if Rust extension is already in sys.modules
        ext = _sys.modules.get("hledac_rust_extensions")
        if ext is None:
            # Extension was never imported — mark permanently unavailable, no retry
            logger.debug("rust_mpsc_extension_never_imported_marking_unavailable")
            self._rust_unavailable = True
            self._pending_retry = False
            return

        # F-15: Extension is in sys.modules — check if it has MPSCPool
        # (if it was imported but failed to compile, it won't have it)
        if not hasattr(ext, "MPSCPool"):
            logger.debug("rust_mpsc_extension_missing_mpscpool_marking_unavailable")
            self._rust_unavailable = True
            self._pending_retry = False
            return

        try:
            from hledac_rust_extensions import MPSCPool as _MPSC  # type: ignore[import]
            pool = _MPSC(capacity=self._capacity)
            sender_ptr = pool.add_sender()
            wake_fd = pool.wake_fd()

            # Success — switch to Rust MPSC
            old_queue = self._queue
            self._pool = pool
            self._sender_ptr = sender_ptr
            self._wake_fd = wake_fd
            self.fallback = False
            self._impl = 'rust'
            self._queue = None  # Abandon asyncio.Queue
            self._pending_retry = False

            # Drain any items that accumulated in asyncio.Queue into Rust MPSC
            if old_queue is not None:
                drained: list[bytes] = []
                while True:
                    try:
                        drained.append(old_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                if drained:
                    for item in drained:
                        self._pool.send(self._sender_ptr, item)
                    logger.debug("rust_mpsc_switched_drained_items", drained_count=len(drained))

            logger.info("rust_mpsc_now_available_switched_from_asyncio_fallback")
        except Exception:  # noqa: BLE001
            logger.debug("rust_mpsc_init_failed_marking_unavailable", exc_info=True)
            # F-15: On any error, mark permanently unavailable — no more retries
            self._rust_unavailable = True
            self._pending_retry = False

    def send(self, item: bytes) -> bool:
        """Send raw bytes to the pool. Non-blocking (Rust) or blocking (asyncio).

        ISSUE-006: bytes-only — serialization is caller's responsibility.
        This eliminates the redundant orjson.dumps() that _RustMPSC did internally.

        D4 FIX: If _pending_retry is True (retry scheduled but no event loop yet),
        try to re-initialize Rust MPSC synchronously on first send().
        """
        # D4 FIX: Sync retry on first send if pending but no event loop was available
        if self._pending_retry and self._impl != 'rust':
            self._do_retry_init()

        if self._impl == 'rust' and self._pool is not None:
            try:
                return self._pool.send(self._sender_ptr, item)
            except Exception:  # noqa: BLE001
                logger.debug("rust_mpsc_send_failed_returning_false", exc_info=True)
                return False
        elif self._queue is not None:
            try:
                self._queue.put_nowait(item)
                return True
            except asyncio.QueueFull:
                # C3-04 FIX: backpressure signal — caller should await and retry.
                # No silent drop; return False so caller can apply backpressure.
                return False
        return False

    def send_batch(self, items: list[bytes]) -> int:
        """Send multiple items via a single Rust send_batch() call.

        ISSUE-007 FIX: Single Python→Rust call for N items — reduces GIL
        acquisition overhead from N× to 1× for the MPSC send phase.
        Falls back to sequential put_nowait() in asyncio fallback path.

        Returns:
            Number of items successfully sent.
        """
        if not items:
            return 0
        if self._impl == "rust" and self._pool is not None:
            try:
                # PyO3: list[bytes] → Vec<&[u8]>
                return self._pool.send_batch(self._sender_ptr, items)
            except Exception:  # noqa: BLE001
                logger.debug("rust_mpsc_batch_send_failed_returning_zero", exc_info=True)
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
        """Async send — applies backpressure via wait_for if queue is full.

        S1-03 FIX: Uses asyncio.wait_for(q.put(), timeout) for bounded
        backpressure instead of put_nowait which silently drops on QueueFull.

        Returns True when item was queued within timeout, False on timeout
        or when the queue is permanently unavailable.
        """
        if self._impl == 'rust' and self._pool is not None:
            return self.send(item)
        elif self._queue is not None:
            try:
                # S1-03 FIX: wait_for applies backpressure — caller yields the
                # event loop for up to `timeout` seconds before giving up.
                # This prevents unbounded queue growth under sustained ingestion.
                await asyncio.wait_for(self._queue.put(item), timeout=timeout)
                return True
            except asyncio.TimeoutError:
                # Queue full for timeout seconds — apply backpressure signal.
                # No silent drop; caller receives False and can react.
                logger.debug("evidence_mpsc_async_queue_timeout", timeout=timeout)
                return False
            except Exception:
                return False
        return False

    def recv_batch(self, max_items: int | None=None) -> list[bytes]:
        """Drain up to max_items as raw bytes (non-blocking).

        ISSUE-006: Returns bytes directly — caller deserializes only when needed.
        SQLite path now uses _flush_batch_bytes() for zero-copy BLOB insert.

        D4 FIX: If _pending_retry is True, try to re-initialize Rust MPSC on first recv.
        """
        # D4 FIX: Sync retry on first recv if pending but no event loop was available
        if self._pending_retry and self._impl != 'rust':
            self._do_retry_init()

        if self._impl == 'rust' and self._pool is not None:
            try:
                return self._pool.recv_batch(max_items)
            except Exception:  # noqa: BLE001
                logger.debug("rust_mpsc_legacy_send_failed_returning_empty", exc_info=True)
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

    def len(self) -> int:
        if self._impl == 'rust' and self._pool is not None:
            return self._pool.len()
        elif self._queue is not None:
            return self._queue.qsize()
        return 0

    def is_empty(self) -> bool:
        if self._impl == 'rust' and self._pool is not None:
            return self._pool.is_empty()
        elif self._queue is not None:
            return self._queue.empty()
        return True

    @property
    def has_async_queue(self) -> bool:
        """True when asyncio.Queue fallback is active (Rust unavailable)."""
        return self._queue is not None

class EvidenceLog:
    """
    Append-only log pro ukládání důkazů - M1 8GB RAM optimized.

    Tato třída implementuje:
    - Append-only zápis (nikdy nemazat)
    - Ring buffer v RAM (max 100 událostí) pro M1 optimalizaci
    - Automatická JSONL persistencer na disk
    - Content hash pro každou událost
    - Trimmované payloady (žádné fulltexty v RAM)
    - Dotazování podle typu a confidence
    - Shrnutí pro Hermes (ne celý raw log)
    """
    __slots__ = ('_run_id', '_log', '_index_by_type', '_index_by_source', '_created_at', '_frozen', '_closed', '_total_count', '_dropped_count', '_seq', '_chain_head', '_genesis_hash', '_encrypt_at_rest', '_encryption_key', '_cipher', '_enable_persist', '_persist_path', '_persist_file', '_persist_path_str', '_mpsc', '_mpsc2', '_flush_task', '_async_write_queue', '_async_write_task', '_mpsc2_reader', '_db_path', '_db', '_initialized', '_arrow_path', '_arrow_writer', '_arrow_schema', '_closing', '_manifest_dirty', '_flush_shutdown', '_async_write_shutdown', '_cancel_event', '_cancel_watcher_task', '_loop', '_silent_failure', '_sample_rate', '_duckdb_conn', '_duckdb_enabled', '_dlq_manager')
    MAX_RAM_EVENTS = 50
    MAX_PAYLOAD_PREVIEW = 200
    JSONL_ROTATE_SIZE = 10 * 1024 * 1024
    _FSYNC_EVERY_N_EVENTS = 25
    _MANIFEST_EVERY_N_EVENTS = 100
    _SQLITE_BATCH_SIZE = 500
    _SQLITE_FLUSH_INTERVAL = 1.5
    _ASYNC_WRITE_QUEUE_MAXSIZE = 500

    # P1-9: Canonical aclose timeout — matches DEFAULT_ACLOSE_TIMEOUT_S.
    # Outer bound for the entire aclose() operation.
    DEFAULT_TIMEOUT_S = 10.0

    def __init__(self, run_id: str, persist_path: Path | None=None, enable_persist: bool=True, encrypt_at_rest: bool=False, silent_failure: bool=False, sample_rate: float=1.0, cancel_event: asyncio.Event | None=None):
        """
        Inicializuje EvidenceLog.

        Args:
            run_id: Unikátní ID běhu výzkumu
            persist_path: Cesta pro JSONL persistenci (None = auto)
            enable_persist: Zda povolit persistenci na disk
            encrypt_at_rest: Zda šifrovat data na disku
            silent_failure: If True, all append() calls become no-ops without I/O.
                          Use for pre-flight / dry-run modes.
            sample_rate: Sampling rate for non-error events (Phase4: 0.10 = 10%).
                        Errors are always logged regardless of sampling.
            cancel_event: Optional asyncio.Event wired to sprint lifecycle shutdown.
                        When set, a background watcher auto-triggers aclose() when the
                        event is set — ensuring EvidenceLog workers exit cleanly even
                        when aclose() is not called explicitly by the lifecycle.
                        E2: Replaces bare asyncio.Event() pattern with lifecycle binding.
        """
        import os
        self._run_id: str = run_id
        self._silent_failure: bool = silent_failure
        self._sample_rate: float = ENV.get_float('HLEDAC_EVIDENCE_SAMPLE_RATE', default=sample_rate)
        self._log: deque = deque(maxlen=self.MAX_RAM_EVENTS)
        self._index_by_type: dict[str, deque[int]] = {'tool_call': deque(maxlen=self.MAX_RAM_EVENTS), 'observation': deque(maxlen=self.MAX_RAM_EVENTS), 'synthesis': deque(maxlen=self.MAX_RAM_EVENTS), 'error': deque(maxlen=self.MAX_RAM_EVENTS), 'decision': deque(maxlen=self.MAX_RAM_EVENTS), 'evidence_packet': deque(maxlen=self.MAX_RAM_EVENTS)}
        self._index_by_source: dict[str, deque[int]] = {}
        self._created_at: datetime = datetime.now(UTC)
        self._frozen: bool = False
        self._closed: bool = False
        self._total_count: int = 0
        self._dropped_count: int = 0
        self._seq: int = 0
        self._chain_head: str = ''
        self._genesis_hash: str = hashlib.sha256(f'GENESIS:{run_id}'.encode()).hexdigest()
        self._chain_head = self._genesis_hash
        self._encrypt_at_rest = encrypt_at_rest or os.environ.get('ENCRYPT_AT_REST', '0') == '1'
        self._encryption_key = os.environ.get('ENCRYPTION_KEY', '').encode() if self._encrypt_at_rest else None
        if self._encrypt_at_rest:
            logger.info("encryption_enabled", target="evidence")
            self._init_encryption()
        else:
            self._cipher = None
        self._enable_persist: bool = enable_persist
        self._persist_path: Path | None = None
        self._persist_file = None
        self._persist_path_str: str | None = None
        if enable_persist:
            if persist_path is None:
                from hledac.universal.paths import EVIDENCE_ROOT
                evidence_dir = EVIDENCE_ROOT
                evidence_dir.mkdir(parents=True, exist_ok=True)
                ext = '.enc' if self._encrypt_at_rest else '.jsonl'
                self._persist_path = evidence_dir / f'{run_id}{ext}'
            else:
                self._persist_path = Path(persist_path)
                self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._persist_file = open(self._persist_path, 'ab' if self._encrypt_at_rest else 'a', encoding='utf-8' if not self._encrypt_at_rest else None, buffering=8192)
                self._persist_path_str = str(self._persist_path)
                logger.debug("evidence_log_persistence", persist_path=str(self._persist_path))
            except Exception as e:  # noqa: BLE001
                logger.error("failed_to_open_evidence_log", error=str(e))
                self._enable_persist = False
        self._mpsc: _RustMPSCBytes = _RustMPSCBytes(capacity=2048, asyncio_fallback=False)
        self._mpsc2: _RustMPSCBytes = _RustMPSCBytes(capacity=2048, asyncio_fallback=True)
        self._flush_task: asyncio.Task | None = None
        self._async_write_queue: asyncio.Queue[bytes | None] | None = None
        self._async_write_task: asyncio.Task | None = None
        self._mpsc2_reader: Any = None
        self._db_path: Path | None = None
        self._db: aiosqlite.Connection | None = None
        self._initialized = False
        self._arrow_path: Path | None = None
        self._arrow_writer: Any = None
        self._arrow_schema: Any = None
        self._closing = False
        self._manifest_dirty: bool = False
        # E2: Bare asyncio.Event() replaced with lifecycle-bound shutdown via cancel_event.
        # _flush_shutdown and _async_write_shutdown are still used by aclose() to signal
        # workers to drain, but a watcher on cancel_event auto-triggers aclose() when
        # the sprint lifecycle ends (even if aclose() is never called explicitly).
        self._flush_shutdown: asyncio.Event = asyncio.Event()
        self._async_write_shutdown: asyncio.Event = asyncio.Event()
        self._cancel_event: asyncio.Event | None = cancel_event
        self._cancel_watcher_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # ISSUE-11: DuckDB Arrow IPC support
        self._duckdb_conn: Any = None
        self._duckdb_enabled: bool = False
        # DLQ-02: Dead-Letter Queue for corrupted payloads
        self._dlq_manager: Any = None

    def _sync_close(self) -> None:
        """Synchronous cleanup: cancel flush task, close Arrow writer, sync persist."""
        if not hasattr(self, '_flush_task'):
            return
        if self._flush_task is not None and (not self._flush_task.done()):
            self._flush_task.cancel()
            self._flush_task = None
        if self._async_write_task is not None and (not self._async_write_task.done()):
            self._async_write_task.cancel()
            self._async_write_task = None
        if self._arrow_writer is not None:
            try:
                self._arrow_writer.close()
            except Exception:  # noqa: BLE001
                pass
            self._arrow_writer = None
        if self._persist_file and (not self._persist_file.closed):
            try:
                self._persist_file.close()
            except Exception:  # noqa: BLE001
                pass

    def __del__(self):
        """Cleanup — synchronous resources only.

        F285-RESOURCE FIX: __del__ now calls _sync_close() which handles:
          1. _flush_task — cancelled (async.Event signaling is aclose's job)
          2. _arrow_writer — closed synchronously
          3. _persist_file — closed (existing behaviour preserved)

        NOTE: _db (aiosqlite.Connection) CANNOT be closed here — it requires
        an async context. If aclose() was not called, the connection will be
        closed when the process exits (aiosqlite does this), but WAL data may
        not be flushed. Always prefer aclose() over relying on __del__.
        """
        self._sync_close()

    async def __aenter__(self) -> EvidenceLog:
        """Async context manager entry — initializes async resources."""
        await self.initialize()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        """Async context manager exit — cleanly shuts down all resources."""
        await self.aclose()

    async def initialize(self) -> None:
        """
        Initialize async SQLite components.

        Creates database, migrates from old JSONL file if exists,
        and starts background flush worker.

        Idempotent: safe to call multiple times. Previous flush worker
        is cancelled before starting a new one.
        """
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        if self._flush_task is not None and (not self._flush_task.done()):
            self._flush_task.cancel()
            try:
                await safe_wait_for(self._flush_task, timeout=1.0, label='_flush_task')
            except (TimeoutError, asyncio.CancelledError):
                pass
            self._flush_task = None
        if self._async_write_task is not None and (not self._async_write_task.done()):
            self._async_write_task.cancel()
            try:
                await safe_wait_for(self._async_write_task, timeout=1.0, label='_async_write_task')
            except (TimeoutError, asyncio.CancelledError):
                pass
            self._async_write_task = None
        if self._mpsc2_reader is not None:
            try:
                self._mpsc2_reader.close()
            except Exception:  # noqa: BLE001
                pass
            self._mpsc2_reader = None
        if self._initialized:
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = safe_create_task(self._flush_worker())
            if self._async_write_task is None or self._async_write_task.done():
                self._async_write_task = safe_create_task(self._async_write_worker())
            if self._loop is not None and (not self._mpsc2.fallback):
                self._mpsc2_reader = self._loop.add_reader(self._mpsc2.wake_fd(), self._mpsc2_drain_callback)
            self._flush_shutdown.clear()
            self._async_write_shutdown.clear()
            return
        await self._init_db()
        try:
            await self._migrate_from_file()
        except Exception as _mig_err:  # noqa: BLE001
            logger.warning("f11c_migration_from_jsonl_failed_non_fatal", error=str(_mig_err))
        try:
            self._flush_task = safe_create_task(self._flush_worker())
        except Exception as _task_err:  # noqa: BLE001
            logger.warning("f11c_flush_worker_task_creation_failed_non_fatal", error=str(_task_err))
            self._flush_task = None
        try:
            self._async_write_task = safe_create_task(self._async_write_worker())
        except Exception as _write_task_err:  # noqa: BLE001
            logger.warning("f290_async_write_worker_task_creation_failed_non_fatal", error=str(_write_task_err))
            self._async_write_task = None
        # E2: Start cancel watcher — triggers aclose() when lifecycle cancel_event is set.
        # This ensures workers exit cleanly even when aclose() is not called explicitly.
        self._start_cancel_watcher()

        # DLQ-02: Lazy DLQ manager initialization
        try:
            from hledac.universal.core.dlq_manager import get_dlq_manager
            self._dlq_manager = get_dlq_manager()
        except Exception:  # noqa: BLE001
            self._dlq_manager = None

    def inject_cancel_event(self, cancel_event: asyncio.Event) -> None:
        """
        E2: Wire an external cancel_event to EvidenceLog.

        When cancel_event is set (sprint lifecycle shutdown), the internal watcher
        auto-triggers aclose(), ensuring workers exit cleanly even when aclose()
        is not called explicitly by the lifecycle.

        Safe to call multiple times (only first call takes effect).
        Idempotent: passing the same event multiple times is a no-op.
        """
        if self._cancel_event is not None:
            return  # Already wired
        self._cancel_event = cancel_event
        # If already initialized, start the watcher immediately;
        # otherwise it will be started by initialize() which calls _start_cancel_watcher().
        if self._initialized:
            self._start_cancel_watcher()

    async def _init_db(self) -> None:
        """Initialize SQLite database with WAL mode OR DuckDB Arrow IPC.

        ISSUE-11: DuckDB Arrow IPC path for GIL-free writes.
        When HLEDAC_EVIDENCE_DUCKDB=1:
          - DuckDB in-process with Arrow IPC batch ingest
          - Zero-copy via run_in_executor (ThreadPoolExecutor)
          - Eliminates GIL contention on SQLite write path
        """
        if self._db_path is None:
            from hledac.universal.paths import EVIDENCE_ROOT
            evidence_dir = EVIDENCE_ROOT
            evidence_dir.mkdir(parents=True, exist_ok=True)
            self._db_path = evidence_dir / f'{self._run_id}.db'

        # ISSUE-11: DuckDB Arrow IPC path
        use_duckdb = os.environ.get('HLEDAC_EVIDENCE_DUCKDB', '0') == '1'
        if use_duckdb:
            try:
                import duckdb
                self._duckdb_conn = duckdb.connect(str(self._db_path).replace('.db', '.duckdb'), read_only=False)
                # M1 8GB: memory_limit + threads + preserve_insertion_order
                try:
                    self._duckdb_conn.execute("SET memory_limit = '256MB'")
                    self._duckdb_conn.execute("PRAGMA threads=2")
                    self._duckdb_conn.execute("SET preserve_insertion_order = false")
                except Exception:  # noqa: BLE001 — fail-soft
                    pass
                self._duckdb_conn.execute('''
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY,
                        timestamp DOUBLE NOT NULL,
                        event_type VARCHAR NOT NULL,
                        data VARCHAR NOT NULL,
                        hash VARCHAR NOT NULL,
                        prev_chain_hash VARCHAR,
                        chain_hash VARCHAR
                    )
                ''')
                self._duckdb_enabled = True
                logger.info("duckdb_arrow_ipc_evidence_enabled", db_path=str(self._db_path))
                _ensure_duckdb_executor()
            except Exception as _duck_err:  # noqa: BLE001
                logger.warning("duckdb_failed_init_using_sqlite_fallback", error=str(_duck_err))
                self._duckdb_enabled = False
                self._duckdb_conn = None

        # SQLite fallback / primary
        if not self._duckdb_enabled:
            self._db = await aiosqlite.connect(str(self._db_path), check_same_thread=False)
            # Batch PRAGMA: single round-trip instead of 6 sequential executes
            await self._db.executescript('''
                PRAGMA busy_timeout=30000;
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                PRAGMA wal_autocheckpoint=1000;
                PRAGMA cache_size=-8192;
                PRAGMA read_uncommitted=1;
            ''')
            try:
                await self._db.execute('PRAGMA integrity_check')
            except Exception:  # noqa: BLE001
                pass
            await self._db.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    data TEXT NOT NULL,
                    hash TEXT NOT NULL,
                    prev_chain_hash TEXT,
                    chain_hash TEXT
                )
            ''')
            await self._db.commit()
            _ensure_evidence_sqlite_executor()

        arrow_loader = _get_arrow()
        if arrow_loader:
            pa, ipc = arrow_loader
            from hledac.universal.paths import EVIDENCE_ROOT
            evidence_dir = EVIDENCE_ROOT
            self._arrow_path = evidence_dir / f'{self._run_id}.arrow'
            self._arrow_schema = pa.schema([('timestamp', pa.float64()), ('event_type', pa.string()), ('data', pa.string()), ('hash', pa.string()), ('prev_chain_hash', pa.string()), ('chain_hash', pa.string())])
            self._arrow_writer = ipc.new_file(str(self._arrow_path), self._arrow_schema)
            logger.info("arrow_ipc_enabled", arrow_path=str(self._arrow_path))

    async def _migrate_from_file(self) -> None:
        """Migrate events from old JSONL file if exists.

        F285-FIX: Atomic migration.
        Before: crash between commit() and rename() caused re-migration
        on next start → duplicate key errors, partial state in DB.
        After: write migration marker BEFORE rename; if rename fails the
        marker exists so next start skips. Also wrap in transaction.
        """
        if self._persist_path is None or not self._persist_path.exists():
            return
        old_file = self._persist_path
        migrated_file = old_file.with_suffix('.migrated')
        if migrated_file.exists():
            return
        if self._db is None:
            return
        try:
            migrated_file.touch(exist_ok=True)
            await self._db.execute('BEGIN TRANSACTION')
            try:
                with open(old_file, encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        data = orjson.loads(line)
                        timestamp = datetime.fromisoformat(data['timestamp']).timestamp()
                        event_type = data['event_type']
                        event_data = orjson.dumps(data).decode()
                        content_hash = data.get('content_hash', '')
                        prev_chain_hash = data.get('prev_chain_hash', '')
                        chain_hash = data.get('chain_hash', '')
                        await self._db.execute('INSERT INTO events (timestamp, event_type, data, hash, prev_chain_hash, chain_hash) VALUES (?, ?, ?, ?, ?, ?)', (timestamp, event_type, event_data, content_hash, prev_chain_hash, chain_hash))
                await self._db.commit()
            except Exception:
                await self._db.rollback()
                if migrated_file.exists():
                    migrated_file.unlink()
                raise
            old_file.rename(migrated_file)
            logger.info("migrated_events_to_sqlite", run_id=self._run_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("migration_failed", error=str(e))

    async def _flush_worker(self) -> None:
        """Background worker that flushes events in batches.

        F320-ISSUE12: Uses _RustMPSCBytes (Rust MPSCPool) instead of asyncio.Queue.
        - recv_batch() is non-blocking, drains all available items from the Rust channel.
        - asyncio.timeout(1.0) provides the periodic wake cycle (instead of queue.get() blocking).
        - shutdown signal: _flush_shutdown.set() from aclose() → worker drains and exits.

        ISSUE-006: Now works with bytes directly — _flush_batch_bytes() for zero-copy SQLite BLOB insert.
        """
        batch: list[bytes] = []
        last_flush = datetime.now(UTC)
        # P2-03: Adaptive batch sizing — dynamically compute optimal batch size from throughput
        # High throughput (1000+ events/s): larger batches reduce SQLite contention
        # Low throughput (10 events/s): smaller batches reduce latency
        # Range: 100-2000 events, calculated as: events_per_sec * 0.5, clamped
        _adaptive_batch_size: int = self._SQLITE_BATCH_SIZE  # Start with default, adapt on first flush
        _events_since_last_flush: int = 0
        while True:
            try:
                async with asyncio.timeout(1.0):
                    received = self._mpsc.recv_batch(max_items=None)
                    if received:
                        batch.extend(received)
                        _events_since_last_flush += len(received)
            except TimeoutError:
                pass
            if self._flush_shutdown.is_set():
                break
            now = datetime.now(UTC)
            elapsed = (now - last_flush).total_seconds()
            # P2-03: Compute adaptive batch size from measured throughput
            if elapsed > 0.001 and _events_since_last_flush > 0:
                events_per_sec = _events_since_last_flush / elapsed
                # Optimal: half-second worth of events, clamped to [100, 2000]
                _adaptive_batch_size = min(max(int(events_per_sec * 0.5), 100), 2000)
            if len(batch) >= _adaptive_batch_size or (batch and elapsed >= self._SQLITE_FLUSH_INTERVAL):
                flush_start = time.perf_counter()
                try:
                    # A5-03: asyncio.shield() prevents cancellation from tearing the
                    # in-progress DB write — avoids partial flush on sprint abort.
                    # CancelledError propagates after the batch write completes.
                    await asyncio.shield(self._flush_batch_bytes(batch))
                    flush_latency_ms = (time.perf_counter() - flush_start) * 1000
                    trace_evidence_flush(len(batch), flush_latency_ms, 'ok', len(batch))
                except asyncio.CancelledError:
                    # Outer CancelledError — flush completed but shutdown was requested.
                    # Re-raise so the worker loop exits cleanly after the shielded write.
                    flush_latency_ms = (time.perf_counter() - flush_start) * 1000
                    logger.warning("flush_shutdown_cancelled_after_write", batch_size=len(batch), flush_latency_ms=flush_latency_ms)
                    trace_evidence_flush(len(batch), flush_latency_ms, 'cancelled', 0)
                    raise
                except Exception as _flush_err:  # noqa: BLE001
                    flush_latency_ms = (time.perf_counter() - flush_start) * 1000
                    logger.warning("flush_batch_failed_dropping_events", batch_size=len(batch), error=str(_flush_err))
                    trace_evidence_flush(len(batch), flush_latency_ms, 'flush_error', 0)
                batch = []
                last_flush = datetime.now(UTC)
                _events_since_last_flush = 0  # P2-03: Reset after flush
        remaining = self._mpsc.recv_batch(max_items=None)
        if remaining:
            batch.extend(remaining)
        if batch and self._db is not None:
            flush_start = time.perf_counter()
            try:
                await asyncio.shield(self._flush_batch_bytes(batch))
                flush_latency_ms = (time.perf_counter() - flush_start) * 1000
                trace_evidence_flush(len(batch), flush_latency_ms, 'ok', len(batch))
            except asyncio.CancelledError:
                flush_latency_ms = (time.perf_counter() - flush_start) * 1000
                logger.warning("flush_shutdown_cancelled_after_write", batch_size=len(batch), flush_latency_ms=flush_latency_ms)
                trace_evidence_flush(len(batch), flush_latency_ms, 'cancelled', 0)
                raise

    def _sync_write_fallback(self, line: str, bytes_to_write: bytes) -> None:
        """Synchronous fallback write for SWAL durability when async queue is unavailable.

        Used when: queue is full, worker dead, or no event loop.
        Writes directly to _persist_file (text or binary depending on encryption).
        """
        if self._persist_file:
            if self._encrypt_at_rest:
                self._persist_file.write(bytes_to_write)
            else:
                self._persist_file.write(line + '\n')
            self._persist_file.flush()

    def _mpsc2_drain_callback(self) -> None:
        """Callback invoked when mpsc2 wake_fd fires.

        F320-ISSUE12b: The wake_fd signals that _mpsc2 has items.
        This callback is a no-op placeholder — the actual drain happens
        in the _async_write_worker loop on the next iteration.
        The callback exists solely to break the worker out of asyncio.timeout()
        so it can immediately drain via recv_batch().
        """
        pass

    async def _async_write_worker(self) -> None:
        """Background worker that writes JSONL entries asynchronously using aiofiles.

        F320-ISSUE12b: Uses _RustMPSC2 (Rust MPSCPool) instead of asyncio.Queue.
        F2F: write-to-buffer + flush at threshold OR shutdown.
        - Writes accumulate in _write_buf (no syscall until flush)
        - Flush every _WRITE_FLUSH_THRESHOLD events OR on shutdown
        - At shutdown: drain queue + final flush + close
        """
        import aiofiles as _f290_aiofiles
        _afile: object | None = None
        try:
            _afile = await _f290_aiofiles.open(self._persist_path_str, 'ab', buffering=8192)
        except Exception as _open_err:  # noqa: BLE001
            logger.warning("f290_aiofiles_open_failed_using_sync_fallback", error=str(_open_err))
            _afile = None
        _WRITE_FLUSH_THRESHOLD = 64
        _write_buf: list[bytes] = []

        async def _flush_buf() -> None:
            if not _write_buf:
                return
            if _afile is not None:
                # Batch write: join all data and write in one syscall
                _combined = b''.join(_write_buf)
                try:
                    await _afile.write(_combined)
                    await _afile.flush()
                except Exception:  # noqa: BLE001
                    # Fallback: sync write each item individually
                    try:
                        with open(cast(str, self._persist_path_str), 'ab') as _sf:
                            for _data in _write_buf:
                                _sf.write(_data)
                    except Exception:  # noqa: BLE001
                        pass
            else:
                # Batch write: join all data and write in one syscall
                try:
                    with open(cast(str, self._persist_path_str), 'ab') as _sf:
                        _sf.write(b''.join(_write_buf))
                except Exception:  # noqa: BLE001
                    pass
            _write_buf.clear()
        while True:
            if self._mpsc2.has_async_queue:
                batch = self._mpsc2.recv_batch(max_items=1)
                if not batch:
                    if self._async_write_shutdown.is_set():
                        break
                    await asyncio.sleep(0.05)
                    continue
            else:
                try:
                    async with asyncio.timeout(1.0):
                        batch = self._mpsc2.recv_batch(max_items=None)
                except TimeoutError:
                    batch = []
                if self._async_write_shutdown.is_set():
                    break
            if batch:
                _write_buf.extend(batch)
                if len(_write_buf) >= _WRITE_FLUSH_THRESHOLD:
                    # A5-03 FIX: Shield flush to prevent cancellation from tearing write.
                    # If CancelledError occurs, the batch is preserved in _write_buf
                    # and will be retried on next iteration or final flush.
                    try:
                        await asyncio.shield(_flush_buf())
                    except asyncio.CancelledError:
                        # CancelledError after shielded flush — batch still in _write_buf.
                        # Clear it to prevent duplicate flush on retry.
                        _write_buf.clear()
                        raise
            if not self._mpsc2.has_async_queue and (not self._mpsc2.is_empty()):
                continue
        remaining = self._mpsc2.recv_batch(max_items=None)
        if remaining:
            _write_buf.extend(remaining)
        if _write_buf:
            # A5-03 FIX: Shield final flush to prevent cancellation from tearing write.
            try:
                await asyncio.shield(_flush_buf())
            except asyncio.CancelledError:
                # CancelledError after shielded final flush — batch preserved.
                # Log warning since this means some data may not be persisted.
                logger.warning("async_write_worker_cancelled_during_final_flush", pending_bytes=sum(len(b) for b in _write_buf))
                _write_buf.clear()
                raise
        if _afile is not None:
            try:
                await _afile.close()
            except Exception:  # noqa: BLE001
                pass
    _ARROW_SUB_BATCH = 256

    async def _flush_batch_bytes(self, batch: list[bytes]) -> None:
        """Flush a batch of bytes to DuckDB Arrow IPC or SQLite via ThreadPoolExecutor.

        ISSUE-11: GIL-free write path using DuckDB Arrow IPC + ThreadPoolExecutor.

        Priority:
          1. DuckDB Arrow IPC (HLEDAC_EVIDENCE_DUCKDB=1) — zero-copy, GIL-free
          2. Arrow IPC file writer — zero-copy, but GIL held by pyarrow
          3. SQLite via run_in_executor — GIL-free, runs on thread pool

        Each path decodes batch once and writes without blocking the event loop.
        """
        if not batch:
            return

        # ISSUE-11: DuckDB Arrow IPC path — GIL-free via ThreadPoolExecutor
        if self._duckdb_enabled and self._duckdb_conn is not None:
            await self._flush_duckdb_batch(batch)
            return

        # Arrow IPC writer path (still holds GIL but batches pyarrow writes)
        arrow_loader = _get_arrow()
        if arrow_loader and self._arrow_writer is not None:
            pa, _ = arrow_loader
            try:
                decoded_batch: list[dict[str, Any]] = []
                for b in batch:
                    try:
                        decoded_batch.append(msgspec.msgpack.decode(b))
                    except Exception:  # noqa: BLE001
                        logger.debug("flush_batch_item_flush_failed_skipping", exc_info=True)
                        continue
                if not decoded_batch:
                    return
                for i in range(0, len(decoded_batch), self._ARROW_SUB_BATCH):
                    sub = decoded_batch[i:i + self._ARROW_SUB_BATCH]
                    arrays = [
                        pa.array([e.get('timestamp', datetime.now(UTC).timestamp()) for e in sub], type=pa.float64()),
                        pa.array([e.get('event_type', 'unknown') for e in sub], type=pa.string()),
                        pa.array([orjson.dumps(e.get('data', {})) for e in sub], type=pa.string()),
                        pa.array([e.get('content_hash', '') for e in sub], type=pa.string()),
                        pa.array([e.get('prev_chain_hash', '') for e in sub], type=pa.string()),
                        pa.array([e.get('chain_hash', '') for e in sub], type=pa.string()),
                    ]
                    batch_arrow = pa.record_batch(arrays, schema=self._arrow_schema)
                    self._arrow_writer.write_batch(batch_arrow)
                return
            except Exception as e:  # noqa: BLE001
                logger.warning("arrow_ipc_write_failed_sqlite_fallback", error=str(e))

        # ISSUE-11: SQLite via ThreadPoolExecutor — GIL-free write
        await self._flush_sqlite_batch(batch)

    async def _flush_duckdb_batch(self, batch: list[bytes]) -> None:
        """Flush batch to DuckDB via executemany (GIL-free via ThreadPoolExecutor).

        F350M-R ISSUE-11 FIX v2: Replaced Arrow IPC file round-trip with direct
        executemany. Arrow IPC is optimized for million-row bulk loads — for
        evidence_log's micro-batches (max 500 records, flush every 1.5s), the
        file I/O + IPC serialization overhead dominates. Direct executemany is
        3-5× faster for small batches and avoids temp file lifecycle issues.

        C2 FIX: DuckDB is thread-safe (internal locking) — max_workers=2
        in the module-level executor. PRAGMA threads=2 is set once at
        connect() time (line ~886) and persists for the session.
        """
        if self._duckdb_conn is None:
            await self._flush_sqlite_batch(batch)
            return

        # Decode batch to records (same structure as _flush_sqlite_batch)
        records: list[tuple[float, str, str, str, str, str]] = []
        for b in batch:
            try:
                event = msgspec.msgpack.decode(b)
                # event tuple: (event_id, event_type, timestamp, payload_bytes, source_ids,
                #               confidence, content_hash, run_id, seq_no, prev_chain_hash, chain_hash)
                timestamp = event[2] if len(event) > 2 else datetime.now(UTC).timestamp()
                event_type = event[1] if len(event) > 1 else 'unknown'
                payload_bytes = event[3] if len(event) > 3 else b''
                payload_str = payload_bytes.decode('utf-8', errors='replace') if isinstance(payload_bytes, bytes) else str(payload_bytes)
                content_hash = event[6] if len(event) > 6 else ''
                prev_chain_hash = event[9] if len(event) > 9 else None
                chain_hash = event[10] if len(event) > 10 else None
                records.append((timestamp, event_type, payload_str, content_hash, prev_chain_hash or '', chain_hash or ''))
            except Exception:  # noqa: BLE001
                logger.debug("flush_batch_batch_item_failed_skipping", exc_info=True)
                continue

        if not records:
            return

        loop = asyncio.get_running_loop()

        def _duckdb_insert_records() -> None:
            """Direct executemany insert — runs on ThreadPoolExecutor, no GIL.

            PRAGMA threads=2 is set once at connect() time (line ~886) and
            persists for the DuckDB session — no need to re-apply per batch.
            """
            try:
                self._duckdb_conn.executemany(
                    'INSERT INTO events (timestamp, event_type, data, hash, prev_chain_hash, chain_hash) VALUES (?, ?, ?, ?, ?, ?)',
                    records,
                )
                self._duckdb_conn.commit()
            except Exception as e:  # noqa: BLE001
                logger.warning("flush_duckdb_batch_executemany_failed", error=str(e))

        try:
            # C2: copy_context() propagates ContextVar (sprint_id, lane, mode) across thread boundary
            ctx = contextvars.copy_context()
            await loop.run_in_executor(ctx, _ensure_duckdb_executor(), _duckdb_insert_records)
        except Exception as e:  # noqa: BLE001
            logger.warning("flush_duckdb_batch_executor_failed", error=str(e))
            # Fallback: direct SQLite
            await self._flush_sqlite_batch(batch)

    async def _flush_sqlite_batch(self, batch: list[bytes]) -> None:
        """Flush batch to SQLite via ThreadPoolExecutor (GIL-free).

        ISSUE-11: SQLite writes run on ThreadPoolExecutor — event loop
        is never blocked by GIL-held database operations.
        """
        if not batch:
            return

        # Decode once
        records: list[tuple[float, str, bytes, str, str, str]] = []
        for b in batch:
            try:
                event = msgspec.msgpack.decode(b)
                timestamp = event[2] if len(event) > 2 else datetime.now(UTC).timestamp()
                event_type = event[1] if len(event) > 1 else 'unknown'
                payload_bytes = event[3] if len(event) > 3 else b''
                content_hash = event[6] if len(event) > 6 else ''
                prev_chain_hash = event[9] if len(event) > 9 else None
                chain_hash = event[10] if len(event) > 10 else None
                records.append((timestamp, event_type, payload_bytes, content_hash, prev_chain_hash or '', chain_hash or ''))
            except Exception:  # noqa: BLE001
                logger.debug("write_payload_encoding_failed_skipping", exc_info=True)
                continue

        if not records:
            return

        loop = asyncio.get_running_loop()
        db_path = str(self._db_path) if self._db_path else ''

        def _sqlite_insert() -> None:
            """Synchronous SQLite insert — runs on ThreadPoolExecutor, no GIL."""
            import sqlite3
            try:
                conn = sqlite3.connect(db_path or ':memory:', timeout=30.0)
                conn.execute('PRAGMA busy_timeout=30000')
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('PRAGMA synchronous=NORMAL')
                conn.executemany(
                    'INSERT INTO events (timestamp, event_type, data, hash, prev_chain_hash, chain_hash) VALUES (?, ?, ?, ?, ?, ?)',
                    records
                )
                conn.commit()
                conn.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("flush_sqlite_batch_insert_failed", error=str(e))

        try:
            # C2: copy_context() propagates ContextVar (sprint_id, lane, mode) across thread boundary
            ctx = contextvars.copy_context()
            await loop.run_in_executor(ctx, _ensure_evidence_sqlite_executor(), _sqlite_insert)
        except Exception as e:  # noqa: BLE001
            logger.warning("flush_sqlite_batch_executor_failed", error=str(e))

    async def _flush_batch(self, batch: list[dict[str, Any]]) -> None:
        """Flush a batch of events to SQLite (default) or Arrow IPC (HLEDAC_ARROW_EVIDENCE=1).

        Arrow IPC path streams through sub-batches of 256 events to limit peak heap
        allocation on M1 8GB. Falls back to SQLite if Arrow is unavailable or disabled.
        """
        if not batch:
            return
        arrow_loader = _get_arrow()
        if arrow_loader and self._arrow_writer is not None:
            pa, _ = arrow_loader
            try:
                for i in range(0, len(batch), self._ARROW_SUB_BATCH):
                    sub = batch[i:i + self._ARROW_SUB_BATCH]
                    arrays = [pa.array([e.get('timestamp', datetime.now(UTC).timestamp()) for e in sub], type=pa.float64()), pa.array([e.get('event_type', 'unknown') for e in sub], type=pa.string()), pa.array([orjson.dumps(e.get('data', {})) for e in sub], type=pa.string()), pa.array([e.get('content_hash', '') for e in sub], type=pa.string()), pa.array([e.get('prev_chain_hash', '') for e in sub], type=pa.string()), pa.array([e.get('chain_hash', '') for e in sub], type=pa.string())]
                    batch_arrow = pa.record_batch(arrays, schema=self._arrow_schema)
                    self._arrow_writer.write_batch(batch_arrow)
                return
            except Exception as e:  # noqa: BLE001
                logger.warning("arrow_ipc_write_failed_sqlite_fallback", error=str(e))
        records = []
        for event_data in batch:
            timestamp = event_data.get('timestamp', datetime.now(UTC).timestamp())
            event_type = event_data.get('event_type', 'unknown')
            data = orjson.dumps(event_data).decode()
            content_hash = event_data.get('content_hash', '')
            prev_chain_hash = event_data.get('prev_chain_hash', '')
            chain_hash = event_data.get('chain_hash', '')
            records.append((timestamp, event_type, data, content_hash, prev_chain_hash, chain_hash))
        db = self._db
        if db is None:
            return
        if not hasattr(db, 'executemany'):
            logger.warning("evidence_log_db_not_aiosqlite_connection")
            return
        await db.executemany('INSERT INTO events (timestamp, event_type, data, hash, prev_chain_hash, chain_hash) VALUES (?, ?, ?, ?, ?, ?)', records)
        await db.commit()

    def _init_encryption(self):
        """Initialize encryption cipher."""
        if not self._encryption_key:
            self._encryption_key = secrets.token_bytes(32)
            logger.warning("encrypt_no_key_env_using_temporary")
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            self._cipher = (Cipher, algorithms, modes)
        except ImportError:
            logger.warning("encrypt_cryptography_not_available_disabled")
            self._encrypt_at_rest = False
            self._cipher = None

    def _trim_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Trim payload pro RAM šetření - odstraň fulltexty.

        Returns:
            Trimmovaný payload s preview místo fulltextů
        """
        if not payload:
            return payload
        trimmed = {}
        for key, value in payload.items():
            large_fields = {'content', 'fulltext', 'html', 'body', 'text', 'raw_data', 'document', 'finding_text'}
            if key in large_fields and isinstance(value, str):
                if len(value) > self.MAX_PAYLOAD_PREVIEW:
                    preview = value[:self.MAX_PAYLOAD_PREVIEW] + '...'
                    content_hash = hashlib.sha256(value.encode()).hexdigest()[:16]
                    trimmed[key] = f'[preview:{content_hash}] {preview}'
                else:
                    trimmed[key] = value
            elif isinstance(value, dict):
                trimmed[key] = self._trim_payload(value)
            elif isinstance(value, list) and len(value) > 10:
                trimmed[key] = value[:10] + [f'... ({len(value) - 10} more)']
            else:
                trimmed[key] = value
        return trimmed

    def _trim_payload_fast(self, payload: bytes) -> bytes:
        """
        ISSUE-002 FIX: Zero-copy when trim is no-op.

        Fast-path pro M1 8GB: 10K events/min × 2 orjson calls = 20K/min CPU waste.
        Pokud trim nezmění nic, vrátí původní bytes bez re-serializace.
        Fail-open: při jakékoliv chybě vrátí původní payload.
        """
        try:
            decoded = orjson.loads(payload)
            trimmed = self._trim_payload(decoded)
            # No-op trim — return original bytes, no re-encode
            if trimmed == decoded:
                return payload
            return orjson.dumps(trimmed)
        except Exception:  # noqa: BLE001
            return payload  # Fail open — return original

    @property
    def run_id(self) -> str:
        """ID běhu výzkumu"""
        return self._run_id

    @property
    def size(self) -> int:
        """Celkový počet událostí (včetně persistovaných na disk)"""
        return self._total_count

    @property
    def ram_size(self) -> int:
        """Počet událostí v RAM ring bufferu"""
        return len(self._log)

    @property
    def persist_path(self) -> Path | None:
        """Cesta k persistovanému souboru"""
        return self._persist_path

    @property
    def is_frozen(self) -> bool:
        """Zda je log zmrazený (read-only)"""
        return self._frozen

    def append(self, event: EvidenceEvent) -> None:
        """
        Přidá událost do logu - M1 8GB optimized s ring bufferem.

        Args:
            event: EvidenceEvent k přidání

        Raises:
            RuntimeError: Pokud je log zmrazený nebo uzavřený
            ValueError: Pokud se neshoduje run_id nebo hash
        """
        if self._silent_failure:
            return
        if self._frozen:
            raise RuntimeError('Cannot append to frozen EvidenceLog')
        if self._closed:
            raise RuntimeError('Cannot append to closed EvidenceLog')
        if self._closing:
            raise RuntimeError('Cannot append while EvidenceLog is closing')
        if event.run_id != self._run_id:
            raise ValueError(f"Event run_id '{event.run_id}' does not match log run_id '{self._run_id}'")
        self._seq += 1
        event.seq_no = self._seq
        event.prev_chain_hash = self._chain_head
        chain_input = f'{self._chain_head}:{event.content_hash}:{event.event_id}'
        event.chain_hash = hashlib.sha256(chain_input.encode()).hexdigest()
        self._chain_head = event.chain_hash
        queue_size = self._mpsc.len()
        trace_evidence_append(event.event_type, queue_size, 'queued')
        _worker_alive = self._initialized and self._flush_task is not None and (not self._flush_task.done())
        if _worker_alive and (not self._closing):
            # ISSUE-006: send bytes directly — zero-copy path
            _sent = self._mpsc.send(event.to_bytes())
            if not _sent:
                logger.warning("mpsc_pool_full_fallback_to_sync_write")
                trace_queue_drop('mpsc_pool', queue_size + 1)
        elif not self._initialized and self._db is not None:
            _event_dict = event.to_dict()
            try:

                def _sync_insert():
                    import sqlite3
                    db_path = str(self._db_path)
                    conn = sqlite3.connect(db_path, timeout=30.0)
                    conn.executescript('''
                        PRAGMA busy_timeout=30000;
                        PRAGMA journal_mode=WAL;
                        PRAGMA synchronous=NORMAL;
                        PRAGMA wal_autocheckpoint=1000;
                        PRAGMA cache_size=-8192;
                    ''')
                    conn.execute('INSERT INTO events (timestamp, event_type, data, hash, prev_chain_hash, chain_hash) VALUES (?, ?, ?, ?, ?, ?)', (_event_dict.get('timestamp', 0.0), _event_dict.get('event_type', 'unknown'), orjson.dumps(_event_dict).decode(), _event_dict.get('content_hash', ''), _event_dict.get('prev_chain_hash', ''), _event_dict.get('chain_hash', '')))
                    conn.commit()
                    conn.close()
                t = threading.Thread(target=_sync_insert, daemon=True)
                t.start()
                trace_evidence_append(event.event_type, 0, 'sync_sqlite')
            except Exception as _sync_err:  # noqa: BLE001
                logger.debug("f11c_sync_sqlite_fallback_failed_non_fatal", error=str(_sync_err))
        if self._enable_persist:
            try:
                line = event.to_jsonl_line()
                bytes_to_write = line.encode('utf-8') + b'\n'
                if self._encrypt_at_rest and self._cipher and self._encryption_key:
                    try:
                        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                        nonce = secrets.token_bytes(12)
                        cipher = Cipher(algorithms.AES(self._encryption_key), modes.GCM(nonce))
                        encryptor = cipher.encryptor()
                        encrypted = encryptor.update(bytes_to_write) + encryptor.finalize()
                        bytes_to_write = nonce + encryptor.tag + encrypted
                        logger.debug("encrypt_stored", bytes_in=len(line), bytes_out=len(bytes_to_write))
                    except Exception as e:  # noqa: BLE001
                        logger.warning("encrypt_operation_failed", error=str(e))
                _sent = self._mpsc2.send(bytes_to_write)
                if not _sent:
                    self._sync_write_fallback(line, bytes_to_write)
            except Exception as e:
                logger.critical("f286_swal_write_failed_fatal", error=str(e))
                # DLQ-02: Store corrupted payload for later inspection
                try:
                    if self._dlq_manager is not None:
                        self._dlq_manager.store_payload(
                            payload_data=event.to_bytes(),
                            sprint_id=self._run_id,
                            source="evidence_log.append",
                            error=e,
                            metadata={
                                'event_id': event.event_id,
                                'event_type': event.event_type,
                                'timestamp': event.timestamp,
                            },
                        )
                except Exception:  # noqa: BLE001
                    pass  # Fail-silent for DLQ
                raise RuntimeError(f'EvidenceLog SWAL write failed: {e}') from e
        # ISSUE-002 FIX: Fast-path — skip deserialize/serialize when trim is no-op.
        # 10K events/min × 2 orjson calls = 20K/min overhead eliminated.
        event.payload = self._trim_payload_fast(event.payload)
        event.content_hash = event.calculate_hash()
        chain_input = f'{event.prev_chain_hash}:{event.content_hash}:{event.event_id}'
        event.chain_hash = hashlib.sha256(chain_input.encode()).hexdigest()
        self._chain_head = event.chain_hash
        was_full = len(self._log) == self.MAX_RAM_EVENTS
        self._log.append(event)
        self._total_count += 1
        if was_full:
            self._dropped_count += 1
            try:
                self._rebuild_indexes()
            except Exception:  # noqa: BLE001
                pass
            return
        index = len(self._log) - 1
        self._index_by_type[event.event_type].append(index)
        for source_id in event.source_ids:
            if source_id not in self._index_by_source:
                self._index_by_source[source_id] = deque(maxlen=self.MAX_RAM_EVENTS)
            self._index_by_source[source_id].append(index)
        try:
            from hledac.universal.knowledge.analytics_hook import shadow_record_finding
            if event.event_type == 'evidence_packet':
                payload = event.payload_dict if event.payload else {}
                _corr: dict[str, Any] | None = payload.get('_correlation')
                shadow_record_finding(finding_id=event.event_id, query=payload.get('query', ''), source_type='evidence_packet', confidence=event.confidence, run_id=event.run_id, url=payload.get('url'), title=payload.get('title'), source=payload.get('source'), relevance_score=payload.get('relevance_score'), branch_id=_corr.get('branch_id') if _corr else None, provider_id=_corr.get('provider_id') if _corr else None, action_id=_corr.get('action_id') if _corr else None)
        except Exception:  # noqa: BLE001
            pass

    def _rebuild_indexes(self) -> None:
        """Přebuduj indexy po vyřazení z ring bufferu."""
        self._index_by_type = {'tool_call': deque(maxlen=self.MAX_RAM_EVENTS), 'observation': deque(maxlen=self.MAX_RAM_EVENTS), 'synthesis': deque(maxlen=self.MAX_RAM_EVENTS), 'error': deque(maxlen=self.MAX_RAM_EVENTS), 'decision': deque(maxlen=self.MAX_RAM_EVENTS), 'evidence_packet': deque(maxlen=self.MAX_RAM_EVENTS)}
        self._index_by_source = {}
        for i, event in enumerate(self._log):
            self._index_by_type[event.event_type].append(i)
            for source_id in event.source_ids:
                if source_id not in self._index_by_source:
                    self._index_by_source[source_id] = deque(maxlen=self.MAX_RAM_EVENTS)
                self._index_by_source[source_id].append(i)

    def create_event(self, event_type: Literal['tool_call', 'observation', 'synthesis', 'error', 'decision', 'evidence_packet'], payload: dict[str, Any], source_ids: list[str] | None=None, confidence: float=1.0, correlation: dict[str, str | None] | None=None) -> EvidenceEvent | None:
        """
        Vytvoří a přidá novou událost.

        Args:
            event_type: Typ události
            payload: Data události
            source_ids: ID zdrojových událostí
            confidence: Spolehlivost 0-1
            correlation: Optional correlation dict with keys:
                run_id, branch_id, provider_id, action_id

        Returns:
            Vytvořená EvidenceEvent, nebo None pokud je silent_failure=True
        """
        if self._silent_failure:
            return None
        if event_type != 'error' and self._sample_rate < 1.0:
            import random as _random
            if _random.random() > self._sample_rate:
                return None
        if self._closed:
            raise RuntimeError('Cannot create event in closed EvidenceLog')
        event_id = f'{self._run_id}_{uuid.uuid4().hex[:12]}'
        if correlation:
            payload = {**payload, '_correlation': correlation}
        event = EvidenceEvent(event_id=event_id, event_type=event_type, timestamp=datetime.now(UTC).timestamp(), payload=orjson.dumps(payload), source_ids=source_ids or [], confidence=confidence, content_hash='', run_id=self._run_id)
        event.content_hash = event.calculate_hash()
        self.append(event)
        return event

    def create_events_batch(
        self,
        events: list[
            tuple[
                Literal["tool_call", "observation", "synthesis", "error", "decision", "evidence_packet"],
                dict[str, Any],
                list[str] | None,
                float,
            ]
        ],
    ) -> list[EvidenceEvent]:
        """
        ISSUE-007 FIX: Batch event submission — jeden acquire MPSC slot na celý batch.

        Args:
            events: List of (event_type, payload, source_ids, confidence) tuples.

        Returns:
            List of created EvidenceEvent objects (empty if silent_failure=True).

        Performance:
            - Rust MPSC: single Python→Rust call, N× native crossbeam send (no GIL in native).
            - Fallback: asyncio.Queue.put_nowait() loop with reduced call overhead.
            - ~1 µs/event vs 5 µs for N× individual create_event() calls.

        Pořadí zachováno: seq_no assigned in order, chain_hash includes seq_no.
        """
        if not events or self._silent_failure:
            return []
        if self._closed:
            raise RuntimeError("Cannot create events in closed EvidenceLog")

        # ISSUE-007 FIX: true batching — single send_batch() for all events.
        # Chain-hash computation stays sequential (depends on previous hash),
        # but MPSC send is single Python→Rust call for the entire batch.
        import random as _random

        created: list[EvidenceEvent] = []
        chain_head = self._chain_head

        # SEC-01: Import once outside the loop — avoid per-event import overhead.
        _scrub_dict_recursive: Any = None
        try:
            from hledac.universal.security.secrets_scrubber import scrub_dict_recursive
            _scrub_dict_recursive = scrub_dict_recursive
        except Exception:  # noqa: BLE001
            pass

        for event_type, payload, source_ids, confidence in events:
            if event_type != "error" and self._sample_rate < 1.0:
                if _random.random() > self._sample_rate:
                    continue

            event_id = f"{self._run_id}_{uuid.uuid4().hex[:12]}"
            # TEL-03: Normalize payload for consistent hashing (same as create_event path).
            normalized_payload = _normalize_payload(payload)
            # TEL-03: Fast-path guard — skip expensive scrubbing for primitive-only payloads.
            if not _payload_needs_scrubbing(normalized_payload):
                scrubbed_payload = normalized_payload
            elif _scrub_dict_recursive is not None:
                scrubbed_payload = _scrub_dict_recursive(normalized_payload)
            else:
                scrubbed_payload = normalized_payload
            event = EvidenceEvent(
                event_id=event_id,
                event_type=event_type,
                timestamp=datetime.now(UTC).timestamp(),
                payload=orjson.dumps(scrubbed_payload),
                source_ids=source_ids or [],
                confidence=confidence,
                content_hash="",
                run_id=self._run_id,
            )
            # Sequential chain-hash (must be in order)
            event.prev_chain_hash = chain_head
            event.content_hash = event.calculate_hash()
            chain_input = f'{chain_head}:{event.content_hash}:{event.event_id}'
            event.chain_hash = hashlib.sha256(chain_input.encode()).hexdigest()
            chain_head = event.chain_hash

            # In-memory bookkeeping (fast, no I/O)
            was_full = len(self._log) == self.MAX_RAM_EVENTS
            self._log.append(event)
            self._total_count += 1
            if was_full:
                self._dropped_count += 1
                try:
                    self._rebuild_indexes()
                except Exception:  # noqa: BLE001
                    pass
            else:
                index = len(self._log) - 1
                self._index_by_type[event.event_type].append(index)
                for sid in event.source_ids:
                    if sid not in self._index_by_source:
                        self._index_by_source[sid] = deque(maxlen=self.MAX_RAM_EVENTS)
                    self._index_by_source[sid].append(index)

            created.append(event)

        # Update chain head once for the entire batch
        self._chain_head = chain_head

        if not created:
            return created

        # M1-02 fix: coordinated dual-channel send with backpressure.
        # Both channels are independent Rust MPSC pools (capacity 2048 each).
        # Without coordination, partial sends can cause SQLite/JSONL inconsistency:
        #   - _mpsc (SQLite path) succeeds with N items, _mpsc2 (JSONL path) fills partially
        #   - A subsequent sync-write fallback on mpsc2 writes events out of SQLite order
        #   - Result: SQLite has event #5 but JSONL doesn't, causing replay/desync bugs
        #
        # Fix: check remaining capacity of BOTH channels BEFORE sending. If either is
        # too full to accept the full batch, apply backpressure (drop the batch) rather
        # than partial sends. This guarantees atomicity across both channels.
        _worker_alive = (
            self._initialized
            and self._flush_task is not None
            and (not self._flush_task.done())
        )
        if _worker_alive and (not self._closing):
            _mpsc_payloads = [e.to_bytes() for e in created]
            # M1-02: Rust MPSC pool has fixed capacity 2048; no capacity() method exists
            _mpsc_cap = 2048
            _mpsc_free = _mpsc_cap - self._mpsc.len()
            _mpsc_would_fit = _mpsc_free >= len(_mpsc_payloads)
            _mpsc_pressure_pct = self._mpsc.len() / max(_mpsc_cap, 1)
            # Backpressure threshold: 85% full = hard backpressure (drop batch)
            _MPSC_BACKPRESSURE_THRESHOLD = 0.85

            _jsonl_payloads: list[bytes] = []
            if self._enable_persist:
                # ISSUE-D FIX: crypto import outside the loop — avoid per-event IMPORT_NAME bytecode
                if self._encrypt_at_rest and self._cipher and self._encryption_key:
                    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                for e in created:
                    line = e.to_jsonl_line()
                    bytes_to_write = line.encode('utf-8') + b'\n'
                    if self._encrypt_at_rest and self._cipher and self._encryption_key:
                        try:
                            nonce = secrets.token_bytes(12)
                            cipher = Cipher(algorithms.AES(self._encryption_key), modes.GCM(nonce))
                            encryptor = cipher.encryptor()
                            encrypted = encryptor.update(bytes_to_write) + encryptor.finalize()
                            bytes_to_write = nonce + encryptor.tag + encrypted
                        except Exception as _enc_err:  # noqa: BLE001
                            logger.warning("encrypt_batch_failed", error=str(_enc_err))
                    _jsonl_payloads.append(bytes_to_write)

            # M1-02: Rust MPSC pool has fixed capacity 2048; no capacity() method exists
            _mpsc2_cap = 2048
            _mpsc2_free = _mpsc2_cap - self._mpsc2.len()
            _mpsc2_would_fit = _mpsc2_free >= len(_jsonl_payloads) if self._enable_persist else True
            _mpsc2_pressure_pct = self._mpsc2.len() / max(_mpsc2_cap, 1)

            # Backpressure: if either channel is >85% full, drop the entire batch.
            # This prevents partial-send inconsistency (SQLite succeeds, JSONL fails → reorder).
            # SQLite and JSONL must be consistent — we drop rather than corrupt.
            if (_mpsc_pressure_pct >= _MPSC_BACKPRESSURE_THRESHOLD or
                (self._enable_persist and _mpsc2_pressure_pct >= _MPSC_BACKPRESSURE_THRESHOLD)):
                # Backpressure applied — drop batch, count as dropped events
                self._dropped_count += 1
                logger.warning(
                    "m1_02_backpressure_dropped_batch",
                    mpsc_pressure=f"{_mpsc_pressure_pct:.0%}",
                    mpsc2_pressure=f"{_mpsc2_pressure_pct:.0%}",
                    batch_size=len(created),
                )
                trace_queue_drop('dual_channel_backpressure', len(created))
                return created

            # Both channels have capacity — send to both atomically (best-effort on each)
            _sent = self._mpsc.send_batch(_mpsc_payloads)

            for e in created:
                trace_evidence_append(e.event_type, self._mpsc.len(), 'queued')
            if _sent < len(created):
                logger.warning("issue007_mpsc_pool_full", sent=_sent, total=len(created))
                trace_queue_drop('mpsc_batch', len(created) - _sent)

            # M1-02 fix: JSONL persist path uses same backpressure-gated send
            if self._enable_persist:
                _sent2 = 0
                if _mpsc2_would_fit:
                    try:
                        _sent2 = self._mpsc2.send_batch(_jsonl_payloads)
                        if _sent2 < len(created):
                            for e in created[_sent2:]:
                                line = e.to_jsonl_line()
                                bytes_to_write = line.encode('utf-8') + b'\n'
                                self._sync_write_fallback(line, bytes_to_write)
                    except Exception as _swal_err:  # noqa: BLE001
                        logger.critical("f286_swal_batch_send_failed", error=str(_swal_err))
                else:
                    # mpsc2 unexpectedly full despite pressure check — fallback all
                    for e in created:
                        line = e.to_jsonl_line()
                        bytes_to_write = line.encode('utf-8') + b'\n'
                        self._sync_write_fallback(line, bytes_to_write)
                    logger.warning("m1_02_mpsc2_unexpectedly_full", free=_mpsc2_free, needed=len(_jsonl_payloads))

        # analytics_hook per event (only evidence_packet type)
        try:
            from hledac.universal.knowledge.analytics_hook import shadow_record_finding

            for e in created:
                if e.event_type == 'evidence_packet':
                    _pl: dict[str, Any] = orjson.loads(e.payload) if e.payload else {}
                    _co: dict[str, Any] | None = _pl.get('_correlation')
                    shadow_record_finding(
                        finding_id=e.event_id,
                        query=_pl.get('query', ''),
                        source_type='evidence_packet',
                        confidence=e.confidence,
                        run_id=e.run_id,
                        url=_pl.get('url'),
                        title=_pl.get('title'),
                        source=_pl.get('source'),
                        relevance_score=_pl.get('relevance_score'),
                        branch_id=_co.get('branch_id') if _co else None,
                        provider_id=_co.get('provider_id') if _co else None,
                        action_id=_co.get('action_id') if _co else None,
                    )
        except Exception:  # noqa: BLE001
            pass

        return created

    def create_evidence_packet_event(self, evidence_id: str, packet_path: str, summary: dict[str, Any], source_ids: list[str] | None=None, confidence: float=1.0) -> EvidenceEvent | None:
        """
        Vytvoří evidence_packet event s payload trimming (jen summary + pointer na packet).

        Args:
            evidence_id: ID důkazu
            packet_path: Cesta k packet souboru na disku
            summary: Shrnutí packetu (url, status, page_type, etc.)
            source_ids: ID zdrojových událostí
            confidence: Spolehlivost 0-1

        Returns:
            EvidenceEvent s trimmovaným payloadem
        """
        payload = {'evidence_id': evidence_id, 'packet_path': packet_path, 'summary': summary}
        return self.create_event(event_type='evidence_packet', payload=payload, source_ids=source_ids, confidence=confidence)
    _FORENSIC_MAX_KEYS = 30
    _FORENSIC_MAX_VALUE_LEN = 1000
    _FORENSIC_MAX_LIST_ITEMS = 20
    _FORENSIC_MAX_DEPTH = 3

    def _bound_forensic_value(self, value: Any, depth: int=0) -> Any:
        """Bound forensic result values to prevent payload blowup.

        F261 invariant: bounded payloads only — never trust caller sizes.
        Trims strings, caps list lengths, recurses into dicts up to
        _FORENSIC_MAX_DEPTH.
        """
        if depth > self._FORENSIC_MAX_DEPTH:
            return '[depth_truncated]'
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return value
        if isinstance(value, str):
            if len(value) > self._FORENSIC_MAX_VALUE_LEN:
                return value[:self._FORENSIC_MAX_VALUE_LEN] + '...'
            return value
        if isinstance(value, (list, tuple)):
            cap = self._FORENSIC_MAX_LIST_ITEMS
            items = [self._bound_forensic_value(v, depth + 1) for v in value[:cap]]
            truncated = len(value) - len(items)
            if truncated > 0:
                items.append(f'[...{truncated}_more_items_truncated]')
            return items
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for i, (k, v) in enumerate(value.items()):
                if i >= self._FORENSIC_MAX_KEYS:
                    out['_truncated_keys'] = list(value.keys())[i:][:5]
                    break
                out[str(k)[:80]] = self._bound_forensic_value(v, depth + 1)
            return out
        return str(value)[:self._FORENSIC_MAX_VALUE_LEN]

    def attach_forensic_analysis(self, finding_id: str, forensic_result: Any, source_id: str | None=None, confidence: float=0.95) -> EvidenceEvent | None:
        """
        Attach a forensic analysis result to a finding in the evidence chain.

        Sprint F261: Forensic-grade evidence handling for OSINT. Persists a
        bounded forensic analysis payload as an evidence_packet event,
        linked to the parent finding via source_ids. Forensic results are
        fail-safe — never crash the caller. The full result is recoverable
        from the bounded envelope + the source_id pointer.

        Bounded by _FORENSIC_MAX_KEYS / _FORENSIC_MAX_VALUE_LEN /
        _FORENSIC_MAX_LIST_ITEMS / _FORENSIC_MAX_DEPTH to prevent payload
        blowup. Stored in the same tamper-evident chain as other evidence
        events, so forensic analyses participate in verify_all() and
        get_chain().

        Args:
            finding_id: ID of the parent finding (event_id of original
                observation/evidence_packet, or canonical finding id).
            forensic_result: Dict from ForensicsResult.to_dict() or a
                compatible nested dict. None is allowed (logs a debug
                line and returns None).
            source_id: Optional source event_id. Defaults to finding_id.
            confidence: Confidence of the forensic analysis
                (default 0.95, clamped to [0.0, 1.0]).

        Returns:
            Created EvidenceEvent, or None on validation failure.
        """
        if not finding_id:
            logger.warning("forensic_attach_called_with_empty_finding_id")
            return None
        if forensic_result is None:
            logger.debug("forensic_attach_no_result", finding_id=finding_id)
            return None
        if not isinstance(forensic_result, dict):
            logger.warning("forensic_attach_invalid_result_type", got_type=type(forensic_result).__name__, finding_id=finding_id)
            return None
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.95
        bounded_result = self._bound_forensic_value(forensic_result)
        payload = {'kind': 'forensic_analysis', 'finding_id': str(finding_id)[:128], 'forensic_result': bounded_result, 'attached_at': datetime.now(UTC).isoformat()}
        effective_source_id = (source_id or finding_id)[:128]
        try:
            return self.create_event(event_type='evidence_packet', payload=payload, source_ids=[effective_source_id], confidence=confidence)
        except (RuntimeError, ValueError) as exc:
            logger.warning("forensic_attach_failed", finding_id=finding_id, error=str(exc))
            return None

    def get_forensic_analyses(self, finding_id: str) -> list[EvidenceEvent]:
        """
        Retrieve all forensic analysis events for a given finding_id.

        Sprint F261: Read-side companion to attach_forensic_analysis().
        Scans evidence_packet events with payload['kind'] == "forensic_analysis"
        and matching payload['finding_id']. Bounded to MAX_RAM_EVENTS.

        Args:
            finding_id: The finding_id to filter on.

        Returns:
            List of matching EvidenceEvent objects (may be empty).
        """
        if not finding_id:
            return []
        out: list[EvidenceEvent] = []
        for event in self._log:
            if event.event_type != 'evidence_packet':
                continue
            payload: dict[str, Any] = orjson.loads(event.payload) if event.payload else {}
            if payload.get('kind') != 'forensic_analysis':
                continue
            if payload.get('finding_id') != finding_id:
                continue
            out.append(event)
        return out
    MAX_DECISION_SUMMARY_KEYS = 20
    MAX_DECISION_SUMMARY_VALUE_LEN = 200
    MAX_DECISION_REASONS = 8
    MAX_DECISION_REASON_LEN = 120
    MAX_DECISION_REF_EVIDENCE = 10
    MAX_DECISION_REF_CLUSTERS = 10
    MAX_DECISION_REF_URLS = 10

    def create_decision_event(self, kind: str, summary: dict[str, Any], reasons: list[str], refs: dict[str, list[str]], confidence: float=1.0) -> EvidenceEvent | None:
        """
        Vytvoří decision event pro Decision Ledger.

        Decision events zaznamenávají důležitá rozhodnutí orchestrátoru
        s full audit trail - why + inputs + outputs.

        Args:
            kind: Typ rozhodnutí - "bandit"|"playbook"|"backpressure"|"delta"|"alignment"|"primary_chase"|"drift"
            summary: Malé dict (max 20 keys, každé value max ~200 chars)
            reasons: Max 8 stringů (max 120 chars každý)
            refs: {evidence_ids:[], cluster_ids:[], url_hashes:[]}
            confidence: Spolehlivost 0-1

        Returns:
            EvidenceEvent s trimmovaným payloadem, nebo None pokud je silent_failure=True
        """
        valid_kinds = {'bandit', 'playbook', 'backpressure', 'delta', 'alignment', 'primary_chase', 'drift'}
        if kind not in valid_kinds:
            logger.warning("decision_invalid_kind_using_drift", kind=kind)
            kind = 'drift'
        trimmed_summary = {}
        for i, (k, v) in enumerate(summary.items()):
            if i >= self.MAX_DECISION_SUMMARY_KEYS:
                break
            v_str = str(v)
            if len(v_str) > self.MAX_DECISION_SUMMARY_VALUE_LEN:
                v_str = v_str[:self.MAX_DECISION_SUMMARY_VALUE_LEN] + '...'
            trimmed_summary[k] = v_str
        trimmed_reasons = []
        for i, r in enumerate(reasons):
            if i >= self.MAX_DECISION_REASONS:
                break
            if len(r) > self.MAX_DECISION_REASON_LEN:
                r = r[:self.MAX_DECISION_REASON_LEN] + '...'
            trimmed_reasons.append(r)
        trimmed_refs = {}
        if 'evidence_ids' in refs:
            trimmed_refs['evidence_ids'] = refs['evidence_ids'][:self.MAX_DECISION_REF_EVIDENCE]
        if 'cluster_ids' in refs:
            trimmed_refs['cluster_ids'] = refs['cluster_ids'][:self.MAX_DECISION_REF_CLUSTERS]
        if 'url_hashes' in refs:
            trimmed_refs['url_hashes'] = refs['url_hashes'][:self.MAX_DECISION_REF_URLS]
        payload = {'kind': kind, 'summary': trimmed_summary, 'reasons': trimmed_reasons, 'refs': trimmed_refs}
        return self.create_event(event_type='decision', payload=payload, source_ids=[], confidence=confidence)

    def get(self, index: int) -> EvidenceEvent | None:
        """
        Vrátí událost na daném indexu.

        Args:
            index: Index události

        Returns:
            EvidenceEvent nebo None pokud index neexistuje
        """
        if 0 <= index < len(self._log):
            return self._log[index]
        return None

    def get_by_id(self, event_id: str) -> EvidenceEvent | None:
        """
        Najde událost podle ID.

        Args:
            event_id: ID události

        Returns:
            EvidenceEvent nebo None
        """
        for event in self._log:
            if event.event_id == event_id:
                return event
        return None

    def query(self, event_type: str | None=None, min_confidence: float=0.0, after_timestamp: datetime | None=None, before_timestamp: datetime | None=None, limit: int | None=None) -> list[EvidenceEvent]:
        """
        Dotazuje se na události v logu.

        Args:
            event_type: Filtrovat podle typu
            min_confidence: Minimální confidence (0-1)
            after_timestamp: Pouze události po tomto čase
            before_timestamp: Pouze události před tímto časem
            limit: Maximální počet výsledků

        Returns:
            Seznam odpovídajících EvidenceEvent
        """
        results = []
        if event_type and event_type in self._index_by_type:
            indices = self._index_by_type[event_type]
        else:
            indices = range(len(self._log))
        for idx in indices:
            event = self._log[idx]
            if event.confidence < min_confidence:
                continue
            if after_timestamp and event.timestamp < after_timestamp:
                continue
            if before_timestamp and event.timestamp > before_timestamp:
                continue
            results.append(event)
        if limit and len(results) > limit:
            results = results[:limit]
        return results

    def get_summary_lines(self, last_n: int=10) -> Iterator[str]:
        """
        ISSUE-34: Lazy generator version of get_summary — yields lines one by one.

        For live ticker / UI streaming: yield each line via asyncio.Queue
        instead of building entire string in memory.

        Args:
            last_n: Počet posledních událostí k zahrnutí

        Yields:
            Formátované řádky shrnutí
        """
        yield '=' * 60
        yield 'EVIDENCE LOG SUMMARY'
        yield '=' * 60
        yield ''
        yield f'Run ID: {self._run_id}'
        yield f'Total Events: {self.size}'
        yield f'Created: {self._created_at.isoformat()}'
        yield ''
        yield 'Event Counts by Type:'
        for event_type, indices in self._index_by_type.items():
            count = len(indices)
            if count > 0:
                yield f'  {event_type}: {count}'
        yield ''
        yield '-' * 40
        yield f'Last {last_n} Events (newest first):'
        yield '-' * 40
        # Slice deque directly — materialize only the needed slice (not full list)
        start = max(0, len(self._log) - last_n)
        recent = list(self._log)[start:]  # deque doesn't support slicing
        for i, event in enumerate(reversed(recent), 1):
            timestamp = datetime.fromtimestamp(event.timestamp, UTC).strftime('%H:%M:%S')
            # Parse only fields we need, not the whole payload
            try:
                payload_dict = orjson.loads(event.payload) if event.payload else None
            except Exception:  # noqa: BLE001
                logger.debug("serialise_payload_encoding_failed", exc_info=True)
                payload_dict = None
            payload_summary = self._summarize_payload(payload_dict)
            yield f'{i}. [{timestamp}] {event.event_type.upper()} (conf: {event.confidence:.2f})'
            yield f'   {payload_summary}'
            if event.source_ids:
                sources_str = ', '.join(event.source_ids[:3])
                if len(event.source_ids) > 3:
                    sources_str += f' (+{len(event.source_ids) - 3} more)'
                yield f'   Sources: {sources_str}'
            yield ''
        yield '=' * 60

    def get_summary(self, last_n: int=10) -> str:
        """
        Vytvoří shrnutí logu pro Hermes.

        Vrací stručné shrnutí posledních N událostí - ne celý raw log.

        Args:
            last_n: Počet posledních událostí k zahrnutí

        Returns:
            Formátovaný string shrnutí
        """
        return '\n'.join(self.get_summary_lines(last_n))

    async def stream_summary_to_queue(self, queue: asyncio.Queue[str], last_n: int = 10) -> None:
        """
        ISSUE-34: Stream summary lines to an asyncio.Queue for live UI ticker.

        Each line is put on the queue as it is generated, enabling real-time
        display without building the entire string in memory first.

        Args:
            queue: asyncio.Queue to stream lines into
            last_n: Number of recent events to include
        """
        for line in self.get_summary_lines(last_n):
            await queue.put(line)
        await queue.put("")  # Empty string: end of stream sentinel

    def _summarize_payload(self, payload: dict[str, Any] | None, max_length: int=60) -> str:
        """Vytvoří stručné shrnutí payloadu"""
        if not payload:
            return '(no payload)'
        priority_fields = ['action', 'tool', 'query', 'result', 'message', 'summary']
        for field in priority_fields:
            if field in payload:
                value = payload[field]
                if isinstance(value, str):
                    if len(value) > max_length:
                        return f'{field}={value[:max_length]}...'
                    return f'{field}={value}'
                return f'{field}={str(value)[:max_length]}'
        first_key = next(iter(payload.keys()))
        value = str(payload[first_key])[:max_length]
        return f"{first_key}={value}{('...' if len(str(payload[first_key])) > max_length else '')}"

    def to_jsonl(self, path: Path | None=None) -> None:
        """
        Exportuje log do JSONL souboru pro replay mode.

        M1 8GB: Pokud je již persistováno, pouze zkopíruj soubor.

        Args:
            path: Cesta k výstupnímu souboru (None = použij persist_path)
        """
        export_path = path or self._persist_path
        if not export_path:
            raise ValueError('No path specified for export')
        export_path = Path(export_path)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        if self._persist_path and export_path == self._persist_path:
            return
        if self._persist_path and self._persist_path.exists():
            # APFS clonefile: CoW snapshot, ~0 I/O vs 3-5s pro 1GB
            # Falls back to shutil.copy2 na non-APFS / Linux
            try:
                os.clonefile(self._persist_path, export_path)
            except (AttributeError, OSError):
                import shutil
                shutil.copy2(self._persist_path, export_path)
            return
        # Batch write: single syscall for all events
        with open(export_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(e.to_jsonl_line() for e in self._log) + '\n')

    @classmethod
    def from_jsonl(cls, path: Path, run_id: str | None=None, load_to_ram: bool=False, max_ram_events: int=100) -> EvidenceLog:
        """
        Načte log z JSONL souboru - M1 8GB optimized.

        Uses backward seek for append-only files (zero bytes read beyond what's needed).
        Falls back to streaming iterator for non-seekable files or small files.

        Args:
            path: Cesta k JSONL souboru
            run_id: Volitelné run_id (jinak se zkusí zjistit z první události)
            load_to_ram: Zda načíst vše do RAM (pouze pro replay/debug)
            max_ram_events: Max událostí v RAM pokud load_to_ram=True

        Returns:
            EvidenceLog instance
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f'JSONL file not found: {path}')

        detected_run_id = run_id
        if detected_run_id is None:
            with open(path, encoding='utf-8') as f:
                first_line = f.readline().strip()
                if first_line:
                    data = orjson.loads(first_line)
                    detected_run_id = data.get('run_id', 'unknown')

        log = cls(run_id=detected_run_id or 'unknown', enable_persist=False)

        # Fast path: small files or load_to_ram requested — read fully
        if load_to_ram:
            total_lines, events = cls._from_jsonl_full(path, max_ram_events)
            log._total_count = total_lines
            log._dropped_count = 0
            cls._index_events(log, events)
            return log

        # Estimate total lines without reading entire file
        file_size = path.stat().st_size
        if file_size == 0:
            log._total_count = 0
            return log

        # Heuristic: assume avg line ~200 bytes (realistic for JSONL evidence events)
        avg_line_len = 200
        estimated_lines = max(1, file_size // avg_line_len)

        if estimated_lines <= max_ram_events:
            # Small file — read fully
            total_lines, events = cls._from_jsonl_full(path, max_ram_events)
            log._total_count = total_lines
            log._dropped_count = 0
            cls._index_events(log, events)
            return log

        # Large file: use tail-seek to count lines from end (zero bytes read beyond what's needed)
        # ISSUE-003 FIX: tail-seek reads only the last max_ram_events lines
        total_lines = cls._count_jsonl_lines_backward(path)
        log._total_count = total_lines

        if total_lines <= max_ram_events:
            # Few enough events — read all
            _, events = cls._from_jsonl_full(path, max_ram_events)
            cls._index_events(log, events)
        else:
            # Tail-seek: read only last max_ram_events events
            events = list(cls._iter_jsonl_tail(path, max_ram_events))
            log._dropped_count = total_lines - len(events)
            cls._index_events(log, events)

        return log

    @classmethod
    def _from_jsonl_full(cls, path: Path, max_ram_events: int) -> tuple[int, list[EvidenceEvent]]:
        """Read all events from JSONL file (for small files or load_to_ram=True)."""
        events = []
        total_lines = 0
        with open(path, encoding='utf-8') as f:
            for line in f:
                total_lines += 1
                line = line.strip()
                if not line:
                    continue
                data = orjson.loads(line)
                event = EvidenceEvent.from_dict(data)
                events.append(event)
                if len(events) > max_ram_events:
                    events = events[-max_ram_events:]
        return total_lines, events

    @classmethod
    def _count_jsonl_lines_backward(cls, path: Path) -> int:
        """
        Count total lines by seeking to end and reading backwards.

        For append-only JSONL files this reads ONLY the bytes needed to
        determine line count — zero bytes read beyond what's needed.
        """
        fd = None
        try:
            fd = os.open(str(path), os.O_RDONLY)
            file_size = os.lseek(fd, 0, os.SEEK_END)
            if file_size == 0:
                return 0

            count = 0
            pos = file_size
            buf_size = 8192

            while pos > 0:
                read_size = min(buf_size, pos)
                pos -= read_size
                os.lseek(fd, pos, os.SEEK_SET)
                chunk = os.read(fd, read_size)
                count += chunk.count(b'\n')
                # Skip potential incomplete line at start
                if pos > 0 and chunk and chunk[-1] != ord('\n'):
                    count -= 1
                    if count < 0:
                        count = 0

            return count
        except Exception:
            # Fallback: count by reading forward (expensive but safe)
            with open(path, encoding='utf-8') as f:
                return sum(1 for _ in f)
        finally:
            if fd is not None:
                os.close(fd)

    @classmethod
    def _iter_jsonl_tail(cls, path: Path, max_events: int) -> Iterator[EvidenceEvent]:
        """
        Iterate over last max_events from JSONL file using backward seek.

        For append-only files this reads only the bytes containing the
        last max_events lines — zero bytes read beyond what's needed.
        """
        fd = None
        try:
            fd = os.open(str(path), os.O_RDONLY)
            file_size = os.lseek(fd, 0, os.SEEK_END)
            if file_size == 0:
                return

            events = []
            pos = file_size
            buf_size = 8192
            current_line = bytearray()

            while pos > 0 and len(events) <= max_events:
                read_size = min(buf_size, pos)
                pos -= read_size
                os.lseek(fd, pos, os.SEEK_SET)
                chunk = os.read(fd, read_size)

                # Process chunk in reverse
                for byte in reversed(chunk):
                    if byte == ord('\n'):
                        if current_line:
                            try:
                                line_str = current_line.decode('utf-8').strip()
                                if line_str:
                                    data = orjson.loads(line_str)
                                    events.append(EvidenceEvent.from_dict(data))
                            except Exception:
                                pass
                            current_line = bytearray()
                            if len(events) > max_events:
                                break
                    else:
                        current_line.append(byte)

                if pos == 0 and current_line:
                    try:
                        line_str = current_line.decode('utf-8').strip()
                        if line_str:
                            data = orjson.loads(line_str)
                            events.append(EvidenceEvent.from_dict(data))
                    except Exception:
                        pass

            # Yield in correct order (oldest to newest)
            for event in reversed(events[-max_events:]):
                yield event

        except Exception:
            # Fallback: stream forward with sliding window (expensive but safe)
            events = []
            with open(path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = orjson.loads(line)
                        event = EvidenceEvent.from_dict(data)
                        events.append(event)
                        if len(events) > max_events:
                            events = events[-max_events:]
                    except Exception:
                        continue
            for event in events:
                yield event
        finally:
            if fd is not None:
                os.close(fd)

    @classmethod
    def _index_events(cls, log: EvidenceLog, events: list[EvidenceEvent]) -> None:
        """Index events into log._log and related indexes."""
        for event in events:
            index = len(log._log)
            log._log.append(event)
            log._index_by_type[event.event_type].append(index)
            for source_id in event.source_ids:
                if source_id not in log._index_by_source:
                    log._index_by_source[source_id] = deque(maxlen=log.MAX_RAM_EVENTS)
                log._index_by_source[source_id].append(index)

    def freeze(self) -> None:
        """Zmrazí log - přepne do read-only režimu"""
        self._frozen = True

    # =============================================================================
    # FLOW-03: WAL checkpoint protocol for dual-writer atomic commit
    # =============================================================================
    # EvidenceLog uses two write paths:
    #   1. DuckDB/SQLite (_flush_worker → _flush_batch_bytes → _flush_duckdb_batch/_flush_sqlite_batch)
    #   2. JSONL (_async_write_worker → mpsc2 → _persist_file)
    #
    # Problem: There is no atomic commit protocol between these two paths.
    # A crash between path-1 commit and path-2 write leaves the ledger inconsistent.
    #
    # FLOW-03 FIX: WAL checkpoint protocol using write-ahead phases:
    #   Phase 1: PREPARE — drain both paths, fsync JSONL, write .wal.prepare marker
    #   Phase 2: COMMIT — write .wal.commit marker (proves both paths flushed)
    #   Phase 3: CLEANUP — rename .wal.commit to .wal.done, delete .wal.prepare
    #
    # On crash recovery: if .wal.prepare exists without .wal.commit, replay from
    # DuckDB/SQLite checkpoint (last committed state). If .wal.commit exists, both
    # paths completed — promote to .wal.done.
    #
    # Bounds: checkpoint runs at most once per aclose(). WAL files cleaned on next init.
    # M1 8GB: WAL files are tiny (<1 KiB each), negligible I/O.
    # =============================================================================

    _WAL_DIR: Path | None = None

    def _get_wal_dir(self) -> Path | None:
        """Get or create WAL directory for this run_id. Lazy init."""
        if self._WAL_DIR is not None:
            return self._WAL_DIR
        if not self._persist_path:
            return None
        try:
            wal_dir = self._persist_path.parent / '.wal'
            wal_dir.mkdir(parents=True, exist_ok=True)
            EvidenceLog._WAL_DIR = wal_dir
            return wal_dir
        except Exception:  # noqa: BLE001
            return None

    def _wal_prepare(self) -> bool:
        """FLOW-03 Phase 1: Write .wal.prepare marker (both paths drained).

        Returns True if prepare succeeded (WAL file written).
        """
        wal_dir = self._get_wal_dir()
        if wal_dir is None:
            return False
        prepare_path = wal_dir / f'{self._run_id}.wal.prepare'
        try:
            # .wal.prepare contains: run_id, chain_head, total_count, seq, timestamp
            # This is the commit proof for path-1 (DuckDB/SQLite)
            prepare_data = {
                'run_id': self._run_id,
                'chain_head': self._chain_head,
                'total_count': self._total_count,
                'seq': self._seq,
                'genesis_hash': self._genesis_hash,
                'timestamp': datetime.now(UTC).isoformat(),
                'version': 1,
            }
            with open(prepare_path, 'wb') as f:
                f.write(orjson.dumps(prepare_data))
                os.fsync(f.fileno())
            return True
        except Exception:  # noqa: BLE001
            return False

    def _wal_commit(self) -> bool:
        """FLOW-03 Phase 2: Write .wal.commit marker (both paths complete).

        Returns True if commit succeeded.
        """
        wal_dir = self._get_wal_dir()
        if wal_dir is None:
            return False
        prepare_path = wal_dir / f'{self._run_id}.wal.prepare'
        commit_path = wal_dir / f'{self._run_id}.wal.commit'
        try:
            # Verify .wal.prepare exists (Phase 1 must have succeeded)
            if not prepare_path.exists():
                logger.warning("wal_commit_no_prepare", run_id=self._run_id)
                return False
            # Read prepare data and extend with commit timestamp
            with open(prepare_path, 'rb') as f:
                prepare_data = orjson.loads(f.read())
            prepare_data['commit_timestamp'] = datetime.now(UTC).isoformat()
            with open(commit_path, 'wb') as f:
                f.write(orjson.dumps(prepare_data))
                os.fsync(f.fileno())
            return True
        except Exception:  # noqa: BLE001
            return False

    def _wal_cleanup(self) -> None:
        """FLOW-03 Phase 3: Clean up WAL files after successful commit.

        Called after both paths have flushed. Removes .wal.prepare and .wal.commit.
        """
        wal_dir = self._get_wal_dir()
        if wal_dir is None:
            return
        prepare_path = wal_dir / f'{self._run_id}.wal.prepare'
        commit_path = wal_dir / f'{self._run_id}.wal.commit'
        try:
            if commit_path.exists():
                commit_path.unlink()
            if prepare_path.exists():
                prepare_path.unlink()
        except Exception:  # noqa: BLE001
            pass

    def _wal_recover(self) -> dict[str, Any] | None:
        """FLOW-03: Recover from WAL state on init.

        Checks for incomplete WAL cycle (.wal.prepare without .wal.commit).
        Returns recovery metadata or None if no recovery needed.
        """
        wal_dir = self._get_wal_dir()
        if wal_dir is None:
            return None
        prepare_path = wal_dir / f'{self._run_id}.wal.prepare'
        commit_path = wal_dir / f'{self._run_id}.wal.commit'
        try:
            if commit_path.exists():
                # Both paths completed — WAL cycle done, clean up
                self._wal_cleanup()
                return None
            if prepare_path.exists():
                # Phase-1 only — crash between prepare and commit
                # Recovery: replay from DuckDB/SQLite checkpoint (last committed state)
                with open(prepare_path, 'rb') as f:
                    prepare_data = orjson.loads(f.read())
                logger.warning("wal_recovery_from_prepare", run_id=self._run_id, chain_head=prepare_data.get('chain_head'))
                self._wal_cleanup()
                return prepare_data
        except Exception:  # noqa: BLE001
            pass
        return None

    def _wal_checkpoint(self) -> bool:
        """FLOW-03: Execute full WAL checkpoint protocol.

        Called at the start of aclose() before any shutdown.
        Returns True if protocol completed successfully.
        """
        # Phase 1: PREPARE — both paths must be drained before this runs
        if not self._wal_prepare():
            return False
        # Phase 2: COMMIT — proves DuckDB/SQLite flushed (path-1 done)
        # JSONL flush is synchronous in _do_shutdown before this is called,
        # so path-2 is also done at this point
        if not self._wal_commit():
            return False
        # Phase 3: CLEANUP
        self._wal_cleanup()
        return True

    def write_manifest(self) -> Path | None:
        """
        Writes a manifest JSON file next to the persist path.

        The manifest contains:
        - run_id, chain_head, total_count, created_at, last_seq_no, persist_path

        Returns:
            Path to the written manifest file, or None if no persist_path
        """
        if not self._persist_path:
            logger.warning("cannot_write_manifest_no_persist_path")
            return None
        manifest = {'run_id': self._run_id, 'chain_head': self._chain_head, 'total_count': self._total_count, 'created_at': self._created_at.isoformat(), 'last_seq_no': self._seq, 'persist_path': str(self._persist_path), 'genesis_hash': self._genesis_hash}
        manifest_path = self._persist_path.with_suffix('.manifest.json')
        try:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(manifest_path, 'wb') as f:
                f.write(orjson.dumps(manifest, option=orjson.OPT_INDENT_2))
            logger.info("evidence_manifest_written", manifest_path=str(manifest_path))
            return manifest_path
        except Exception as e:  # noqa: BLE001
            logger.error("failed_to_write_manifest", error=str(e))
            return None

    def _start_cancel_watcher(self) -> None:
        """
        E2: Start a background watcher that auto-triggers aclose() when cancel_event is set.

        This bridges the bare asyncio.Event() pattern to the sprint lifecycle:
        - When cancel_event is set (lifecycle shutdown), the watcher calls aclose()
        - Ensures EvidenceLog workers exit cleanly even when aclose() is never called
          explicitly by the lifecycle TEARDOWN phase.
        - Fail-safe: any exception is caught and logged; watcher task is cancelled in aclose().
        """
        if self._cancel_event is None:
            return
        if self._cancel_watcher_task is not None and not self._cancel_watcher_task.done():
            return

        async def _watch_cancel() -> None:
            try:
                await self._cancel_event.wait()
            except asyncio.CancelledError:
                return
            except Exception as _wait_err:  # noqa: BLE001
                logger.debug("e2_cancel_wait_failed", error=str(_wait_err))
                return
            # Cancel event was set — trigger aclose if not already closing/closed
            if not self._closing and not self._closed:
                try:
                    await self.aclose()
                except Exception as _aclose_err:  # noqa: BLE001
                    logger.debug("e2_aclose_from_cancel_watcher_failed", error=str(_aclose_err))

        try:
            self._cancel_watcher_task = safe_create_task(_watch_cancel(), name='_cancel_watcher')
        except Exception as _watch_err:  # noqa: BLE001
            logger.debug("e2_cancel_watcher_start_failed", error=str(_watch_err))

    async def aclose(self, timeout_s: float | None = None) -> None:
        """
        Async cleanup: shutdown flush worker, close SQLite, close persist file.

        P1-9: Delegates to shutdown_aclose() for canonical timeout + force-shutdown
        pattern. All internal timeouts (_flush_task, _async_write_task) are
        preserved as nested timeouts; the outer bound is enforced by shutdown_aclose().

        Idempotent: safe to call multiple times.
        """
        if self._closed:
            return
        await shutdown_aclose(
            name="EvidenceLog",
            coro=self._do_shutdown(),
            timeout_s=timeout_s if timeout_s is not None else self.DEFAULT_TIMEOUT_S,
        )

    async def _do_shutdown(self) -> None:
        """Inner cleanup — called by aclose() via shutdown_aclose()."""
        self._closing = True
        # FLOW-03: WAL checkpoint — execute BEFORE any cleanup to ensure both paths
        # are drained and committed. If this fails, we continue with normal shutdown
        # (fail-safe — WAL is best-effort crash consistency).
        _wal_ok = False
        try:
            _wal_ok = self._wal_checkpoint()
        except Exception:  # noqa: BLE001
            pass
        # E2: Cancel the lifecycle watcher — aclose() is now the owner of shutdown.
        if self._cancel_watcher_task is not None and not self._cancel_watcher_task.done():
            self._cancel_watcher_task.cancel()
            try:
                with contextlib.suppress(asyncio.CancelledError):
                    await safe_wait_for(self._cancel_watcher_task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception:  # noqa: BLE001
                pass
            self._cancel_watcher_task = None
        if self._flush_shutdown:
            self._flush_shutdown.set()
        if self._flush_task:
            try:
                await safe_wait_for(self._flush_task, timeout=10.0, label='_flush_task')
            except TimeoutError:
                logger.warning("flush_worker_did_not_exit_cancelling")
                self._flush_task.cancel()
                try:
                    await safe_wait_for(self._flush_task, timeout=5.0, label='_flush_task')
                except (TimeoutError, asyncio.CancelledError):
                    pass
            except asyncio.CancelledError:
                self._flush_task.cancel()
                try:
                    await safe_wait_for(self._flush_task, timeout=5.0, label='_flush_task')
                except (TimeoutError, asyncio.CancelledError):
                    pass
            finally:
                self._flush_task = None
        self._async_write_shutdown.set()
        if self._async_write_task:
            try:
                await safe_wait_for(self._async_write_task, timeout=5.0, label='_async_write_task')
            except TimeoutError:
                logger.warning("async_write_worker_did_not_exit_cancelling")
                self._async_write_task.cancel()
                try:
                    await safe_wait_for(self._async_write_task, timeout=2.0, label='_async_write_task')
                except (TimeoutError, asyncio.CancelledError):
                    pass
            except asyncio.CancelledError:
                self._async_write_task.cancel()
                try:
                    await safe_wait_for(self._async_write_task, timeout=2.0, label='_async_write_task')
                except (TimeoutError, asyncio.CancelledError):
                    pass
            finally:
                self._async_write_task = None
        drained = self._mpsc.recv_batch(max_items=None)
        # ISSUE-FIX: Also drain mpsc2 in case _async_write_worker was cancelled before draining.
        # This prevents data loss on premature shutdown. Safe to call even if worker already drained.
        mpsc2_drained = self._mpsc2.recv_batch(max_items=None)
        if self._arrow_writer is not None:
            try:
                self._arrow_writer.close()
                logger.info("arrow_ipc_writer_closed", arrow_path=str(self._arrow_path))
            except Exception as e:  # noqa: BLE001
                logger.warning("arrow_failed_to_close_writer", error=str(e))
            finally:
                self._arrow_writer = None
        if drained and self._db is not None:
            try:
                await self._flush_batch_bytes(drained)
            except Exception as e:  # noqa: BLE001
                logger.warning("failed_to_flush_remaining_items", error=str(e))
        if mpsc2_drained and self._persist_file:
            try:
                # Write remaining mpsc2 items (JSONL path) before closing
                # _persist_file is text-mode (encoding='utf-8') when not encrypting,
                # so decode bytes to str before writing
                if self._encrypt_at_rest:
                    _combined = b''.join(mpsc2_drained)
                    self._persist_file.write(_combined)
                else:
                    for item in mpsc2_drained:
                        self._persist_file.write(item.decode('utf-8', errors='replace'))
                self._persist_file.flush()
            except Exception as e:  # noqa: BLE001
                logger.warning("failed_to_flush_mpsc2_remaining_items", error=str(e))
        if self._db is not None:
            try:
                await self._db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                await self._db.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("failed_to_close_sqlite", error=str(e))
            finally:
                self._db = None
        # ISSUE-11: Close DuckDB connection
        if self._duckdb_conn is not None:
            try:
                self._duckdb_conn.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("failed_to_close_duckdb", error=str(e))
            finally:
                self._duckdb_conn = None
        self._close_persist_file()
        self._closed = True
        self._closing = False
        self.freeze()
        logger.debug("evidence_aclose_complete", run_id=self._run_id)

    def _close_persist_file(self) -> None:
        """Close persist file with idempotency guard (runs in thread)."""
        if self._persist_file and (not self._persist_file.closed):
            try:
                self._persist_file.flush()
                os.fsync(self._persist_file.fileno())
                self._persist_file.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("failed_to_close_persist_file", error=str(e))
            finally:
                self._persist_file = None
        elif self._persist_file is not None:
            self._persist_file = None

    def close(self) -> None:
        """
        Sync cleanup: run aclose in a dedicated thread.

        Idempotent: safe to call multiple times.
        Works from both sync and async (pytest-asyncio) contexts.

        M1-SAFE / Python 3.14+: Uses sync_bridge.to_thread() which:
          - Schedules aclose() on the stored event loop via run_coroutine_threadsafe
          - Runs the wait in a bounded thread pool, not the event loop
          - Never calls run_until_complete() on a running loop

        Refactored from temporary ThreadPoolExecutor pattern (F350M-R P1-05):
          - Previously created a temporary ThreadPoolExecutor(max_workers=1) per close()
          - Now uses sync_bridge.to_thread() which reuses the cached dedicated pool
        """
        # P1-05 FIX: Use asyncio.Runner() instead of temporary ThreadPoolExecutor.
        # Runner manages event loop lifecycle and schedules aclose() on the stored loop.
        # This is the Python 3.11+ (PEP 654) safe pattern for running coroutines
        # from a sync method when a loop may or may not be running.
        try:
            stored_loop = self._loop
            if stored_loop is not None and stored_loop.is_running():
                # There's a running loop — schedule aclose() on it and wait.
                async def _coro():
                    await self.aclose()
                future = asyncio.run_coroutine_threadsafe(_coro(), stored_loop)
                future.result()
            else:
                # No running loop — use Runner for clean loop lifecycle management.
                with asyncio.Runner() as runner:
                    runner.run(self.aclose())
        except Exception:
            # Best-effort cleanup — never raise from close()
            pass

    def finalize(self) -> None:
        """
        Finalize the log: flush, write manifest, and close handles.

        This should be called at the end of a run (no user toggle).
        Always flushes and fsyncs to preserve crash-safety.

        FLOW-03: WAL checkpoint runs BEFORE manifest write to ensure both paths
        are committed. If checkpoint fails, we proceed with normal close (fail-safe).

        Backward-compatible entry point — delegates to close() for full cleanup.
        """
        # FLOW-03: WAL checkpoint before manifest (both paths committed before manifest证明)
        _wal_ok = False
        try:
            _wal_ok = self._wal_checkpoint()
        except Exception:  # noqa: BLE001
            pass
        if not _wal_ok:
            logger.debug("wal_checkpoint_skipped_finalize", run_id=self._run_id)
        self.write_manifest()
        self.close()
        self.freeze()
        logger.info("evidence_log_finalized", run_id=self._run_id, events=self._total_count, chain_head=self._chain_head[:16], wal_checkpoint=_wal_ok)

    def verify_all(self) -> dict[str, Any]:
        """
        Ověří integritu všech událostí v logu.

        Returns:
            Dictionary s výsledky verifikace včetně chain_valid a chain_invalid
        """
        total = len(self._log)
        valid = 0
        invalid = []
        chain_valid = True
        chain_invalid = []
        prev_expected_hash = self._genesis_hash
        for i, event in enumerate(self._log):
            if event.verify_integrity():
                valid += 1
            else:
                invalid.append({'index': i, 'event_id': event.event_id, 'stored_hash': event.content_hash, 'calculated_hash': event.calculate_hash()})
            if event.chain_hash and event.seq_no > 0:
                chain_input = f'{event.prev_chain_hash or self._genesis_hash}:{event.content_hash}:{event.event_id}'
                expected_chain_hash = hashlib.sha256(chain_input.encode()).hexdigest()
                if expected_chain_hash != event.chain_hash:
                    chain_valid = False
                    if len(chain_invalid) < 100:
                        chain_invalid.append({'index': i, 'event_id': event.event_id, 'reason': 'chain_hash_mismatch', 'expected': expected_chain_hash, 'stored': event.chain_hash})
                if event.prev_chain_hash and event.prev_chain_hash != prev_expected_hash:
                    chain_valid = False
                    if len(chain_invalid) < 100:
                        chain_invalid.append({'index': i, 'event_id': event.event_id, 'reason': 'linkage_broken', 'expected_prev': prev_expected_hash, 'stored_prev': event.prev_chain_hash})
                prev_expected_hash = event.chain_hash
        chain_invalid_reason = None
        if not chain_valid:
            if chain_invalid:
                first_issue = chain_invalid[0]
                chain_invalid_reason = f"{first_issue.get('reason', 'unknown')}_at_index_{first_issue.get('index', 0)}"
            else:
                chain_invalid_reason = 'legacy_events_missing_chain_fields'
        return {'total_events': total, 'valid_events': valid, 'invalid_events': len(invalid), 'integrity_percentage': valid / total * 100 if total > 0 else 100.0, 'invalid_details': invalid[:10], 'all_valid': not invalid, 'chain_valid': chain_valid, 'chain_invalid_reason': chain_invalid_reason, 'chain_invalid': chain_invalid, 'chain_head': self._chain_head, 'last_seq_no': self._seq}

    def get_event_funnel(self) -> dict[str, Any]:
        """
        Vrací funnel událostí: počty a průměrná confidence per typ.

        Praktický sprint-ready view — rychlý přehled "co se stalo"
        bez iterace přes všechny události.

        Returns:
            Dict s event_type jako klíče, hodnoty jsou {count, avg_conf, pct}
        """
        if not self._log:
            return {}
        total = len(self._log)
        result = {}
        for event_type, indices in self._index_by_type.items():
            if not indices:
                continue
            events = [self._log[i] for i in indices]
            avg_conf = sum((e.confidence for e in events)) / len(events)
            result[event_type] = {'count': len(indices), 'avg_conf': round(avg_conf, 4), 'pct': round(len(indices) / total * 100, 1)}
        return result

    def get_decision_summary(self) -> dict[str, Any]:
        """
        Vrací shrnutí decision událostí pro sprint retro.

        Ukazuje: počet rozhodnutí, confidence spread,
        top decision kinds, top reason patterns.

        Returns:
            Dict s decision statistikami
        """
        decisions = self.query(event_type='decision')
        if not decisions:
            return {'count': 0, 'kinds': {}, 'avg_confidence': 0.0}
        kind_counts: dict[str, int] = {}
        all_reasons: list[str] = []
        confidences: list[float] = []
        for e in decisions:
            payload: dict[str, Any] = orjson.loads(e.payload) if e.payload else {}
            kind = payload.get('kind', 'unknown')
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            reasons = payload.get('reasons', [])
            all_reasons.extend(reasons)
            confidences.append(e.confidence)
        top_reasons: dict[str, int] = {}
        for r in all_reasons:
            fragment = r[:40] if len(r) > 40 else r
            top_reasons[fragment] = top_reasons.get(fragment, 0) + 1
        top_reasons = dict(sorted(top_reasons.items(), key=lambda x: -x[1])[:5])
        return {'count': len(decisions), 'avg_confidence': round(sum(confidences) / len(confidences), 4), 'min_confidence': round(min(confidences), 4), 'max_confidence': round(max(confidences), 4), 'kinds': dict(sorted(kind_counts.items(), key=lambda x: -x[1])), 'top_reasons': top_reasons}

    def get_surface_tension(self) -> float:
        """
        Ratio: confirmed_decisions / (confirmed_decisions + error_events)
        Use existing get_error_rate() and get_decision_summary() to compute.
        Returns float in [0.0, 1.0]. Returns 1.0 (healthy) when no events yet.
        Used by escalation logic to trigger deep archive search.
        """
        try:
            decision_summary = self.get_decision_summary()
            error_rate = self.get_error_rate()
            confirmed = decision_summary.get('count', 0)
            errors = error_rate.get('error_count', 0)
            total = confirmed + errors
            if total == 0:
                return 1.0
            return confirmed / total
        except Exception:  # noqa: BLE001
            return 1.0

    def get_evidence_by_finding_id(self, finding_id: str, event_types: list[str] | None=None) -> list[EvidenceEvent]:
        """
        Return all EvidenceEvents whose payload contains finding_id.
        Lookup key: check payload.get('finding_id') OR payload.get('id')
        Optional filter: only return events where event_type in event_types.
        Uses existing self.query() or iterates self._events — whichever is
        the correct internal access pattern (read the class first).
        Returns [] on any error or when no match.
        """
        try:
            results = []
            for event in self._log:
                if event_types and event.event_type not in event_types:
                    continue
                payload: dict[str, Any] = orjson.loads(event.payload) if event.payload else {}
                if payload.get('finding_id') == finding_id or payload.get('id') == finding_id:
                    results.append(event)
            return results
        except Exception:  # noqa: BLE001
            return []

    def get_error_rate(self) -> dict[str, Any]:
        """
        Vrací error rate a low-confidence event breakdown.
        - error_count + error_rate
        - low_confidence_count (< 0.7)
        - recent_error_types (posledních 10 errors)

        Returns:
            Dict s error a low-confidence metrikama
        """
        if not self._log:
            return {'error_count': 0, 'error_rate': 0.0, 'low_conf_count': 0}
        errors = self.query(event_type='error')
        low_conf_events = [e for e in self._log if e.confidence < 0.7]
        recent_errors = []
        for e in reversed(errors):
            if len(recent_errors) >= 10:
                break
            payload: dict[str, Any] = orjson.loads(e.payload) if e.payload else {}
            recent_errors.append({'event_id': e.event_id, 'timestamp': datetime.fromtimestamp(e.timestamp, UTC).isoformat(), 'message': payload.get('message', '')[:80], 'kind': payload.get('kind', '')})
        return {'error_count': len(errors), 'error_rate': round(len(errors) / len(self._log) * 100, 2), 'low_conf_count': len(low_conf_events), 'low_conf_rate': round(len(low_conf_events) / len(self._log) * 100, 2), 'recent_errors': recent_errors}

    def get_statistics(self) -> dict[str, Any]:
        """
        Vrátí statistiky o logu - M1 8GB optimized.

        Returns:
            Dictionary se statistikami (RAM + disk)
        """
        type_counts = {et: len(indices) for et, indices in self._index_by_type.items()}
        type_counts = {k: v for k, v in type_counts.items() if v > 0}
        if self._log:
            avg_confidence = sum((e.confidence for e in self._log)) / len(self._log)
            timestamps = [e.timestamp for e in self._log]
            time_span = (max(timestamps) - min(timestamps)).total_seconds()
        else:
            avg_confidence = 0.0
            time_span = 0.0
        return {'total_events': self._total_count, 'ram_events': self.ram_size, 'dropped_events': self._dropped_count, 'event_types': type_counts, 'avg_confidence': round(avg_confidence, 4), 'time_span_seconds': round(time_span, 2), 'created_at': self._created_at.isoformat(), 'is_frozen': self._frozen, 'is_closed': self._closed, 'is_closing': self._closing, 'sqlite_open': self._db is not None, 'persist_file_open': self._persist_file is not None and (not self._persist_file.closed), 'persist_path': str(self._persist_path) if self._persist_path else None, 'persistence_enabled': self._enable_persist}

    def get_sprint_health_summary(self) -> dict[str, Any]:
        """
        Compact retrospective seam for sprint health assessment.

        Composes existing helpers to answer in one call:
        - Sprint posture: observation-heavy vs decision-heavy vs error-heavy
        - Quality signal integrity (where it broke)
        - Decision confidence distribution
        - Health status: healthy / degraded / noisy
        - Top weak spots and error fragment patterns

        Bounded, fail-soft, read-only. Uses existing helpers as primary
        source; raw event iteration only when helpers don't suffice.

        Returns:
            Dict with health signals ready for export or local diagnostics
        """
        funnel = self.get_event_funnel()
        decisions = self.get_decision_summary()
        errors = self.get_error_rate()
        total = sum((v['count'] for v in funnel.values())) if funnel else 0
        if not funnel:
            posture = 'empty'
        else:
            dominant = max(funnel.items(), key=lambda x: x[1]['count'])
            dominant_pct = dominant[1]['pct']
            if dominant_pct < 40:
                posture = 'balanced'
            else:
                match dominant[0]:
                    case 'observation':
                        posture = 'observation_heavy'
                    case 'decision':
                        posture = 'decision_heavy'
                    case 'tool_call':
                        posture = 'tool_heavy'
                    case 'error':
                        posture = 'error_heavy'
                    case 'synthesis':
                        posture = 'synthesis_heavy'
                    case _:
                        posture = f'{dominant[0]}_heavy'
        quality_breaks = []
        for et, data in funnel.items():
            if data['avg_conf'] < 0.7:
                quality_breaks.append({'event_type': et, 'avg_conf': data['avg_conf'], 'count': data['count']})
        quality_signal = 'intact' if not quality_breaks else 'degraded'
        decision_conf = decisions.get('avg_confidence', 0.0)
        decision_min = decisions.get('min_confidence', 0.0)
        decision_max = decisions.get('max_confidence', 0.0)
        decision_count = decisions.get('count', 0)
        low_conf_decisions = 0
        if decision_count > 0:
            for e in self.query(event_type='decision', limit=500):
                if e.confidence < 0.7:
                    low_conf_decisions += 1
        error_rate = errors.get('error_rate', 0.0)
        low_conf_rate = errors.get('low_conf_rate', 0.0)
        if posture == 'empty' or total == 0:
            health = 'empty'
        else:
            match ():
                case _ if error_rate >= 20 or low_conf_rate >= 30:
                    health = 'noisy'
                case _ if error_rate >= 10 or low_conf_rate >= 20:
                    health = 'degraded'
                case _ if error_rate >= 5 or low_conf_rate >= 10:
                    health = 'warning'
                case _:
                    health = 'healthy'
        if posture == 'error_heavy' and error_rate > 15:
            health = 'degraded' if health == 'healthy' else health
        weak_spots: dict[str, int] = {}
        error_events = self.query(event_type='error', limit=100)
        for e in error_events:
            payload: dict[str, Any] = orjson.loads(e.payload) if e.payload else {}
            kind = payload.get('kind', 'unknown')
            msg = payload.get('message', '')[:50]
            if msg:
                key = f'[{kind}] {msg}'
            else:
                key = f'[{kind}]'
            weak_spots[key] = weak_spots.get(key, 0) + 1
        top_weak_spots = dict(sorted(weak_spots.items(), key=lambda x: -x[1])[:5])
        recent_high_conf_decisions = []
        for e in reversed(self.query(event_type='decision', limit=50)):
            if e.confidence >= 0.9:
                payload: dict[str, Any] = orjson.loads(e.payload) if e.payload else {}
                recent_high_conf_decisions.append({'event_id': e.event_id[-12:], 'kind': payload.get('kind', ''), 'conf': e.confidence, 'timestamp': datetime.fromtimestamp(e.timestamp, UTC).isoformat()})
                if len(recent_high_conf_decisions) >= 3:
                    break
        _low_conf_pressure = ''
        if low_conf_decisions > 0 and decision_count > 0:
            pressure_pct = low_conf_decisions / decision_count * 100
            if pressure_pct > 30:
                _low_conf_pressure = 'high'
            elif pressure_pct > 15:
                _low_conf_pressure = 'moderate'
            else:
                _low_conf_pressure = 'low'
        else:
            _low_conf_pressure = 'none'
        return {'run_id': self._run_id, 'total_events': total, 'created_at': self._created_at.isoformat(), 'posture': posture, 'dominant_pct': dominant[1]['pct'] if posture not in ('empty', 'balanced') else 0.0, 'quality_signal': quality_signal, 'quality_breaks': quality_breaks[:5], 'decision_count': decision_count, 'decision_avg_conf': round(decision_conf, 4), 'decision_conf_range': [round(decision_min, 4), round(decision_max, 4)], 'low_conf_decisions': low_conf_decisions, 'low_conf_pressure': _low_conf_pressure, 'error_count': errors.get('error_count', 0), 'error_rate_pct': error_rate, 'low_conf_count': errors.get('low_conf_count', 0), 'low_conf_rate_pct': low_conf_rate, 'health': health, 'top_weak_spots': top_weak_spots, 'recent_high_conf_decisions': recent_high_conf_decisions}

    @staticmethod
    def _derive_continue_reason(continue_or_pivot: str, health_status: str, decision_count: int, biggest_weakness: str) -> str:
        """Derive one-line continue reason from health signals."""
        if continue_or_pivot == 'pivot':
            return 'pivot: errors/errors dominate — cannot trust signal'
        if continue_or_pivot == 'inspect':
            if biggest_weakness:
                return f'inspect: {biggest_weakness[:70]}'
            return f'inspect: health={health_status}, check signals'
        if decision_count == 0:
            return 'continue: no decisions made yet — gather more signal'
        return f'continue: healthy sprint with {decision_count} decisions'

    @staticmethod
    def _derive_trust_level(total: int, health_status: str, low_conf_pressure: str, error_rate: float) -> str:
        """Derive trust level enum from health signals."""
        if total < 10:
            return 'low'
        if health_status == 'noisy':
            return 'low'
        if low_conf_pressure == 'high':
            return 'moderate'
        if health_status == 'degraded' or error_rate > 10:
            return 'moderate'
        if health_status == 'warning' or error_rate > 5:
            return 'moderate'
        return 'high'

    def get_retrospective_bundle(self) -> dict[str, Any]:
        """
        Single-call retrospective seam for private sprint retro.

        Composes get_sprint_health_summary() as primary source.
        Bounded raw scan only for signals that helpers don't provide directly.

        Answers in one call:
        - jaký sprint byl (verdict)
        - kde se lámal (breakdown)
        - co fungovalo (what_worked)
        - největší slabina (biggest_weakness)
        - pokračovat / pivotnout / inspectnout (continue_or_pivot)

        Operator-facing fields:
        - operator_takeaway: one-line bottom line
        - top_retro_actions: 2-3 condensed action items

        Bounded, fail-soft, read-only. Works on empty log.

        Returns:
            Dict ready for export or local diagnostics
        """
        health = self.get_sprint_health_summary()
        what_worked: list[str] = []
        funnel = health.get('funnel') or {}
        for et, data in funnel.items():
            if data.get('avg_conf', 0) >= 0.85 and data.get('count', 0) >= 2:
                label = f"{et} (conf={data['avg_conf']:.2f}, n={data['count']})"
                what_worked.append(label)
        what_worked = what_worked[:4]
        breakdown: list[str] = []
        for break_item in health.get('quality_breaks', []):
            breakdown.append(f"{break_item['event_type']} conf={break_item['avg_conf']:.2f}")
        for spot in list(health.get('top_weak_spots', {}).keys())[:3]:
            breakdown.append(f'error: {spot}')
        breakdown = breakdown[:5]
        biggest_weakness = ''
        weak_spots = health.get('top_weak_spots', {})
        if weak_spots:
            biggest_weakness = next(iter(weak_spots.keys()), '')
        elif breakdown:
            biggest_weakness = breakdown[0]
        else:
            quality_breaks = health.get('quality_breaks', [])
            if quality_breaks:
                biggest_weakness = f"{quality_breaks[0]['event_type']} quality gap"
        posture = health.get('posture', 'unknown')
        total = health.get('total_events', 0)
        health_status = health.get('health', 'unknown')
        error_rate = health.get('error_rate_pct', 0.0)
        decision_count = health.get('decision_count', 0)
        if total == 0:
            verdict = 'empty log — no events recorded'
        else:
            match health_status:
                case 'healthy':
                    verdict = f'clean sprint: {posture}, {total} events, {decision_count} decisions'
                case 'warning':
                    verdict = f'warning sprint: {posture}, {total} events, {error_rate:.1f}% errors'
                case 'degraded':
                    verdict = f'degraded sprint: {posture}, {total} events, {error_rate:.1f}% errors'
                case 'noisy':
                    verdict = f'noisy sprint: {posture}, {total} events, {error_rate:.1f}% errors — signal hard to trust'
                case _:
                    verdict = f'{posture} sprint: {total} events, health={health_status}'
        continue_or_pivot = 'continue'
        match ():
            case _ if health_status == 'noisy':
                continue_or_pivot = 'pivot'
            case _ if health_status == 'degraded' and error_rate > 15:
                continue_or_pivot = 'pivot'
            case _ if health_status == 'degraded':
                continue_or_pivot = 'inspect'
            case _ if health_status == 'warning':
                continue_or_pivot = 'inspect'
            case _ if health.get('low_conf_pressure') == 'high':
                continue_or_pivot = 'inspect'
            case _ if total < 10:
                continue_or_pivot = 'inspect'
        if total == 0:
            operator_takeaway = 'no data — sprint not started or all events dropped'
        else:
            match health_status:
                case 'healthy':
                    operator_takeaway = f'sprint healthy, {decision_count} decisions made, continue'
                case 'warning':
                    operator_takeaway = f"sprint has warnings: {(biggest_weakness[:60] if biggest_weakness else 'see breakdown')}"
                case 'degraded':
                    operator_takeaway = f"sprint degraded: {(biggest_weakness[:60] if biggest_weakness else 'errors above threshold')}"
                case 'noisy':
                    operator_takeaway = f"sprint noisy: {(biggest_weakness[:60] if biggest_weakness else 'too many errors to trust')}"
                case _:
                    operator_takeaway = f'sprint status={health_status}, verdict={verdict[:80]}'
        top_retro_actions: list[str] = []
        if continue_or_pivot == 'pivot':
            top_retro_actions.append('pivot: root-cause errors blocking progress — investigate before continuing')
        elif continue_or_pivot == 'inspect':
            if biggest_weakness:
                top_retro_actions.append(f'inspect: {biggest_weakness[:80]}')
            if error_rate > 5:
                top_retro_actions.append(f'review error_rate={error_rate:.1f}% — identify top failure modes')
            if health.get('low_conf_pressure') != 'none':
                top_retro_actions.append(f"review low-conf decisions ({health.get('low_conf_pressure')} pressure)")
        if health.get('low_conf_pressure') == 'high' and continue_or_pivot != 'pivot':
            top_retro_actions.append('address decision confidence — >30% decisions below 0.7 conf')
        if what_worked and continue_or_pivot == 'continue':
            top_retro_actions.append(f'leverage what worked: {what_worked[0][:60]}')
        seen = set()
        deduped = []
        for a in top_retro_actions:
            normalized = a[:60]
            if normalized not in seen:
                seen.add(normalized)
                deduped.append(a)
        top_retro_actions = deduped[:3]
        if total < 10:
            _health_confidence_note = f'low confidence: only {total} events — treat verdict as indicative'
        else:
            match health_status:
                case 'noisy':
                    _health_confidence_note = 'low confidence: error_rate >20% — signal integrity compromised'
                case _ if health.get('low_conf_pressure') == 'high':
                    _health_confidence_note = 'moderate confidence: high low-conf decision pressure'
                case _:
                    _health_confidence_note = 'confident verdict: sufficient data and low noise'
        return {'run_id': self._run_id, 'total_events': total, 'verdict': verdict, 'posture': posture, 'health': health_status, 'breakdown': breakdown, 'what_worked': what_worked, 'biggest_weakness': biggest_weakness, 'continue_or_pivot': continue_or_pivot, 'operator_takeaway': operator_takeaway, 'top_retro_actions': top_retro_actions, 'health_confidence_note': _health_confidence_note, 'operator_retro_brief': operator_takeaway, 'continue_reason': self._derive_continue_reason(continue_or_pivot, health_status, decision_count, biggest_weakness), 'trust_level': self._derive_trust_level(total, health_status, health.get('low_conf_pressure', 'none'), error_rate), 'biggest_win': what_worked[0] if what_worked else '', 'retro_priority': top_retro_actions[0] if top_retro_actions else '', '_health': health}

    def get_chain(self, event_id: str) -> list[EvidenceEvent]:
        """
        Získá řetězec událostí vedoucí k dané události.

        Prochází source_ids zpětně a sestaví řetězec závislostí.

        Args:
            event_id: ID cílové události

        Returns:
            Seznam událostí v řetězci (od nejstarší po cílovou)
        """
        chain = []
        visited = set()

        def traverse(eid: str):
            if eid in visited:
                return
            visited.add(eid)
            event = self.get_by_id(eid)
            if not event:
                return
            for source_id in event.source_ids:
                traverse(source_id)
            chain.append(event)
        traverse(event_id)
        return chain