"""
EvidenceLog — CANONICAL EVIDENCE LEDGER
=======================================

ROLE: Append-only evidence ledger for the autonomous research system.
This module implements the EVIDENCE LEDGER boundary — it records what
happened during research but does NOT govern sprint truth or own facts.

FACTS / LEDGER / DERIVED MAP:
-----------------------------
TIER 1 — EVIDENCE LEDGER (EvidenceLog):
    append-only events: tool_call, observation, synthesis, error, decision, evidence_packet
    Hash-chained events with tamper detection
    Ring buffer in RAM (max 100 events) + SQLite/JSONL persistence

TIER 2 — SPRINT FACTS (DuckDBShadowStore):
    sprint_delta, sprint_scorecard, source_hit_log (canonical sprint metrics)
    shadow_findings, shadow_runs (finding-level forwarded from EvidenceLog)

TIER 3 — GRAPH/STORE (injected):
    IOCGraph (Kuzu), SemanticStore (LanceDB), DuckPGQGraph (analytics donor)

LEDGER → FACTS boundary (Sprint F11C):
    ResearchContext (carrier) --handoff metadata--> EvidenceLog (ledger writer)
    EvidenceLog.append() --analytics_hook--> DuckDBShadowStore (sprint facts)

The handoff flows through:
  1. ResearchContext.context_metadata carries ContextHandoffMetadata descriptor
  2. EvidenceLog.create_event(correlation=) receives RunCorrelation dict
  3. Shadow analytics_hook receives correlation via payload["_correlation"]

LEDGER BOUNDARY RULES:
    [1] EvidenceLog remains ledger WRITER — no orchestrator authority
    [2] ResearchContext remains context CARRIER — no writer authority
    [3] Correlation is the ONLY cross-boundary handoff mechanism
    [4] context_metadata is carrier-internal (EvidenceLog never reads it directly)
    [5] No new session manager or persistence redesign

⚠️  This module does NOT own sprint facts or derived views.
    It is the EVIDENCE LEDGER — the immutable record of what happened.

M1 8GB Optimalizace:
- Ring buffer v RAM (max 100 událostí)
- Append-only JSONL persistencer na disk
- Trimmované payloady (žádné fulltexty)
- Automatická rotace logů
"""
from __future__ import annotations



import asyncio
import hashlib
import logging
import os
import secrets
import threading
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import aiosqlite
import msgspec
import orjson

from core.env_config import ENV  # noqa: E402

# Arrow IPC — lazy import (M1 8GB: only load if pyarrow available)
_arrow = None


def _get_arrow():
    """Lazy Arrow IPC loader — only loads pyarrow if HLEDAC_ARROW_EVIDENCE=1."""
    global _arrow
    if _arrow is None:
        import os as _os
        if _os.environ.get("HLEDAC_ARROW_EVIDENCE", "0") == "1":
            try:
                import pyarrow as _pa
                import pyarrow.ipc as _ipc
                _arrow = (_pa, _ipc)
            except ImportError:
                logger.debug("[Arrow] pyarrow not available, falling back to SQLite")
                _arrow = False
        else:
            _arrow = False
    return _arrow if _arrow else None

# =============================================================================
# CONTEXT/EVIDENCE HANDOFF — Sprint F11C: Canonical Ledger Seams
# =============================================================================
# This module implements the EVIDENCE LEDGER boundary for the F11C sprint.
#
# HANDOFF CONTRACT:
#   ResearchContext (carrier) --handoff metadata--> EvidenceLog (ledger writer)
#
# The handoff flows through:
#   1. ResearchContext.context_metadata carries ContextHandoffMetadata descriptor
#   2. EvidenceLog.create_event(correlation=) receives RunCorrelation dict
#   3. Shadow analytics_hook receives correlation via payload["_correlation"]
#
# BOUNDARY RULES:
#   [1] EvidenceLog remains ledger WRITER — no orchestrator authority
#   [2] ResearchContext remains context CARRIER — no writer authority
#   [3] Correlation is the ONLY cross-boundary handoff mechanism
#   [4] context_metadata is carrier-internal (EvidenceLog never reads it directly)
#   [5] No new session manager or persistence redesign
#
# RELATED COMPONENTS:
#   - ResearchContext: canonical context carrier (research_context.py)
#   - RunCorrelation: canonical correlation carrier (types.py:1310-1356)
#   - ContextHandoffMetadata: typed handoff descriptor (research_context.py)
#   - analytics_hook: shadow consumer of correlation (knowledge/analytics_hook.py)
# =============================================================================

# Sprint 8C1: Flow trace
try:
    from utils.flow_trace import (
        is_enabled,
        trace_counter,
        trace_evidence_append,
        trace_evidence_flush,
        trace_queue_drop,
    )
except ImportError:
    # Fallback if flow_trace not available
    def trace_evidence_append(*_, **_kw): pass
    def trace_evidence_flush(*_, **_kw): pass
    def trace_queue_drop(*_, **_kw): pass
    def trace_counter(*_, **_kw): pass
    def is_enabled(): return False

logger = logging.getLogger(__name__)


class EvidenceEvent(msgspec.Struct, frozen=False, gc=False):
    """
    Událost v evidence logu — msgspec.Struct pro 10× rychlejší (de)serializaci.

    Každá událost má unikátní ID, typ, timestamp, payload
    a content hash pro verifikaci integrity.
    """
    event_id: str
    event_type: str  # Literal["tool_call", "observation", "synthesis", "error", "decision", "evidence_packet"]
    timestamp: float  # epoch seconds (datetime stored as float for msgspec compat)
    payload: bytes  # pre-encoded JSON for zero-copy
    source_ids: list[str]
    confidence: float
    content_hash: str
    run_id: str
    # Tamper-evident hash-chain fields (optional for backward compatibility with legacy JSONL)
    seq_no: int = 0
    prev_chain_hash: str | None = None
    chain_hash: str | None = None

    @classmethod
    def create(
        cls,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        run_id: str,
        source_ids: list[str] | None = None,
        confidence: float = 1.0,
        seq_no: int = 0,
        prev_chain_hash: str | None = None,
    ) -> EvidenceEvent:
        """Factory method — creates event with auto-generated content_hash."""
        source_ids = source_ids or []
        timestamp = datetime.now(UTC).timestamp()
        # Pre-encode payload as bytes for zero-copy
        encoded_payload = orjson.dumps(payload)
        # Calculate content hash from normalized representation
        content_hash = cls._calculate_hash(
            event_id=event_id,
            event_type=event_type,
            timestamp=timestamp,
            payload=payload,
            source_ids=source_ids,
            confidence=confidence,
            run_id=run_id,
        )
        return cls(
            event_id=event_id,
            event_type=event_type,
            timestamp=timestamp,
            payload=encoded_payload,
            source_ids=source_ids,
            confidence=confidence,
            content_hash=content_hash,
            run_id=run_id,
            seq_no=seq_no,
            prev_chain_hash=prev_chain_hash,
            chain_hash=None,
        )

    @staticmethod
    def _calculate_hash(
        event_id: str,
        event_type: str,
        timestamp: float,
        payload: dict[str, Any],
        source_ids: list[str],
        confidence: float,
        run_id: str,
    ) -> str:
        """Calculate SHA-256 hash of normalized event content."""
        data = {
            "event_id": event_id,
            "event_type": event_type,
            "timestamp": timestamp,
            "payload": _normalize_payload(payload),
            "source_ids": sorted(source_ids),
            "confidence": round(confidence, 6),
            "run_id": run_id,
        }
        json_bytes = orjson.dumps(data, option=orjson.OPT_SORT_KEYS)
        return hashlib.sha256(json_bytes).hexdigest()

    def calculate_hash(self) -> str:
        """Calculate current event's content hash."""
        return self._calculate_hash(
            event_id=self.event_id,
            event_type=self.event_type,
            timestamp=self.timestamp,
            payload=self.payload_dict,
            source_ids=self.source_ids,
            confidence=self.confidence,
            run_id=self.run_id,
        )

    @property
    def payload_dict(self) -> dict[str, Any]:
        """Decode payload bytes to dict (lazy decode)."""
        return orjson.loads(self.payload)

    def verify_integrity(self) -> bool:
        """Verify event integrity using content hash."""
        return self.calculate_hash() == self.content_hash

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary (for backward compatibility)."""
        result = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": datetime.fromtimestamp(self.timestamp, UTC).isoformat(),
            "payload": orjson.loads(self.payload),  # decode bytes back to dict
            "source_ids": self.source_ids,
            "confidence": self.confidence,
            "content_hash": self.content_hash,
            "run_id": self.run_id,
        }
        if self.seq_no > 0:
            result["seq_no"] = self.seq_no
        if self.prev_chain_hash:
            result["prev_chain_hash"] = self.prev_chain_hash
        if self.chain_hash:
            result["chain_hash"] = self.chain_hash
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceEvent:
        """Create event from dictionary."""
        # Parse timestamp
        ts = data["timestamp"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts).timestamp()
        # Encode payload as bytes
        encoded_payload = orjson.dumps(data["payload"])
        # Ensure source_ids
        source_ids = data.get("source_ids") or []
        return cls(
            event_id=data["event_id"],
            event_type=data["event_type"],
            timestamp=ts,
            payload=encoded_payload,
            source_ids=source_ids,
            confidence=data.get("confidence", 1.0),
            content_hash=data["content_hash"],
            run_id=data["run_id"],
            seq_no=data.get("seq_no", 0),
            prev_chain_hash=data.get("prev_chain_hash"),
            chain_hash=data.get("chain_hash"),
        )

    def to_jsonl_line(self) -> str:
        """Convert event to JSONL line."""
        return orjson.dumps(self.to_dict()).decode() + "\n"


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize payload for consistent hashing."""
    normalized = {}
    for key in sorted(payload.keys()):
        value = payload[key]
        if isinstance(value, datetime):
            normalized[key] = value.isoformat()
        elif isinstance(value, (list, tuple)):
            normalized[key] = [_normalize_value(v) for v in value]
        elif isinstance(value, dict):
            normalized[key] = _normalize_payload(value)
        else:
            normalized[key] = _normalize_value(value)
    return normalized


def _normalize_value(value: Any) -> Any:
    """Normalize individual value."""
    if isinstance(value, float):
        return round(value, 6)
    elif isinstance(value, (set, frozenset)):
        return sorted(value)
    elif isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return value


def _put_to_queue(q: asyncio.Queue, item: dict[str, Any]) -> None:
    """Thread-safe queue put helper for F320-ASYNCIO.

    Used by call_soon_threadsafe() to schedule queue puts from append()
    without blocking the event loop. The queue is created by asyncio and
    lives in the event loop thread, so threadsafe put is correct.
    """
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        logger.warning("SQLite queue full in _put_to_queue")


# ---------------------------------------------------------------------------
# _RustMPSC — Bounded MPSC pool via crossbeam-channel (Rust)
# ---------------------------------------------------------------------------
# Replaces asyncio.Queue in evidence_log for IOC stream batching.
# - crossbeam-channel ~2-5ns send (no GIL, ARM LSE atomic)
# - pipe-based async wake-up for Python's event loop
# - graceful fallback to asyncio.Queue if Rust unavailable
# ---------------------------------------------------------------------------

_RUST_MPSC: type | None = None  # lazily loaded MPSCPool wrapper


def _load_rust_mpsc() -> type | None:
    """Lazily import and initialize Rust MPSCPool."""
    global _RUST_MPSC
    if _RUST_MPSC is not None:
        return _RUST_MPSC
    try:
        from hledac_rust_extensions import MPSCPool
        pool = MPSCPool(capacity=2048)
        sender_ptr = pool.add_sender()
        wake_fd = pool.wake_fd()
        _RUST_MPSC = (MPSCPool, pool, sender_ptr, wake_fd)
        return _RUST_MPSC
    except Exception:
        return None


class _RustMPSC:
    """Python wrapper for Rust MPSCPool with asyncio integration.

    Attrs:
        pool: MPSCPool instance
        sender_ptr: opaque usize handle for send()
        wake_fd: pipe read fd for asyncio.AddedReader
        fallback: True if Rust MPSCPool unavailable (uses asyncio.Queue)
    """

    def __init__(self, capacity: int = 2048) -> None:
        self._pool = None
        self._sender_ptr = 0
        self._wake_fd = -1
        self.fallback = True
        self._impl = None  # 'rust' or 'asyncio'
        self._impl = self._init_rust(capacity)

    def _init_rust(self, capacity: int) -> str:
        try:
            from hledac_rust_extensions import MPSCPool as _MPSC
        except Exception:
            return "asyncio"

        try:
            pool = _MPSC(capacity=capacity)
            sender_ptr = pool.add_sender()
            wake_fd = pool.wake_fd()
            self._pool = pool
            self._sender_ptr = sender_ptr
            self._wake_fd = wake_fd
            self.fallback = False
            return "rust"
        except Exception:
            return "asyncio"

    def send(self, item: dict[str, Any]) -> bool:
        """Send an item (msgspec-serialized bytes) to the pool."""
        if self._impl == "rust" and self._pool is not None:
            try:
                payload = orjson.dumps(item)
                return self._pool.send(self._sender_ptr, payload)
            except Exception:
                return False
        # Fallback: return False to signal caller should use asyncio path
        return False

    def recv_batch(self, max_items: int | None = None) -> list[dict[str, Any]]:
        """Drain up to max_items from the pool (non-blocking)."""
        if self._impl == "rust" and self._pool is not None:
            try:
                batch_bytes = self._pool.recv_batch(max_items)
                return [orjson.loads(item) for item in batch_bytes]
            except Exception:
                return []
        return []

    def wake_fd(self) -> int:
        """Pipe read fd for asyncio reader registration."""
        return self._wake_fd

    def len(self) -> int:
        """Current queue depth."""
        if self._pool is not None:
            return self._pool.len()
        return 0

    def is_empty(self) -> bool:
        if self._pool is not None:
            return self._pool.is_empty()
        return True


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

    # M1 8GB RAM: __slots__ reduces ~200 bytes/instance (no __dict__ overhead)
    __slots__ = (
        "_run_id", "_log", "_index_by_type", "_index_by_source",
        "_created_at", "_frozen", "_closed", "_total_count", "_dropped_count",
        "_seq", "_chain_head", "_genesis_hash",
        "_encrypt_at_rest", "_encryption_key", "_cipher",
        "_enable_persist", "_persist_path", "_persist_file", "_persist_path_str",
        "_queue", "_flush_task",
        "_async_write_queue", "_async_write_task",
        "_db_path", "_db", "_initialized",
        "_arrow_path", "_arrow_writer", "_arrow_schema",
        "_closing", "_manifest_dirty",
        "_flush_shutdown", "_async_write_shutdown",
        "_loop", "_silent_failure",
        "_sample_rate",
    )

    # M1 8GB RAM hard limity
    MAX_RAM_EVENTS = 50  # Ring buffer size (P3.3: reduced 100→50 for lower memory footprint)
    MAX_PAYLOAD_PREVIEW = 200  # Max chars v payload preview
    JSONL_ROTATE_SIZE = 10 * 1024 * 1024  # 10MB rotace

    # Internal constant for fsync batching (no user toggle)
    # fsync every N events to avoid per-event IO bottleneck
    _FSYNC_EVERY_N_EVENTS = 25
    _MANIFEST_EVERY_N_EVENTS = 100  # Write manifest every N events (optimized: 50→100)
    # Sprint F265X: Increased batch size for higher throughput.
    # Larger batches amortize SQLite IO overhead — target is +10-15% throughput.
    # M1 8GB: 500 events ≈ ~500KB RAM worst-case (Event objects are small).
    _SQLITE_BATCH_SIZE = 500  # was 200 — 2.5x batch reduces IO calls by ~60%
    _SQLITE_FLUSH_INTERVAL = 1.5  # was 1.0 — slightly longer interval lets batches accumulate

    # F290-ASYNCIO: aiofiles write queue for non-blocking JSONL persistence
    # Bounded: max 500 pending writes to prevent memory bloat
    _ASYNC_WRITE_QUEUE_MAXSIZE = 500

    def __init__(
        self,
        run_id: str,
        persist_path: Path | None = None,
        enable_persist: bool = True,
        encrypt_at_rest: bool = False,
        silent_failure: bool = False,
        sample_rate: float = 1.0,  # Phase4: 0.10 = 10% sampling for non-error events
    ):
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
        """
        import os

        self._run_id: str = run_id
        self._silent_failure: bool = silent_failure
        # Phase4: ENV override for sample_rate (default 0.10 = 10%)
        self._sample_rate: float = ENV.get_float("HLEDAC_EVIDENCE_SAMPLE_RATE", default=sample_rate)
        self._log: deque = deque(maxlen=self.MAX_RAM_EVENTS)  # Ring buffer (max MAX_RAM_EVENTS)
        # Bounded indexes (F-MEMFIX): use deque with maxlen=MAX_RAM_EVENTS so
        # indices never grow beyond ring-buffer size even across overflow rebuilds.
        self._index_by_type: dict[str, deque[int]] = {
            "tool_call": deque(maxlen=self.MAX_RAM_EVENTS),
            "observation": deque(maxlen=self.MAX_RAM_EVENTS),
            "synthesis": deque(maxlen=self.MAX_RAM_EVENTS),
            "error": deque(maxlen=self.MAX_RAM_EVENTS),
            "decision": deque(maxlen=self.MAX_RAM_EVENTS),
            "evidence_packet": deque(maxlen=self.MAX_RAM_EVENTS),
        }
        self._index_by_source: dict[str, deque[int]] = {}
        self._created_at: datetime = datetime.now(UTC)
        self._frozen: bool = False
        self._closed: bool = False  # H1: closed flag for post-close guards
        self._total_count: int = 0  # Celkový počet událostí (včetně na disku)
        self._dropped_count: int = 0  # Počet vyřazených z ring bufferu
        # F290-ASYNCIO: fsync batching moved to async worker (local counter, not instance var)

        # Hash-chain state for tamper detection
        self._seq: int = 0  # Sequence counter
        self._chain_head: str = ""  # Current chain head hash
        self._genesis_hash: str = hashlib.sha256(f"GENESIS:{run_id}".encode()).hexdigest()  # Genesis hash
        self._chain_head = self._genesis_hash  # Initialize chain head

        # Encryption setup
        self._encrypt_at_rest = encrypt_at_rest or os.environ.get('ENCRYPT_AT_REST', '0') == '1'
        self._encryption_key = os.environ.get('ENCRYPTION_KEY', '').encode() if self._encrypt_at_rest else None

        if self._encrypt_at_rest:
            logger.info("[ENCRYPT] enabled=True target=evidence")
            self._init_encryption()
        else:
            self._cipher = None

        # Persistencer setup
        self._enable_persist: bool = enable_persist
        self._persist_path: Path | None = None
        self._persist_file = None
        self._persist_path_str: str | None = None  # F290-ASYNCIO: string path for aiofiles

        if enable_persist:
            if persist_path is None:
                # Auto path: EVIDENCE_ROOT/{run_id}.jsonl
                from hledac.universal.paths import EVIDENCE_ROOT
                evidence_dir = EVIDENCE_ROOT
                evidence_dir.mkdir(parents=True, exist_ok=True)
                # Change extension for encrypted files
                ext = '.enc' if self._encrypt_at_rest else '.jsonl'
                self._persist_path = evidence_dir / f"{run_id}{ext}"
            else:
                self._persist_path = Path(persist_path)
                self._persist_path.parent.mkdir(parents=True, exist_ok=True)

            # Otevři append-only file
            try:
                self._persist_file = open(  # noqa: SIM115
                    self._persist_path, 'ab' if self._encrypt_at_rest else 'a',
                    encoding='utf-8' if not self._encrypt_at_rest else None,
                    buffering=8192
                )
                self._persist_path_str = str(self._persist_path)  # F290-ASYNCIO: store for aiofiles
                logger.debug(f"EvidenceLog persistence: {self._persist_path}")
            except Exception as e:
                logger.error(f"Failed to open evidence log: {e}")
                self._enable_persist = False

        # SQLite async batching components — ALWAYS initialized (even with silent_failure)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._flush_task: asyncio.Task | None = None
        # F290-ASYNCIO: async write queue for non-blocking JSONL persistence
        self._async_write_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=self._ASYNC_WRITE_QUEUE_MAXSIZE)
        self._async_write_task: asyncio.Task | None = None
        self._db_path: Path | None = None
        self._db: aiosqlite.Connection | None = None
        self._initialized = False
        # Arrow IPC state (lazy, HLEDAC_ARROW_EVIDENCE=1)
        self._arrow_path: Path | None = None
        self._arrow_writer: Any = None
        self._arrow_schema: Any = None
        self._closing = False  # Flag: aclose in progress, block queue access
        self._manifest_dirty: bool = False  # Flag: manifest needs update on next batch
        # F285: asyncio.Event for clean flush-worker shutdown — avoids race between
        # cancel() and _db close. The worker waits on this event instead of relying
        # on CancelledError, guaranteeing the worker exits BEFORE aclose() closes _db.
        self._flush_shutdown: asyncio.Event = asyncio.Event()
        # ISSUE-2 FIX: Async write worker shutdown event — mirrors _flush_shutdown
        # pattern but for _async_write_worker which writes JSONL asynchronously.
        self._async_write_shutdown: asyncio.Event = asyncio.Event()
        # F285-RACE: Lock removed in F314-4 — _flush_worker is the sole writer,
        # single-threaded with no concurrent access to _db. aclose() signals
        # shutdown event, never calls _flush_batch concurrently.
        # ISSUE-2 FIX: Store event loop reference at initialization time.
        # Both _flush_worker and _async_write_worker are created in the SAME
        # call chain (initialize()) so they inherit the same running loop.
        # We store it here so close() can detect which loop to use without
        # calling get_running_loop() from a worker thread.
        # NOTE: Stored in initialize() (not __init__) because __init__ is sync
        # and get_running_loop() would always fail and set _loop=None.
        # initialize() is async so get_running_loop() works there.
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # F285-RESOURCE: Synchronous cleanup — called from __del__ and aclose
    # path. Only closes synchronous resources. Async resources (_db,
    # _flush_task via Event) must be closed by aclose().
    # ------------------------------------------------------------------
    def _sync_close(self) -> None:
        """Synchronous cleanup: cancel flush task, close Arrow writer, sync persist."""
        # Issue 8.3: guard against __del__ during __init__ if silent_failure=True
        # with enable_persist=False skips async component initialization
        if not hasattr(self, "_flush_task"):
            return
        # Cancel flush task (sync path — don't wait, just cancel)
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            self._flush_task = None

        # F290-ASYNCIO: cancel async write task
        if self._async_write_task is not None and not self._async_write_task.done():
            self._async_write_task.cancel()
            self._async_write_task = None

        # Arrow IPC: close writer (sync close() on the file object)
        if self._arrow_writer is not None:
            try:
                self._arrow_writer.close()
            except Exception:  # noqa: BLE001
                pass
            self._arrow_writer = None

        # Persist file (already in __del__, kept here for _sync_close parity)
        if self._persist_file and not self._persist_file.closed:
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

    # ------------------------------------------------------------------
    # Async context manager — enables `async with EvidenceLog(...) as elog:`
    # ------------------------------------------------------------------
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
        # ISSUE-2 FIX: Store running event loop so close() can call aclose()
        # on the correct loop. This is the ONLY place where get_running_loop()
        # is safe to call — initialize() is always invoked within an async context.
        # Storing in __init__ would always yield None (sync context).
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        # F285-FIX: Cancel any existing worker before creating new one.
        # Without this, two workers can run simultaneously causing
        # "database is locked" errors and race conditions on _flush_shutdown.
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await asyncio.wait_for(self._flush_task, timeout=1.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            self._flush_task = None

        # ISSUE-2 FIX: Also cancel/reinit _async_write_task on re-init
        if self._async_write_task is not None and not self._async_write_task.done():
            self._async_write_task.cancel()
            try:
                await asyncio.wait_for(self._async_write_task, timeout=1.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            self._async_write_task = None

        if self._initialized:
            # ISSUE-5 FIX: re-initialize must restart workers if they were cancelled.
            # Previously returned early without restarting dead workers, causing silent
            # data loss on subsequent sprints with the same EvidenceLog instance.
            # ISSUE-4: _flush_task.done() means cancelled/crashed — restart needed.
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._flush_worker())
            if self._async_write_task is None or self._async_write_task.done():
                self._async_write_task = asyncio.create_task(self._async_write_worker())
            # Clear shutdown events for new session
            self._flush_shutdown.clear()
            self._async_write_shutdown.clear()
            return

        # F285-FIX: Reuse existing _flush_shutdown Event instead of creating new one.
        # Creating a new Event orphaned the old worker's wait on the old Event,
        # causing a hang when aclose() set the new Event but the old worker
        # was waiting on the (now-garbage) old Event instance.
        # F11C-FIX: _init_db() must succeed for EvidenceLog to be functional.
        # _migrate_from_file() is best-effort — if it fails, evidence still goes to
        # existing JSONL and new events go to DB/SQLite normally.
        await self._init_db()
        try:
            await self._migrate_from_file()
        except Exception as _mig_err:
            logger.warning(f"[F11C] Migration from JSONL failed (non-fatal): {_mig_err}")
        # F11C-FIX: Flush worker start must also be wrapped — if create_task fails,
        # we still have sync SQLite fallback in append() and JSONL persistence.
        try:
            self._flush_task = asyncio.create_task(self._flush_worker())
        except Exception as _task_err:
            logger.warning(f"[F11C] Flush worker task creation failed (non-fatal): {_task_err}")
            self._flush_task = None
        # F290-ASYNCIO: Start async write worker for non-blocking JSONL persistence
        try:
            self._async_write_task = asyncio.create_task(self._async_write_worker())
        except Exception as _write_task_err:
            logger.warning(f"[F290] Async write worker task creation failed (non-fatal): {_write_task_err}")
            self._async_write_task = None
        self._initialized = True

    async def _init_db(self) -> None:
        """Initialize SQLite database with WAL mode."""
        if self._db_path is None:
            from hledac.universal.paths import EVIDENCE_ROOT
            evidence_dir = EVIDENCE_ROOT
            evidence_dir.mkdir(parents=True, exist_ok=True)
            self._db_path = evidence_dir / f"{self._run_id}.db"

        self._db = await aiosqlite.connect(str(self._db_path), check_same_thread=False)
        await self._db.execute("PRAGMA busy_timeout=30000")  # 30s — prevent "database table locked" during concurrent access
        await self._db.execute("PRAGMA journal_mode=WAL")
        # WAL optimizations for M1 8GB + Python 3.14 async I/O
        # synchronous=NORMAL: WAL-safe (~3-5× faster writes vs FULL, fsync at checkpoints only)
        await self._db.execute("PRAGMA synchronous=NORMAL")
        # wal_autocheckpoint=1000: checkpoint every 1000 WAL pages (~1MB), keeps WAL small
        await self._db.execute("PRAGMA wal_autocheckpoint=1000")
        # cache_size=-8192: 8MB page cache (negative = KB), M1 8GB friendly
        await self._db.execute("PRAGMA cache_size=-8192")
        # read_uncommitted=1: dirty reads for analytics, no blocking on writers
        await self._db.execute("PRAGMA read_uncommitted=1")
        # F285-FIX: integrity_check on startup — detect corrupt WAL pages
        # before any transaction. QUICK is NOT a valid SQLite integrity_check argument.
        # SQLite only accepts integer N (page count) for integrity_check(N).
        # Wrap in try/except — fails gracefully on older SQLite/aiosqlite.
        try:
            await self._db.execute("PRAGMA integrity_check")
        except Exception:  # noqa: BLE001
            pass  # pragma not supported or syntax error — skip

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                event_type TEXT NOT NULL,
                data TEXT NOT NULL,
                hash TEXT NOT NULL
            )
        """)
        await self._db.commit()

        # Arrow IPC init (lazy, HLEDAC_ARROW_EVIDENCE=1 enables zero-copy path)
        arrow_loader = _get_arrow()
        if arrow_loader:
            pa, ipc = arrow_loader
            from hledac.universal.paths import EVIDENCE_ROOT
            evidence_dir = EVIDENCE_ROOT
            self._arrow_path = evidence_dir / f"{self._run_id}.arrow"
            self._arrow_schema = pa.schema([
                ("timestamp", pa.float64()),
                ("event_type", pa.string()),
                ("data", pa.string()),
                ("hash", pa.string()),
            ])
            self._arrow_writer = ipc.new_file(str(self._arrow_path), self._arrow_schema)
            logger.info(f"[Arrow] IPC enabled: {self._arrow_path}")

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

        # Check if already migrated
        if migrated_file.exists():
            return

        if self._db is None:
            return

        try:
            # F285-FIX: Pre-write migration marker so crash after commit
            # but before rename → next start skips (marker exists).
            migrated_file.touch(exist_ok=True)

            # Use transaction for atomic bulk insert
            await self._db.execute("BEGIN TRANSACTION")
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

                        await self._db.execute(
                            "INSERT INTO events (timestamp, event_type, data, hash) VALUES (?, ?, ?, ?)",
                            (timestamp, event_type, event_data, content_hash)
                        )
                await self._db.commit()
            except Exception:
                await self._db.rollback()
                # Remove marker so next start retries
                if migrated_file.exists():
                    migrated_file.unlink()
                raise

            # F285-FIX: Keep .migrated marker AFTER rename — it proves migration
            # was successful. Next sprint start skips re-migration entirely.
            # The .migrated file serves as a persistent "this JSONL is done" flag.
            old_file.rename(migrated_file)
            logger.info(f"Migrated {self._run_id} events to SQLite")
        except Exception as e:
            logger.warning(f"Migration failed: {e}")

    async def _flush_worker(self) -> None:
        """Background worker that flushes events in batches."""
        batch = []
        last_flush = datetime.now(UTC)  # noqa: DTZ005

        while True:
            try:
                # F285: Wait on shutdown event INSTEAD of relying on CancelledError.
                # This guarantees the worker exits at a well-defined point AFTER aclose()
                # has drained the queue and BEFORE aclose() closes _db.
                # The 1s timeout lets us flush on interval even while shutting down.
                try:
                    async with asyncio.timeout(1.0):
                        event = await self._queue.get()
                    if event is None:  # Shutdown signal (enqueued by aclose drain)
                        break
                    batch.append(event)
                except TimeoutError:
                    pass

                # Check shutdown BEFORE flushing — aclose signals shutdown after draining
                # so the worker flushes its final batch then exits cleanly.
                if self._flush_shutdown.is_set():
                    # Drain any remaining items queued after shutdown signal
                    while True:
                        try:
                            event = self._queue.get_nowait()
                            if event is None:
                                break
                            batch.append(event)
                        except asyncio.QueueEmpty:
                            break
                    break

                # Flush if batch full or timeout reached
                if len(batch) >= self._SQLITE_BATCH_SIZE or \
                   (batch and (datetime.now(UTC) - last_flush).total_seconds() >= self._SQLITE_FLUSH_INTERVAL):  # noqa: DTZ005
                    flush_start = time.perf_counter()
                    try:
                        await self._flush_batch(batch)
                        flush_latency_ms = (time.perf_counter() - flush_start) * 1000
                        trace_evidence_flush(len(batch), flush_latency_ms, "ok", len(batch))
                    except Exception as _flush_err:
                        flush_latency_ms = (time.perf_counter() - flush_start) * 1000
                        logger.warning(f"Flush batch failed (dropping {len(batch)} events): {_flush_err}")
                        trace_evidence_flush(len(batch), flush_latency_ms, "flush_error", 0)
                    # ISSUE-3 FIX: always clear batch after flush attempt, regardless of outcome.
                    # Previously batch was only cleared on success — on _flush_batch failure the
                    # batch accumulated indefinitely causing memory growth and duplicate flushes.
                    batch = []
                    last_flush = datetime.now(UTC)  # noqa: DTZ005

            except asyncio.CancelledError:
                # ISSUE-2 FIX: drain batch before exit. CancelledError means the
                # task was cancelled externally (e.g. aclose timeout). Drain the
                # current batch so no evidence is lost, then exit cleanly.
                if batch and self._db is not None:
                    flush_start = time.perf_counter()
                    await self._flush_batch(batch)
                    flush_latency_ms = (time.perf_counter() - flush_start) * 1000
                    trace_evidence_flush(len(batch), flush_latency_ms, "cancelled_drain", len(batch))
                break
            except Exception as e:
                logger.warning(f"Flush worker error: {e}")
                trace_evidence_flush(0, 0.0, "error", None)

        # Final flush — only if _db is still open (aclose hasn't closed it yet)
        if batch and self._db is not None:
            flush_start = time.perf_counter()
            await self._flush_batch(batch)
            flush_latency_ms = (time.perf_counter() - flush_start) * 1000
            trace_evidence_flush(len(batch), flush_latency_ms, "ok", len(batch))

    # F290-ASYNCIO: Non-blocking JSONL write worker
    # Uses aiofiles for async I/O instead of blocking sync write+fsync in append()
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

    async def _async_write_worker(self) -> None:
        """Background worker that writes JSONL entries asynchronously using aiofiles.

        F290-ASYNCIO invariants:
          - Bounded: max 500 pending writes (queue maxsize)
          - Fail-safe: sync fallback via asyncio.to_thread if aiofiles unavailable
          - fsync every _FSYNC_EVERY_N_EVENTS for durability
          - M1 8GB safe: non-blocking, never blocks the event loop
        """
        import aiofiles as _f290_aiofiles

        _afile: object | None = None
        try:
            # Open file asynchronously (always binary: data is always bytes)
            _afile = await _f290_aiofiles.open(self._persist_path_str, "ab", buffering=8192)
        except Exception as _open_err:
            logger.warning(f"[F290] aiofiles open failed, using sync fallback: {_open_err}")
            # Fallback: use asyncio.to_thread for sync writes
            _afile = None

        fsync_counter = 0
        while True:
            # ISSUE-2 FIX: Wait on shutdown event OR queue item — mirrors _flush_worker pattern.
            # This ensures the worker exits cleanly when aclose() sets _async_write_shutdown.
            shutdown_signaled = False
            try:
                # Check shutdown event first (non-blocking)
                if self._async_write_shutdown.is_set():
                    shutdown_signaled = True
                else:
                    # Wait for data with timeout
                    try:
                        async with asyncio.timeout(1.0):
                            data = await self._async_write_queue.get()
                        if data is None:  # Shutdown signal — drain queue BEFORE break
                            # ISSUE-12 FIX: drain remaining items before exit.
                            # Previously broke immediately, losing events queued after shutdown.
                            while True:
                                try:
                                    drain_item = self._async_write_queue.get_nowait()
                                    if drain_item is None:
                                        break
                                    if _afile is not None:
                                        try:
                                            await _afile.write(drain_item)
                                            await _afile.flush()
                                        except Exception:  # noqa: BLE001
                                            pass
                                    else:
                                        # ISSUE-2 FIX: Use sync I/O directly in drain path.
                                        # asyncio.to_thread() calls get_running_loop() internally,
                                        # which raises "This event loop is already running" when
                                        # close() is called from a worker thread via
                                        # run_until_complete(). Since this is the drain/shutdown
                                        # path, blocking I/O is acceptable — we write directly.
                                        try:
                                            _path_str = cast(str, self._persist_path_str)
                                            with open(_path_str, "ab") as _sf:
                                                _sf.write(drain_item)
                                                _sf.flush()
                                        except Exception:  # noqa: BLE001
                                            pass
                                except asyncio.QueueEmpty:
                                    break
                                except Exception:
                                    break
                            break
                        # ISSUE-2 FIX: type narrowing — data is bytes here (not None).
                        # Type checker doesn't follow the break above; help it.
                        assert data is not None, "data must be bytes at this point"
                    except TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

                if shutdown_signaled:
                    # Drain remaining queue items before shutdown
                    # ISSUE-2 FIX: drain BEFORE break, not after. Items in queue at
                    # shutdown signal time must be written — they were already enqueued
                    # from append() and represent evidence that must not be lost.
                    while True:
                        try:
                            drain_item = self._async_write_queue.get_nowait()
                            if drain_item is None:
                                break
                            if _afile is not None:
                                try:
                                    await _afile.write(drain_item)
                                    await _afile.flush()
                                except Exception:  # noqa: BLE001
                                    pass
                            else:
                                # ISSUE-2 FIX: Use sync I/O directly in drain path.
                                try:
                                    _path_str = cast(str, self._persist_path_str)
                                    with open(_path_str, "ab") as _sf:
                                        _sf.write(drain_item)
                                        _sf.flush()
                                except Exception:  # noqa: BLE001
                                    pass
                        except asyncio.QueueEmpty:
                            break
                        except Exception:
                            break
                    break

                # Write data (data is always bytes)
                if _afile is not None:
                    try:
                        await _afile.write(data)
                        await _afile.flush()
                    except Exception as _write_err:  # noqa: BLE001
                        logger.warning(f"[F290] aiofiles write failed: {_write_err}")
                        # Fallback to sync write
                        try:
                            with open(cast(str, self._persist_path_str), "ab") as _sf:
                                _sf.write(cast(bytes, data))
                                _sf.flush()
                        except Exception:  # noqa: BLE001
                            pass
                else:
                    # ISSUE-2 FIX: Use sync I/O directly in write fallback path.
                    # asyncio.to_thread() calls get_running_loop() internally,
                    # which raises "already running" in nested loop contexts.
                    try:
                        _path_str = cast(str, self._persist_path_str)
                        with open(_path_str, "ab") as _sf:
                            _sf.write(cast(bytes, data))
                            _sf.flush()
                    except Exception:  # noqa: BLE001
                        pass

                # fsync batching
                fsync_counter += 1
                if fsync_counter >= self._FSYNC_EVERY_N_EVENTS:
                    if _afile is not None:
                        try:
                            await _afile.flush()
                            # Note: os.fsync requires sync call, do via thread
                            os.fsync(_afile.fileno())
                        except Exception:  # noqa: BLE001
                            pass
                    fsync_counter = 0

            except asyncio.CancelledError:
                break
            except Exception as _worker_err:
                logger.warning(f"[F290] Async write worker error: {_worker_err}")

        # F290-ASYNCIO: Drain remaining queue items before shutdown
        # Ensures no event loss on graceful worker shutdown
        while True:
            try:
                drain_item = self._async_write_queue.get_nowait()
                if drain_item is None:
                    break
                if _afile is not None:
                    try:
                        await _afile.write(drain_item)
                        await _afile.flush()
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    # ISSUE-2 FIX: Use sync I/O directly in final drain path.
                    try:
                        _path_str = cast(str, self._persist_path_str)
                        with open(_path_str, "ab") as _sf:
                            _sf.write(drain_item)
                            _sf.flush()
                    except Exception:  # noqa: BLE001
                        pass
            except asyncio.QueueEmpty:
                break
            except Exception:
                break

        # Final flush and close
        if _afile is not None:
            try:
                await _afile.flush()
                await _afile.close()
            except Exception:  # noqa: BLE001
                pass

    # A4-15: Sub-batch size for streaming Arrow writes — M1 8GB heap-safe
    _ARROW_SUB_BATCH = 256

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
                # A4-15: Stream through sub-batches — limits peak heap to ~256 events
                for i in range(0, len(batch), self._ARROW_SUB_BATCH):
                    sub = batch[i:i + self._ARROW_SUB_BATCH]
                    arrays = [
                        pa.array([e.get('timestamp', datetime.now(UTC).timestamp()) for e in sub], type=pa.float64()),  # noqa: DTZ005
                        pa.array([e.get('event_type', 'unknown') for e in sub], type=pa.string()),
                        pa.array([orjson.dumps(e.get('data', {})).decode() for e in sub], type=pa.string()),
                        pa.array([e.get('content_hash', '') for e in sub], type=pa.string()),
                    ]
                    batch_arrow = pa.record_batch(arrays, schema=self._arrow_schema)
                    self._arrow_writer.write_batch(batch_arrow)
                return
            except Exception as e:
                logger.warning(f"[Arrow] IPC write failed, falling back to SQLite: {e}")

        # SQLite fallback path — build records only when Arrow unavailable
        records = []
        for event_data in batch:
            timestamp = event_data.get('timestamp', datetime.now(UTC).timestamp())  # noqa: DTZ005
            event_type = event_data.get('event_type', 'unknown')
            data = orjson.dumps(event_data).decode()
            content_hash = event_data.get('content_hash', '')
            records.append((timestamp, event_type, data, content_hash))

        db = self._db
        if db is None:
            return
        if not hasattr(db, 'executemany'):
            logger.warning("EvidenceLog._db not initialized as aiosqlite.Connection")
            return
        await db.executemany(
            "INSERT INTO events (timestamp, event_type, data, hash) VALUES (?, ?, ?, ?)",
            records,
        )
        await db.commit()

    def _init_encryption(self):
        """Initialize encryption cipher."""
        if not self._encryption_key:
            self._encryption_key = secrets.token_bytes(32)
            logger.warning("[ENCRYPT] No ENCRYPTION_KEY env - using temporary key")

        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            self._cipher = (Cipher, algorithms, modes)  # Store for lazy init
        except ImportError:
            logger.warning("[ENCRYPT] cryptography not available, encryption disabled")
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
            # Seznam polí co jsou potenciálně velká
            large_fields = {'content', 'fulltext', 'html', 'body', 'text',
                          'raw_data', 'document', 'finding_text'}

            if key in large_fields and isinstance(value, str):
                # Vytvoř preview místo fulltextu
                if len(value) > self.MAX_PAYLOAD_PREVIEW:
                    preview = value[:self.MAX_PAYLOAD_PREVIEW] + "..."
                    # Přidej hash pro reference
                    content_hash = hashlib.sha256(value.encode()).hexdigest()[:16]
                    trimmed[key] = f"[preview:{content_hash}] {preview}"
                else:
                    trimmed[key] = value
            elif isinstance(value, dict):
                # Rekurzivně trim nested dicts
                trimmed[key] = self._trim_payload(value)
            elif isinstance(value, list) and len(value) > 10:
                # Omež dlouhé listy na preview
                trimmed[key] = value[:10] + [f"... ({len(value) - 10} more)"]
            else:
                trimmed[key] = value

        return trimmed

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
        # Issue 8.3: silent_failure bypass — no I/O, no RAM allocation
        if self._silent_failure:
            return

        # H1/H3: Block on _closed AND _frozen (both seal the write path)
        if self._frozen:
            raise RuntimeError("Cannot append to frozen EvidenceLog")
        if self._closed:
            raise RuntimeError("Cannot append to closed EvidenceLog")

        # H3: Also block if aclose() is in progress (drain phase)
        if self._closing:
            raise RuntimeError("Cannot append while EvidenceLog is closing")

        # Kontrola run_id
        if event.run_id != self._run_id:
            raise ValueError(
                f"Event run_id '{event.run_id}' does not match log run_id '{self._run_id}'"
            )

        # NOTE: verify_integrity() removed in Sprint 79a - redundant with content_hash
        # computed at event creation. Chain integrity verified on load via verify_chain().

        # ===== HASH-CHAIN: Compute chain hash =====
        self._seq += 1
        event.seq_no = self._seq
        event.prev_chain_hash = self._chain_head
        # chain_hash = sha256(prev_chain_hash + ":" + content_hash + ":" + event_id)
        chain_input = f"{self._chain_head}:{event.content_hash}:{event.event_id}"
        event.chain_hash = hashlib.sha256(chain_input.encode()).hexdigest()
        self._chain_head = event.chain_hash  # Update chain head

        # Push to async queue for SQLite batching (if initialized)
        queue_size = self._queue.qsize() if self._queue else 0
        trace_evidence_append(event.event_type, queue_size, "queued")

        # F11C-FIX: SQLite sync fallback when _initialized=False or queue full.
        # If _init_db() succeeded but _flush_worker failed to start, events go only to JSONL.
        # Write directly to SQLite here (sync, not async) so events survive in the DB
        # even when the async flush worker is dead.
        # ISSUE-4 FIX: _initialized=True doesn't guarantee workers are healthy.
        # Also check that flush_task is running — if create_task failed, it's None.
        _worker_alive = (
            self._initialized
            and self._flush_task is not None
            and not self._flush_task.done()
        )
        if _worker_alive and self._queue and not self._closing:
            try:
                # F320-ASYNCIO FIX: Use call_soon_threadsafe instead of put_nowait.
                # put_nowait() with a full queue raises QueueFull immediately and
                # the caller retries forever in a tight loop (blocking the event loop).
                # call_soon_threadsafe() schedules the put for the next event-loop
                # iteration without blocking the caller. The _flush_worker gets
                # CPU time to drain the queue before the next put is processed.
                # This allows batch accumulation in _flush_worker even under pressure.
                _loop = self._loop
                if _loop is not None and not _loop.is_closed():
                    _loop.call_soon_threadsafe(
                        lambda e=event.to_dict(): _put_to_queue(self._queue, e)
                    )
                else:
                    # No event loop: fall back to put_nowait (sync path, worker dead)
                    self._queue.put_nowait(event.to_dict())
            except asyncio.QueueFull:
                logger.warning("SQLite queue full, falling back to direct sync write")
                trace_queue_drop("sqlite_queue", queue_size + 1)
                # Fall through to sync path below
        elif not self._initialized and self._db is not None:
            # initialize() partially succeeded (DB open) but flush worker never started.
            # Write directly to SQLite synchronously via to_thread (non-blocking for caller).
            _event_dict = event.to_dict()
            try:
                def _sync_insert():
                    import sqlite3
                    # Use blocking sqlite3 for the sync insert path (aiosqlite thread unsafe)
                    db_path = str(self._db_path)
                    conn = sqlite3.connect(db_path, timeout=30.0)
                    conn.execute("PRAGMA busy_timeout=30000")  # 30s — prevent "database table locked"
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")  # ~3-5× faster for WAL
                    conn.execute("PRAGMA wal_autocheckpoint=1000")
                    conn.execute("PRAGMA cache_size=-8192")
                    conn.execute(
                        "INSERT INTO events (timestamp, event_type, data, hash) VALUES (?, ?, ?, ?)",
                        (
                            _event_dict.get('timestamp', 0.0),
                            _event_dict.get('event_type', 'unknown'),
                            orjson.dumps(_event_dict).decode(),
                            _event_dict.get('content_hash', ''),
                        ),
                    )
                    conn.commit()
                    conn.close()
                # asyncio.to_thread: non-blocking for the sync path, M1 8GB safe
                t = threading.Thread(target=_sync_insert, daemon=True)
                t.start()
                trace_evidence_append(event.event_type, 0, "sync_sqlite")
            except Exception as _sync_err:
                logger.debug(f"[F11C] Sync SQLite fallback failed (non-fatal): {_sync_err}")

        # ===== SWAL: Single Write-Ahead Log (F286/F290-ASYNCIO) =====
        # JSONL is the authoritative WAL. SQLite is a derived queryable index.
        # On crash: SQLite replays from JSONL to restore consistency.
        # F290-ASYNCIO: JSONL writes now go through async queue for non-blocking I/O.
        # F286-FIX: JSONL write MUST succeed. SQLite is fail-safe derivative.
        if self._enable_persist:
            try:
                line = event.to_jsonl_line()
                bytes_to_write = line.encode('utf-8') + b'\n'

                # Encrypt if enabled
                if self._encrypt_at_rest and self._cipher and self._encryption_key:
                    try:
                        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                        nonce = secrets.token_bytes(12)
                        cipher = Cipher(
                            algorithms.AES(self._encryption_key),
                            modes.GCM(nonce)
                        )
                        encryptor = cipher.encryptor()
                        encrypted = encryptor.update(bytes_to_write) + encryptor.finalize()
                        # Write: nonce (12) + tag (16) + ciphertext
                        bytes_to_write = nonce + encryptor.tag + encrypted
                        logger.debug(f"[ENCRYPT] stored bytes_in={len(line)} bytes_out={len(bytes_to_write)}")
                    except Exception as e:
                        logger.warning(f"[ENCRYPT] failed: {e}")

                # F290-ASYNCIO: Enqueue to async write worker (non-blocking)
                # If queue is full, fall back to sync write to maintain durability guarantee
                try:
                    if self._async_write_task is not None and self._async_write_task.done():
                        self._async_write_task = None
                    if self._async_write_task is not None and not self._async_write_queue.full():
                        self._async_write_queue.put_nowait(bytes_to_write)
                    else:
                        # Queue full or worker not running: sync fallback (blocking but durable)
                        self._sync_write_fallback(line, bytes_to_write)
                except asyncio.QueueFull:
                    # Queue full — use sync fallback
                    self._sync_write_fallback(line, bytes_to_write)
                except RuntimeError:
                    # No running event loop — use sync fallback
                    self._sync_write_fallback(line, bytes_to_write)
            except Exception as e:
                # F286-FIX: JSONL write failure is FATAL — SWAL must be durable
                # Do NOT continue if WAL write fails, event is lost otherwise
                logger.critical(f"[F286] SWAL write failed (FATAL): {e}")
                raise RuntimeError(f"EvidenceLog SWAL write failed: {e}") from e

        # Trim payload pro RAM šetření
        # NOTE: After trimming, content_hash must be RECOMPUTED to match the
        # trimmed payload in RAM. This ensures verify_integrity() passes
        # on in-memory events. The JSONL was already written with the correct
        # original-payload hash before this trim, so persisted events are fine.
        # payload is bytes (msgspec zero-copy), decode->trim->re-encode
        decoded_payload = orjson.loads(event.payload)
        trimmed_payload = self._trim_payload(decoded_payload)
        event.payload = orjson.dumps(trimmed_payload)
        event.content_hash = event.calculate_hash()

        # Recompute chain_hash to match the new content_hash.
        # The chain_hash at line 558 was computed with the original (pre-trim)
        # content_hash. After content_hash update, chain_hash must be updated too
        # so verify_all() chain validation passes.
        chain_input = f"{event.prev_chain_hash}:{event.content_hash}:{event.event_id}"
        event.chain_hash = hashlib.sha256(chain_input.encode()).hexdigest()
        self._chain_head = event.chain_hash

        # Ring buffer logika - using deque with maxlen (auto overflow)
        # Check if deque is full before appending
        was_full = len(self._log) == self.MAX_RAM_EVENTS

        # Append to deque (会自动丢弃最旧的如果满了)
        self._log.append(event)
        self._total_count += 1

        # If deque overflowed (was full before append), rebuild indexes.
        # _rebuild_indexes() iterates the FULL _log, so inline index updates
        # below would duplicate entries. Only rebuild, no inline updates.
        if was_full:
            self._dropped_count += 1
            try:
                self._rebuild_indexes()
            except Exception:  # noqa: BLE001
                pass  # noqa: BLE001  # Fail-safe: never crash orchestration
            # Index updates for this event are handled by _rebuild_indexes()
            # (it iterates all events including this one at position len-1)
            return

        # Normal path: update indexes for the single new event.
        # No rebuild needed — deque has room and was not full.
        index = len(self._log) - 1
        self._index_by_type[event.event_type].append(index)
        for source_id in event.source_ids:
            if source_id not in self._index_by_source:
                self._index_by_source[source_id] = deque(maxlen=self.MAX_RAM_EVENTS)
            self._index_by_source[source_id].append(index)

        # ===== SHADOW ANALYTICS HOOK (Sprint 8AX) =====
        # Non-blocking, fail-open: extract finding metadata and enqueue for DuckDB shadow.
        # GHOST_DUCKDB_SHADOW=1 must be set to activate.
        # This runs AFTER the event is fully committed to the log — zero risk to main path.
        try:
            from knowledge.analytics_hook import shadow_record_finding
            # Only emit shadow records for evidence_packet events with URL-bearing payloads
            if event.event_type == "evidence_packet":
                # payload is bytes (msgspec zero-copy), decode for access
                payload: dict[str, Any] = orjson.loads(event.payload) if event.payload else {}
                # Extract correlation from payload if present (flattened by create_event)
                _corr: dict[str, Any] | None = payload.get("_correlation")
                shadow_record_finding(
                    finding_id=event.event_id,
                    query=payload.get("query", ""),
                    source_type="evidence_packet",
                    confidence=event.confidence,
                    run_id=event.run_id,
                    url=payload.get("url"),
                    title=payload.get("title"),
                    source=payload.get("source"),
                    relevance_score=payload.get("relevance_score"),
                    branch_id=_corr.get("branch_id") if _corr else None,
                    provider_id=_corr.get("provider_id") if _corr else None,
                    action_id=_corr.get("action_id") if _corr else None,
                )
        except Exception:  # noqa: BLE001
            # Fail-open: shadow hook never crashes the main path
            pass

    def _rebuild_indexes(self) -> None:
        """Přebuduj indexy po vyřazení z ring bufferu."""
        # Bounded deques — auto-evict oldest when ring buffer overflows.
        # maxlen matches _log deque so indices never exceed MAX_RAM_EVENTS.
        self._index_by_type = {
            "tool_call": deque(maxlen=self.MAX_RAM_EVENTS),
            "observation": deque(maxlen=self.MAX_RAM_EVENTS),
            "synthesis": deque(maxlen=self.MAX_RAM_EVENTS),
            "error": deque(maxlen=self.MAX_RAM_EVENTS),
            "decision": deque(maxlen=self.MAX_RAM_EVENTS),
            "evidence_packet": deque(maxlen=self.MAX_RAM_EVENTS),
        }
        self._index_by_source = {}

        for i, event in enumerate(self._log):
            self._index_by_type[event.event_type].append(i)
            for source_id in event.source_ids:
                if source_id not in self._index_by_source:
                    self._index_by_source[source_id] = deque(maxlen=self.MAX_RAM_EVENTS)
                self._index_by_source[source_id].append(i)

    def create_event(
        self,
        event_type: Literal["tool_call", "observation", "synthesis", "error", "decision", "evidence_packet"],
        payload: dict[str, Any],
        source_ids: list[str] | None = None,
        confidence: float = 1.0,
        correlation: dict[str, str | None] | None = None,
    ) -> EvidenceEvent | None:
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
        # Issue 8.3: silent_failure bypass
        if self._silent_failure:
            return None

        # Phase4: 10% sampling for non-error events (errors always logged)
        if event_type != "error" and self._sample_rate < 1.0:
            import random as _random
            if _random.random() > self._sample_rate:
                return None  # Sampled out — silently drop

        # H1: Reject new events if log is closed
        if self._closed:
            raise RuntimeError("Cannot create event in closed EvidenceLog")

        event_id = f"{self._run_id}_{uuid.uuid4().hex[:12]}"

        # Sprint F200A FIX: Add correlation to payload BEFORE hash computation.
        # Previously correlation was added AFTER calculate_hash(), causing
        # verify_integrity() to fail on events with correlation (the stored
        # content_hash didn't reflect the final payload with _correlation).
        # Sprint F200E FIX: Do NOT mutate caller's dict — use shallow copy.
        if correlation:
            payload = {**payload, "_correlation": correlation}

        # Vytvoř událost s dočasným hashem
        # payload encoded as bytes for msgspec zero-copy
        event = EvidenceEvent(
            event_id=event_id,
            event_type=event_type,
            timestamp=datetime.now(UTC).timestamp(),
            payload=orjson.dumps(payload),
            source_ids=source_ids or [],
            confidence=confidence,
            content_hash="",  # Dočasné
            run_id=self._run_id,
        )

        # Vypočítej hash - nyní včetně correlation
        event.content_hash = event.calculate_hash()

        # Přidej do logu
        self.append(event)

        return event

    def create_evidence_packet_event(
        self,
        evidence_id: str,
        packet_path: str,
        summary: dict[str, Any],
        source_ids: list[str] | None = None,
        confidence: float = 1.0,
    ) -> EvidenceEvent | None:
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
        # Trim payload - jen summary + pointer, žádné fulltexty
        payload = {
            'evidence_id': evidence_id,
            'packet_path': packet_path,  # Pointer na disk
            'summary': summary,  # Jen metadata, ne obsah
        }

        return self.create_event(
            event_type="evidence_packet",
            payload=payload,
            source_ids=source_ids,
            confidence=confidence,
        )

    # =========================================================================
    # FORENSIC ANALYSIS ATTACHMENT - Sprint F261
    # =========================================================================
    # Persists forensic analysis results in the tamper-evident evidence chain
    # so they participate in verify_all() and get_chain(). Forensic results
    # are bounded (_FORENSIC_*) to prevent payload blowup. Failure to attach
    # is fail-safe: returns None, never raises.

    # Sprint F261: Forensic analysis hard limits
    _FORENSIC_MAX_KEYS = 30
    _FORENSIC_MAX_VALUE_LEN = 1000
    _FORENSIC_MAX_LIST_ITEMS = 20
    _FORENSIC_MAX_DEPTH = 3

    def _bound_forensic_value(
        self, value: Any, depth: int = 0
    ) -> Any:
        """Bound forensic result values to prevent payload blowup.

        F261 invariant: bounded payloads only — never trust caller sizes.
        Trims strings, caps list lengths, recurses into dicts up to
        _FORENSIC_MAX_DEPTH.
        """
        if depth > self._FORENSIC_MAX_DEPTH:
            return "[depth_truncated]"
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return value
        if isinstance(value, str):
            if len(value) > self._FORENSIC_MAX_VALUE_LEN:
                return value[: self._FORENSIC_MAX_VALUE_LEN] + "..."
            return value
        if isinstance(value, (list, tuple)):
            cap = self._FORENSIC_MAX_LIST_ITEMS
            items = [self._bound_forensic_value(v, depth + 1) for v in value[:cap]]
            truncated = len(value) - len(items)
            if truncated > 0:
                items.append(f"[...{truncated}_more_items_truncated]")
            return items
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for i, (k, v) in enumerate(value.items()):
                if i >= self._FORENSIC_MAX_KEYS:
                    out["_truncated_keys"] = list(value.keys())[i:][:5]
                    break
                out[str(k)[:80]] = self._bound_forensic_value(v, depth + 1)
            return out
        # Fallback: serialize other types to bounded string
        return str(value)[: self._FORENSIC_MAX_VALUE_LEN]

    def attach_forensic_analysis(
        self,
        finding_id: str,
        forensic_result: Any,  # Accepts dict | None | any serializable; validated at runtime
        source_id: str | None = None,
        confidence: float = 0.95,
    ) -> EvidenceEvent | None:
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
        # Fail-safe: empty / invalid input
        if not finding_id:
            logger.warning("[FORENSIC] attach_forensic_analysis called with empty finding_id")
            return None
        if forensic_result is None:
            logger.debug(f"[FORENSIC] attach_forensic_analysis: no forensic_result for {finding_id}")
            return None
        if not isinstance(forensic_result, dict):
            logger.warning(
                f"[FORENSIC] attach_forensic_analysis: forensic_result must be dict, "
                f"got {type(forensic_result).__name__} for {finding_id}"
            )
            return None

        # Clamp confidence to valid range
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.95

        # Bound the payload — never trust caller sizes
        bounded_result = self._bound_forensic_value(forensic_result)

        payload = {
            "kind": "forensic_analysis",
            "finding_id": str(finding_id)[:128],
            "forensic_result": bounded_result,
            "attached_at": datetime.now(UTC).isoformat(),
        }

        # source_id defaults to finding_id for traceability
        effective_source_id = (source_id or finding_id)[:128]

        try:
            return self.create_event(
                event_type="evidence_packet",
                payload=payload,
                source_ids=[effective_source_id],
                confidence=confidence,
            )
        except (RuntimeError, ValueError) as exc:
            # Closed / frozen / run_id mismatch — fail-safe
            logger.warning(
                f"[FORENSIC] attach_forensic_analysis failed for {finding_id}: {exc}"
            )
            return None

    def get_forensic_analyses(
        self,
        finding_id: str,
    ) -> list[EvidenceEvent]:
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
            if event.event_type != "evidence_packet":
                continue
            payload: dict[str, Any] = orjson.loads(event.payload) if event.payload else {}
            if payload.get("kind") != "forensic_analysis":
                continue
            if payload.get("finding_id") != finding_id:
                continue
            out.append(event)
        return out

    # =========================================================================
    # DECISION LEDGER - Decision events with hard limits
    # =========================================================================

    # Decision event hard limits
    MAX_DECISION_SUMMARY_KEYS = 20
    MAX_DECISION_SUMMARY_VALUE_LEN = 200
    MAX_DECISION_REASONS = 8
    MAX_DECISION_REASON_LEN = 120
    MAX_DECISION_REF_EVIDENCE = 10
    MAX_DECISION_REF_CLUSTERS = 10
    MAX_DECISION_REF_URLS = 10

    def create_decision_event(
        self,
        kind: str,
        summary: dict[str, Any],
        reasons: list[str],
        refs: dict[str, list[str]],
        confidence: float = 1.0,
    ) -> EvidenceEvent | None:
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
        # Validate kind
        valid_kinds = {"bandit", "playbook", "backpressure", "delta", "alignment", "primary_chase", "drift"}
        if kind not in valid_kinds:
            logger.warning(f"[DECISION] Invalid kind={kind}, using 'drift'")
            kind = "drift"

        # Trim summary - max 20 keys, max 200 chars per value
        trimmed_summary = {}
        for i, (k, v) in enumerate(summary.items()):
            if i >= self.MAX_DECISION_SUMMARY_KEYS:
                break
            v_str = str(v)
            if len(v_str) > self.MAX_DECISION_SUMMARY_VALUE_LEN:
                v_str = v_str[:self.MAX_DECISION_SUMMARY_VALUE_LEN] + "..."
            trimmed_summary[k] = v_str

        # Trim reasons - max 8, max 120 chars each
        trimmed_reasons = []
        for i, r in enumerate(reasons):
            if i >= self.MAX_DECISION_REASONS:
                break
            if len(r) > self.MAX_DECISION_REASON_LEN:
                r = r[:self.MAX_DECISION_REASON_LEN] + "..."
            trimmed_reasons.append(r)

        # Trim refs - max 10 per type
        trimmed_refs = {}
        if 'evidence_ids' in refs:
            trimmed_refs['evidence_ids'] = refs['evidence_ids'][:self.MAX_DECISION_REF_EVIDENCE]
        if 'cluster_ids' in refs:
            trimmed_refs['cluster_ids'] = refs['cluster_ids'][:self.MAX_DECISION_REF_CLUSTERS]
        if 'url_hashes' in refs:
            trimmed_refs['url_hashes'] = refs['url_hashes'][:self.MAX_DECISION_REF_URLS]

        # Build payload
        payload = {
            'kind': kind,
            'summary': trimmed_summary,
            'reasons': trimmed_reasons,
            'refs': trimmed_refs,
        }

        # Create event - uses ring buffer automatically (max 100)
        return self.create_event(
            event_type="decision",
            payload=payload,
            source_ids=[],  # Decision events don't need source_ids
            confidence=confidence,
        )

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

    def query(
        self,
        event_type: str | None = None,
        min_confidence: float = 0.0,
        after_timestamp: datetime | None = None,
        before_timestamp: datetime | None = None,
        limit: int | None = None,
    ) -> list[EvidenceEvent]:
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

        # Urči zdrojové indexy
        if event_type and event_type in self._index_by_type:
            indices = self._index_by_type[event_type]
        else:
            indices = range(len(self._log))

        # Filtrování
        for idx in indices:
            event = self._log[idx]

            # Confidence filter
            if event.confidence < min_confidence:
                continue

            # Timestamp filters
            if after_timestamp and event.timestamp < after_timestamp:
                continue
            if before_timestamp and event.timestamp > before_timestamp:
                continue

            results.append(event)

        # Aplikuj limit
        if limit and len(results) > limit:
            results = results[:limit]

        return results

    def get_summary(self, last_n: int = 10) -> str:
        """
        Vytvoří shrnutí logu pro Hermes.

        Vrací stručné shrnutí posledních N událostí - ne celý raw log.

        Args:
            last_n: Počet posledních událostí k zahrnutí

        Returns:
            Formátovaný string shrnutí
        """
        lines = [
            "=" * 60,
            "EVIDENCE LOG SUMMARY",
            "=" * 60,
            "",
            f"Run ID: {self._run_id}",
            f"Total Events: {self.size}",
            f"Created: {self._created_at.isoformat()}",
            "",
            "Event Counts by Type:",
        ]

        for event_type, indices in self._index_by_type.items():
            count = len(indices)
            if count > 0:
                lines.append(f"  {event_type}: {count}")

        lines.extend([
            "",
            "-" * 40,
            f"Last {last_n} Events (newest first):",
            "-" * 40,
        ])

        # Poslední N událostí v reverzním pořadí
        recent_events = list(self._log)[-last_n:] if len(self._log) >= last_n else list(self._log)
        recent_events = list(reversed(recent_events))

        for i, event in enumerate(recent_events, 1):
            timestamp = datetime.fromtimestamp(event.timestamp, UTC).strftime("%H:%M:%S")
            payload_summary = self._summarize_payload(orjson.loads(event.payload) if event.payload else {})

            lines.append(
                f"{i}. [{timestamp}] {event.event_type.upper()} "
                f"(conf: {event.confidence:.2f})"
            )
            lines.append(f"   {payload_summary}")

            if event.source_ids:
                sources_str = ", ".join(event.source_ids[:3])
                if len(event.source_ids) > 3:
                    sources_str += f" (+{len(event.source_ids) - 3} more)"
                lines.append(f"   Sources: {sources_str}")

            lines.append("")

        lines.extend([
            "=" * 60,
        ])

        return "\n".join(lines)

    def _summarize_payload(self, payload: dict[str, Any], max_length: int = 60) -> str:
        """Vytvoří stručné shrnutí payloadu"""
        if not payload:
            return "(no payload)"

        # Zkus najít vhodné pole pro shrnutí
        priority_fields = ["action", "tool", "query", "result", "message", "summary"]

        for field in priority_fields:
            if field in payload:
                value = payload[field]
                if isinstance(value, str):
                    if len(value) > max_length:
                        return f"{field}={value[:max_length]}..."
                    return f"{field}={value}"
                return f"{field}={str(value)[:max_length]}"

        # Fallback: použij první klíč
        first_key = next(iter(payload.keys()))
        value = str(payload[first_key])[:max_length]
        return f"{first_key}={value}{'...' if len(str(payload[first_key])) > max_length else ''}"

    def to_jsonl(self, path: Path | None = None) -> None:
        """
        Exportuje log do JSONL souboru pro replay mode.

        M1 8GB: Pokud je již persistováno, pouze zkopíruj soubor.

        Args:
            path: Cesta k výstupnímu souboru (None = použij persist_path)
        """
        export_path = path or self._persist_path
        if not export_path:
            raise ValueError("No path specified for export")

        export_path = Path(export_path)
        export_path.parent.mkdir(parents=True, exist_ok=True)

        # Pokud je persistováno na stejné místo, nic nedělej
        if self._persist_path and export_path == self._persist_path:
            return

        # Pokud je persistováno jinde, zkopíruj soubor
        if self._persist_path and self._persist_path.exists():
            import shutil
            shutil.copy2(self._persist_path, export_path)
            return

        # Fallback: export z RAM
        with open(export_path, 'w', encoding='utf-8') as f:
            for event in self._log:
                f.write(event.to_jsonl_line() + '\n')

    @classmethod
    def from_jsonl(
        cls,
        path: Path,
        run_id: str | None = None,
        load_to_ram: bool = False,
        max_ram_events: int = 100
    ) -> EvidenceLog:
        """
        Načte log z JSONL souboru - M1 8GB optimized.

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
            raise FileNotFoundError(f"JSONL file not found: {path}")

        # Nejprve zjisti run_id z prvního řádku
        detected_run_id = run_id
        if detected_run_id is None:
            with open(path, encoding='utf-8') as f:
                first_line = f.readline().strip()
                if first_line:
                    data = orjson.loads(first_line)
                    detected_run_id = data.get("run_id", "unknown")

        # Vytvoř log bez persistence (pouze čtení)
        log = cls(
            run_id=detected_run_id or "unknown",
            enable_persist=False
        )

        # Spočítej celkový počet řádků
        total_lines = 0
        with open(path, encoding='utf-8') as f:
            for _ in f:
                total_lines += 1

        log._total_count = total_lines

        # Načti události do RAM - pouze poslední N pro ring buffer
        with open(path, encoding='utf-8') as f:
            lines = f.readlines()

            # Pokud nechceme vše v RAM, vem jen poslední max_ram_events
            if not load_to_ram and len(lines) > max_ram_events:
                lines = lines[-max_ram_events:]
                log._dropped_count = total_lines - len(lines)

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                data = orjson.loads(line)
                event = EvidenceEvent.from_dict(data)
                # Přidej přímo do _log (skip append pro rychlost při načítání)
                index = len(log._log)
                log._log.append(event)
                log._index_by_type[event.event_type].append(index)
                for source_id in event.source_ids:
                    if source_id not in log._index_by_source:
                        log._index_by_source[source_id] = deque(maxlen=log.MAX_RAM_EVENTS)
                    log._index_by_source[source_id].append(index)

        return log

    def freeze(self) -> None:
        """Zmrazí log - přepne do read-only režimu"""
        self._frozen = True

    def write_manifest(self) -> Path | None:
        """
        Writes a manifest JSON file next to the persist path.

        The manifest contains:
        - run_id, chain_head, total_count, created_at, last_seq_no, persist_path

        Returns:
            Path to the written manifest file, or None if no persist_path
        """
        if not self._persist_path:
            logger.warning("Cannot write manifest: no persist_path set")
            return None

        manifest = {
            "run_id": self._run_id,
            "chain_head": self._chain_head,
            "total_count": self._total_count,
            "created_at": self._created_at.isoformat(),
            "last_seq_no": self._seq,
            "persist_path": str(self._persist_path),
            "genesis_hash": self._genesis_hash,
        }

        # Write manifest next to persist path
        manifest_path = self._persist_path.with_suffix('.manifest.json')
        try:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(manifest_path, 'wb') as f:
                f.write(orjson.dumps(manifest, option=orjson.OPT_INDENT_2))
            logger.info(f"[EVIDENCE] Manifest written: {manifest_path}")
            return manifest_path
        except Exception as e:
            logger.error(f"Failed to write manifest: {e}")
            return None

    async def aclose(self) -> None:
        """
        Async cleanup: shutdown flush worker, close SQLite, close persist file.

        This is the canonical async cleanup path. All resources are closed
        in order with proper shutdown signaling.

        Idempotent: safe to call multiple times.
        """
        # R6 Idempotency: early exit if already closed
        if self._closed:
            return

        # Sprint F200E: Signal closing FIRST — no new appends will be queued.
        # This MUST happen before draining so that any concurrent append()
        # calls that see _closing=True will skip queueing.
        self._closing = True

        # F285: Signal flush worker to drain and exit via asyncio.Event.
        # This is safer than cancel() because it gives the worker a defined
        # shutdown path: it flushes pending items, exits its loop, and then
        # aclose() closes _db. The order is guaranteed:
        #   1. _flush_shutdown.set()  → worker sees event, exits loop gracefully
        #   2. Worker final flush     → runs with _db still open
        #   3. _db.close()           → only after worker has exited
        if self._flush_shutdown:
            self._flush_shutdown.set()

        # Enqueue None to wake the worker if it's blocked on _queue.get()
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass  # Queue full is fine — worker will drain via timeout

        # Wait for the flush task to finish cleanly (it exits after final flush).
        # NOTE: Do NOT use asyncio.shield here. shield only protects a task from
        # being cancelled by its OWNER'S cancel() call — it does NOT protect against
        # runtime-initiated CancelledError (e.g. from SIGTERM teardown). When the
        # event loop is shutting down, wait_for raises CancelledError even if the
        # shielded task is still running. The worker would then exit without flushing,
        # leaving evidence manifest corrupted.
        #
        # Correct pattern:
        #   1. Fast path: wait_for(task, timeout) — task finishes in time
        #   2. Timeout: cancel() task, await it — gives it a final flush chance
        #   3. CancelledError (runtime shutdown): cancel(), await with short timeout
        #      — guarantees final flush even on forced exit
        if self._flush_task:
            try:
                await asyncio.wait_for(self._flush_task, timeout=10.0)
            except TimeoutError:
                # Worker is stuck — cancel and await its final flush
                # ISSUE-2 FIX: 10s timeout (was 5s). CancelledError from cancel()
                # triggers drain path in worker (batch flush then break), giving
                # the worker up to 5s more to flush. Prevents evidence loss when
                # cancel() races with a slow SQLite commit. Also: no timeout race
                # on _db — aclose waits for _flush_shutdown signal before closing.
                logger.warning("Flush worker did not exit in 10s, cancelling")
                self._flush_task.cancel()
                try:
                    await asyncio.wait_for(self._flush_task, timeout=5.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass
            except asyncio.CancelledError:
                # Runtime shutdown (SIGTERM) — cancel and await final flush.
                # This guarantees the worker flushes its pending batch before
                # the process exits, preventing evidence manifest corruption.
                self._flush_task.cancel()
                try:
                    await asyncio.wait_for(self._flush_task, timeout=5.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass
            finally:
                self._flush_task = None

        # ISSUE-2 FIX: Signal async write worker to drain and exit.
        # Mirrors the _flush_worker pattern but for _async_write_worker.
        # F310-FIX: Use blocking put with timeout instead of put_nowait to ensure
        # the None sentinel is always enqueued, even when queue is full.
        try:
            await asyncio.wait_for(self._async_write_queue.put(None), timeout=1.0)
        except (TimeoutError, asyncio.QueueFull):
            pass  # Timeout/full is fine — worker will drain via timeout
        # Set shutdown event (worker checks it in its loop)
        if self._async_write_shutdown:
            self._async_write_shutdown.set()

        # Wait for async write task to finish cleanly
        if self._async_write_task:
            try:
                await asyncio.wait_for(self._async_write_task, timeout=5.0)
            except TimeoutError:
                logger.warning("Async write worker did not exit in 5s, cancelling")
                self._async_write_task.cancel()
                try:
                    await asyncio.wait_for(self._async_write_task, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass
            except asyncio.CancelledError:
                self._async_write_task.cancel()
                try:
                    await asyncio.wait_for(self._async_write_task, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass
            finally:
                self._async_write_task = None

        # F285: Drain remaining items (items queued after _closing=True).
        # With the Event-based shutdown, the worker should have drained these,
        # but we drain again to be safe (items can arrive between set() and wait).
        # ISSUE-3 FIX: yield to event loop between each get_nowait() to avoid
        # blocking the event loop when draining a large queue (~10K+ items).
        drained = []
        while True:
            try:
                item = self._queue.get_nowait()
                if item is None:
                    break
                drained.append(item)
            except asyncio.QueueEmpty:
                # ISSUE-3 FIX: asyncio.sleep(0) yields to event loop — allows other
                # coroutines (e.g. aclose() of other resources) to run between polls.
                # Without this, a 10K-item drain would block the event loop entirely.
                await asyncio.sleep(0)
                try:
                    item = self._queue.get_nowait()
                    if item is None:
                        break
                    drained.append(item)
                except asyncio.QueueEmpty:
                    break

        # F314-4: Lock removed — _flush_worker is sole writer, aclose signals
        # shutdown event but does NOT call _flush_batch concurrently.
        # Arrow IPC: close writer before SQLite (Arrow is faster, done first)
        if self._arrow_writer is not None:
            try:
                self._arrow_writer.close()
                logger.info(f"[Arrow] IPC writer closed: {self._arrow_path}")
            except Exception as e:
                logger.warning(f"[Arrow] Failed to close writer: {e}")
            finally:
                self._arrow_writer = None

        # Flush drained items (async SQLite — _db still open here)
        if drained and self._db is not None:
            try:
                await self._flush_batch(drained)
            except Exception as e:
                logger.warning(f"Failed to flush remaining items: {e}")

        # Now close _db — worker has already exited (signaled via _flush_shutdown),
        # so no race on _db access.
        if self._db is not None:
            try:
                # F285-FIX: TRUNCATE checkpoint — syncs WAL to main DB and
                # truncates WAL file to zero pages. Prevents WAL blow-up
                # on restart (WAL replay would otherwise re-read the whole WAL).
                await self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                await self._db.close()
            except Exception as e:
                logger.warning(f"Failed to close SQLite: {e}")
            finally:
                self._db = None

        # 4. Close persist file (synchronous — runs in thread via close())
        self._close_persist_file()

        # H6: Mark closed and freeze so log transitions to properly frozen state
        self._closed = True
        self._closing = False  # Reset closing flag now that shutdown is complete
        self.freeze()

        logger.debug(f"[EVIDENCE] aclose complete: run_id={self._run_id}")

    def _close_persist_file(self) -> None:
        """Close persist file with idempotency guard (runs in thread)."""
        if self._persist_file and not self._persist_file.closed:
            try:
                self._persist_file.flush()
                os.fsync(self._persist_file.fileno())
                self._persist_file.close()
            except Exception as e:
                logger.warning(f"Failed to close persist file: {e}")
            finally:
                self._persist_file = None
        elif self._persist_file is not None:
            # Already closed, just reset reference
            self._persist_file = None

    def close(self) -> None:
        """
        Sync cleanup: run aclose in a dedicated thread.

        Idempotent: safe to call multiple times.
        Works from both sync and async (pytest-asyncio) contexts.

        M1-SAFE / Python 3.14+: Never call run_until_complete() on a loop that
        is already running in another thread — that raises "This event loop is
        already running" (RuntimeError). Always use one of:

          * asyncio.run_coroutine_threadsafe(coro, stored_loop) — schedules on
            the parent loop and returns a concurrent.futures.Future we can
            block on from the worker thread.

          * New fresh loop with run_until_complete() — used when there is no
            live parent loop to schedule onto (e.g. standalone test harness
            or post-process cleanup).
        """
        import concurrent.futures

        def _run_aclose():
            stored_loop = self._loop
            if stored_loop is not None and stored_loop.is_running():
                # Parent loop is alive — schedule aclose on it and wait.
                # run_coroutine_threadsafe works across threads safely.
                future = asyncio.run_coroutine_threadsafe(self.aclose(), stored_loop)
                future.result()
            else:
                # No live parent loop — create a fresh loop.
                # _flush_task was already torn down by Path 1's async caller
                # (if any); this path is for sync-init EvidenceLog() use.
                import asyncio as _asyncio_module
                _new_loop = _asyncio_module.new_event_loop()
                try:
                    _new_loop.run_until_complete(self.aclose())
                finally:
                    _new_loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_aclose)
            future.result()

    def finalize(self) -> None:
        """
        Finalize the log: flush, write manifest, and close handles.

        This should be called at the end of a run (no user toggle).
        Always flushes and fsyncs to preserve crash-safety.

        Backward-compatible entry point — delegates to close() for full cleanup.
        """
        # Write manifest before closing (requires file still open)
        self.write_manifest()

        # Close all resources via canonical close path
        # Note: close() -> aclose() will set _closed=True at the end of cleanup
        self.close()

        # Freeze to prevent further modifications
        # H2: _frozen comes AFTER close (final state transition)
        self.freeze()

        logger.info(f"[EVIDENCE] Log finalized: run_id={self._run_id}, events={self._total_count}, chain_head={self._chain_head[:16]}...")  # noqa: E501

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
        chain_invalid = []  # Bounded RAM-safe

        # Track previous chain hash for linkage verification
        prev_expected_hash = self._genesis_hash

        for i, event in enumerate(self._log):
            # Content integrity check
            if event.verify_integrity():
                valid += 1
            else:
                invalid.append({
                    "index": i,
                    "event_id": event.event_id,
                    "stored_hash": event.content_hash,
                    "calculated_hash": event.calculate_hash(),
                })

            # Chain integrity check (only for events with chain fields)
            if event.chain_hash and event.seq_no > 0:
                # Validate chain_hash recomputation
                chain_input = f"{event.prev_chain_hash or self._genesis_hash}:{event.content_hash}:{event.event_id}"
                expected_chain_hash = hashlib.sha256(chain_input.encode()).hexdigest()

                if expected_chain_hash != event.chain_hash:
                    chain_valid = False
                    if len(chain_invalid) < 100:  # RAM-safe bound
                        chain_invalid.append({
                            "index": i,
                            "event_id": event.event_id,
                            "reason": "chain_hash_mismatch",
                            "expected": expected_chain_hash,
                            "stored": event.chain_hash,
                        })

                # Validate linkage prev_chain_hash == previous_event.chain_hash
                if event.prev_chain_hash and event.prev_chain_hash != prev_expected_hash:
                    chain_valid = False
                    if len(chain_invalid) < 100:
                        chain_invalid.append({
                            "index": i,
                            "event_id": event.event_id,
                            "reason": "linkage_broken",
                            "expected_prev": prev_expected_hash,
                            "stored_prev": event.prev_chain_hash,
                        })

                # Update expected hash for next iteration
                prev_expected_hash = event.chain_hash

        # Determine chain validity reason if invalid
        chain_invalid_reason = None
        if not chain_valid:
            if chain_invalid:
                first_issue = chain_invalid[0]
                chain_invalid_reason = f"{first_issue.get('reason', 'unknown')}_at_index_{first_issue.get('index', 0)}"
            else:
                # Legacy events without chain fields
                chain_invalid_reason = "legacy_events_missing_chain_fields"

        return {
            "total_events": total,
            "valid_events": valid,
            "invalid_events": len(invalid),
            "integrity_percentage": (valid / total * 100) if total > 0 else 100.0,
            "invalid_details": invalid[:10],  # Bounded output
            "all_valid": not invalid,
            # Chain verification results
            "chain_valid": chain_valid,
            "chain_invalid_reason": chain_invalid_reason,
            "chain_invalid": chain_invalid,
            "chain_head": self._chain_head,
            "last_seq_no": self._seq,
        }

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
            avg_conf = sum(e.confidence for e in events) / len(events)
            result[event_type] = {
                "count": len(indices),
                "avg_conf": round(avg_conf, 4),
                "pct": round(len(indices) / total * 100, 1),
            }

        return result

    def get_decision_summary(self) -> dict[str, Any]:
        """
        Vrací shrnutí decision událostí pro sprint retro.

        Ukazuje: počet rozhodnutí, confidence spread,
        top decision kinds, top reason patterns.

        Returns:
            Dict s decision statistikami
        """
        decisions = self.query(event_type="decision")

        if not decisions:
            return {"count": 0, "kinds": {}, "avg_confidence": 0.0}

        kind_counts: dict[str, int] = {}
        all_reasons: list[str] = []
        confidences: list[float] = []

        for e in decisions:
            payload: dict[str, Any] = orjson.loads(e.payload) if e.payload else {}
            kind = payload.get("kind", "unknown")
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            reasons = payload.get("reasons", [])
            all_reasons.extend(reasons)
            confidences.append(e.confidence)

        # Top reason fragments (first 40 chars)
        top_reasons: dict[str, int] = {}
        for r in all_reasons:
            fragment = r[:40] if len(r) > 40 else r
            top_reasons[fragment] = top_reasons.get(fragment, 0) + 1
        top_reasons = dict(sorted(top_reasons.items(), key=lambda x: -x[1])[:5])

        return {
            "count": len(decisions),
            "avg_confidence": round(sum(confidences) / len(confidences), 4),
            "min_confidence": round(min(confidences), 4),
            "max_confidence": round(max(confidences), 4),
            "kinds": dict(sorted(kind_counts.items(), key=lambda x: -x[1])),
            "top_reasons": top_reasons,
        }

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

            confirmed = decision_summary.get("count", 0)
            errors = error_rate.get("error_count", 0)
            total = confirmed + errors

            if total == 0:
                return 1.0
            return confirmed / total
        except Exception:
            return 1.0

    def get_evidence_by_finding_id(
        self,
        finding_id: str,
        event_types: list[str] | None = None
    ) -> list[EvidenceEvent]:
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
            # Iterate ring buffer directly — no query() needed for payload field access
            for event in self._log:
                if event_types and event.event_type not in event_types:
                    continue
                payload: dict[str, Any] = orjson.loads(event.payload) if event.payload else {}
                if payload.get("finding_id") == finding_id or payload.get("id") == finding_id:
                    results.append(event)
            return results
        except Exception:
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
            return {"error_count": 0, "error_rate": 0.0, "low_conf_count": 0}

        errors = self.query(event_type="error")
        low_conf_events = [e for e in self._log if e.confidence < 0.7]

        recent_errors = []
        for e in reversed(errors):
            if len(recent_errors) >= 10:
                break
            payload: dict[str, Any] = orjson.loads(e.payload) if e.payload else {}
            recent_errors.append({
                "event_id": e.event_id,
                "timestamp": datetime.fromtimestamp(e.timestamp, UTC).isoformat(),
                "message": payload.get("message", "")[:80],
                "kind": payload.get("kind", ""),
            })

        return {
            "error_count": len(errors),
            "error_rate": round(len(errors) / len(self._log) * 100, 2),
            "low_conf_count": len(low_conf_events),
            "low_conf_rate": round(len(low_conf_events) / len(self._log) * 100, 2),
            "recent_errors": recent_errors,
        }

    def get_statistics(self) -> dict[str, Any]:
        """
        Vrátí statistiky o logu - M1 8GB optimized.

        Returns:
            Dictionary se statistikami (RAM + disk)
        """
        # Spočítej typy z RAM ring bufferu
        type_counts = {et: len(indices) for et, indices in self._index_by_type.items()}
        type_counts = {k: v for k, v in type_counts.items() if v > 0}

        # Průměrná confidence z RAM
        if self._log:
            avg_confidence = sum(e.confidence for e in self._log) / len(self._log)
            timestamps = [e.timestamp for e in self._log]
            time_span = (max(timestamps) - min(timestamps)).total_seconds()
        else:
            avg_confidence = 0.0
            time_span = 0.0

        return {
            "total_events": self._total_count,
            "ram_events": self.ram_size,
            "dropped_events": self._dropped_count,
            "event_types": type_counts,
            "avg_confidence": round(avg_confidence, 4),
            "time_span_seconds": round(time_span, 2),
            "created_at": self._created_at.isoformat(),
            "is_frozen": self._frozen,
            # H4: Lifecycle truth flags
            "is_closed": self._closed,
            "is_closing": self._closing,
            "sqlite_open": self._db is not None,
            "persist_file_open": self._persist_file is not None and not self._persist_file.closed,
            "persist_path": str(self._persist_path) if self._persist_path else None,
            "persistence_enabled": self._enable_persist,
        }

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
        # ---- Compose existing helpers (primary source) ----
        funnel = self.get_event_funnel()       # event_type counts, avg_conf, pct
        decisions = self.get_decision_summary() # decision count, confidence, kinds
        errors = self.get_error_rate()          # error_count, error_rate, low_conf_*

        total = sum(v["count"] for v in funnel.values()) if funnel else 0

        # ---- 1. SPRINT POSTURE ----
        # Which event type dominates?
        if not funnel:
            posture = "empty"
        else:
            dominant = max(funnel.items(), key=lambda x: x[1]["count"])
            dominant_pct = dominant[1]["pct"]
            if dominant_pct < 40:
                posture = "balanced"
            else:
                match dominant[0]:
                    case "observation":
                        posture = "observation_heavy"
                    case "decision":
                        posture = "decision_heavy"
                    case "tool_call":
                        posture = "tool_heavy"
                    case "error":
                        posture = "error_heavy"
                    case "synthesis":
                        posture = "synthesis_heavy"
                    case _:
                        posture = f"{dominant[0]}_heavy"

        # ---- 2. QUALITY SIGNAL ----
        # Where did quality signal break? Derived from funnel avg_conf drops
        quality_breaks = []
        for et, data in funnel.items():
            if data["avg_conf"] < 0.7:
                quality_breaks.append({
                    "event_type": et,
                    "avg_conf": data["avg_conf"],
                    "count": data["count"],
                })
        quality_signal = "intact" if not quality_breaks else "degraded"

        # ---- 3. DECISION CONFIDENCE ----
        decision_conf = decisions.get("avg_confidence", 0.0)
        decision_min = decisions.get("min_confidence", 0.0)
        decision_max = decisions.get("max_confidence", 0.0)
        decision_count = decisions.get("count", 0)

        # Pressure: low-confidence decisions (conf < 0.7) as pressure signal
        # Count decisions with conf < 0.7 by looking at raw events (bounded)
        low_conf_decisions = 0
        if decision_count > 0:
            for e in self.query(event_type="decision", limit=500):
                if e.confidence < 0.7:
                    low_conf_decisions += 1

        # ---- 4. HEALTH STATUS ----
        error_rate = errors.get("error_rate", 0.0)
        low_conf_rate = errors.get("low_conf_rate", 0.0)

        if posture == "empty" or total == 0:
            health = "empty"
        else:
            match ():
                case _ if error_rate >= 20 or low_conf_rate >= 30:
                    health = "noisy"
                case _ if error_rate >= 10 or low_conf_rate >= 20:
                    health = "degraded"
                case _ if error_rate >= 5 or low_conf_rate >= 10:
                    health = "warning"
                case _:
                    health = "healthy"

        # Override to error_heavy if errors dominate funnel
        if posture == "error_heavy" and error_rate > 15:
            health = "degraded" if health == "healthy" else health

        # ---- 5. TOP WEAK SPOTS (bounded raw access) ----
        weak_spots: dict[str, int] = {}
        error_events = self.query(event_type="error", limit=100)
        for e in error_events:
            payload: dict[str, Any] = orjson.loads(e.payload) if e.payload else {}
            kind = payload.get("kind", "unknown")
            msg = payload.get("message", "")[:50]
            if msg:
                key = f"[{kind}] {msg}"
            else:
                key = f"[{kind}]"
            weak_spots[key] = weak_spots.get(key, 0) + 1

        top_weak_spots = dict(
            sorted(weak_spots.items(), key=lambda x: -x[1])[:5]
        )

        # ---- 6. RECENT HIGH-CONFIDENCE DECISIONS (last 3, conf >= 0.9) ----
        recent_high_conf_decisions = []
        for e in reversed(self.query(event_type="decision", limit=50)):
            if e.confidence >= 0.9:
                payload: dict[str, Any] = orjson.loads(e.payload) if e.payload else {}
                recent_high_conf_decisions.append({
                    "event_id": e.event_id[-12:],
                    "kind": payload.get("kind", ""),
                    "conf": e.confidence,
                    "timestamp": datetime.fromtimestamp(e.timestamp, UTC).isoformat(),
                })
                if len(recent_high_conf_decisions) >= 3:
                    break

        # ---- 7. LOW-CONFIDENCE PRESSURE ----
        _low_conf_pressure = ""
        if low_conf_decisions > 0 and decision_count > 0:
            pressure_pct = low_conf_decisions / decision_count * 100
            if pressure_pct > 30:
                _low_conf_pressure = "high"
            elif pressure_pct > 15:
                _low_conf_pressure = "moderate"
            else:
                _low_conf_pressure = "low"
        else:
            _low_conf_pressure = "none"

        return {
            # Identity
            "run_id": self._run_id,
            "total_events": total,
            "created_at": self._created_at.isoformat(),
            # Posture
            "posture": posture,
            "dominant_pct": dominant[1]["pct"] if posture not in ("empty", "balanced") else 0.0,
            # Quality signal
            "quality_signal": quality_signal,
            "quality_breaks": quality_breaks[:5],
            # Decision confidence
            "decision_count": decision_count,
            "decision_avg_conf": round(decision_conf, 4),
            "decision_conf_range": [round(decision_min, 4), round(decision_max, 4)],
            "low_conf_decisions": low_conf_decisions,
            "low_conf_pressure": _low_conf_pressure,
            # Error signal
            "error_count": errors.get("error_count", 0),
            "error_rate_pct": error_rate,
            "low_conf_count": errors.get("low_conf_count", 0),
            "low_conf_rate_pct": low_conf_rate,
            # Health
            "health": health,
            # Weak spots
            "top_weak_spots": top_weak_spots,
            # Recent high-confidence decisions
            "recent_high_conf_decisions": recent_high_conf_decisions,
        }

    @staticmethod
    def _derive_continue_reason(
        continue_or_pivot: str,
        health_status: str,
        decision_count: int,
        biggest_weakness: str,
    ) -> str:
        """Derive one-line continue reason from health signals."""
        if continue_or_pivot == "pivot":
            return "pivot: errors/errors dominate — cannot trust signal"
        if continue_or_pivot == "inspect":
            if biggest_weakness:
                return f"inspect: {biggest_weakness[:70]}"
            return f"inspect: health={health_status}, check signals"
        # continue
        if decision_count == 0:
            return "continue: no decisions made yet — gather more signal"
        return f"continue: healthy sprint with {decision_count} decisions"

    @staticmethod
    def _derive_trust_level(
        total: int,
        health_status: str,
        low_conf_pressure: str,
        error_rate: float,
    ) -> str:
        """Derive trust level enum from health signals."""
        if total < 10:
            return "low"
        if health_status == "noisy":
            return "low"
        if low_conf_pressure == "high":
            return "moderate"
        if health_status == "degraded" or error_rate > 10:
            return "moderate"
        if health_status == "warning" or error_rate > 5:
            return "moderate"
        return "high"

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
        # ---- Primary composition: sprint health ----
        health = self.get_sprint_health_summary()

        # ---- Secondary composition via health summary (no raw scan needed) ----
        # what_worked and breakdown are derived from health.get("funnel") and
        # health.get("quality_breaks") — no need to iterate raw events here.

        # What worked: event types with high avg_conf and high count
        what_worked: list[str] = []
        # F310-FIX: correct key is "funnel" not "event_funnel"
        funnel = health.get("funnel") or {}
        for et, data in funnel.items():
            if data.get("avg_conf", 0) >= 0.85 and data.get("count", 0) >= 2:
                label = f"{et} (conf={data['avg_conf']:.2f}, n={data['count']})"
                what_worked.append(label)
        what_worked = what_worked[:4]  # Cap at 4

        # Breakdown: low-conf event types and error weak spots
        breakdown: list[str] = []
        for break_item in health.get("quality_breaks", []):
            breakdown.append(
                f"{break_item['event_type']} conf={break_item['avg_conf']:.2f}"
            )
        for spot in list(health.get("top_weak_spots", {}).keys())[:3]:
            breakdown.append(f"error: {spot}")
        breakdown = breakdown[:5]  # Cap at 5

        # Biggest weakness: single most impactful issue
        biggest_weakness = ""
        weak_spots = health.get("top_weak_spots", {})
        if weak_spots:
            biggest_weakness = next(iter(weak_spots.keys()), "")
        elif breakdown:
            biggest_weakness = breakdown[0]
        else:
            # Fallback: degraded quality signal
            quality_breaks = health.get("quality_breaks", [])
            if quality_breaks:
                biggest_weakness = f"{quality_breaks[0]['event_type']} quality gap"

        # ---- Verdict: one-liner sprint characterization ----
        posture = health.get("posture", "unknown")
        total = health.get("total_events", 0)
        health_status = health.get("health", "unknown")
        error_rate = health.get("error_rate_pct", 0.0)
        decision_count = health.get("decision_count", 0)

        if total == 0:
            verdict = "empty log — no events recorded"
        else:
            match health_status:
                case "healthy":
                    verdict = f"clean sprint: {posture}, {total} events, {decision_count} decisions"
                case "warning":
                    verdict = f"warning sprint: {posture}, {total} events, {error_rate:.1f}% errors"
                case "degraded":
                    verdict = f"degraded sprint: {posture}, {total} events, {error_rate:.1f}% errors"
                case "noisy":
                    verdict = f"noisy sprint: {posture}, {total} events, {error_rate:.1f}% errors — signal hard to trust"
                case _:
                    verdict = f"{posture} sprint: {total} events, health={health_status}"

        # ---- Continue / Pivot / Inspect recommendation ----
        continue_or_pivot = "continue"
        match ():
            case _ if health_status == "noisy":
                continue_or_pivot = "pivot"
            case _ if health_status == "degraded" and error_rate > 15:
                continue_or_pivot = "pivot"
            case _ if health_status == "degraded":
                continue_or_pivot = "inspect"
            case _ if health_status == "warning":
                continue_or_pivot = "inspect"
            case _ if health.get("low_conf_pressure") == "high":
                continue_or_pivot = "inspect"
            case _ if total < 10:
                continue_or_pivot = "inspect"  # Not enough data to trust verdict

        # ---- Operator takeaway: one-line bottom line ----
        if total == 0:
            operator_takeaway = "no data — sprint not started or all events dropped"
        else:
            match health_status:
                case "healthy":
                    operator_takeaway = f"sprint healthy, {decision_count} decisions made, continue"
                case "warning":
                    operator_takeaway = f"sprint has warnings: {biggest_weakness[:60] if biggest_weakness else 'see breakdown'}"
                case "degraded":
                    operator_takeaway = f"sprint degraded: {biggest_weakness[:60] if biggest_weakness else 'errors above threshold'}"  # noqa: E501
                case "noisy":
                    operator_takeaway = f"sprint noisy: {biggest_weakness[:60] if biggest_weakness else 'too many errors to trust'}"  # noqa: E501
                case _:
                    operator_takeaway = f"sprint status={health_status}, verdict={verdict[:80]}"

        # ---- Top retro actions: 2-3 condensed items ----
        top_retro_actions: list[str] = []

        if continue_or_pivot == "pivot":
            top_retro_actions.append("pivot: root-cause errors blocking progress — investigate before continuing")
        elif continue_or_pivot == "inspect":
            if biggest_weakness:
                top_retro_actions.append(f"inspect: {biggest_weakness[:80]}")
            if error_rate > 5:
                top_retro_actions.append(f"review error_rate={error_rate:.1f}% — identify top failure modes")
            if health.get("low_conf_pressure") != "none":
                top_retro_actions.append(f"review low-conf decisions ({health.get('low_conf_pressure')} pressure)")

        if health.get("low_conf_pressure") == "high" and continue_or_pivot != "pivot":
            top_retro_actions.append("address decision confidence — >30% decisions below 0.7 conf")

        if what_worked and continue_or_pivot == "continue":
            top_retro_actions.append(f"leverage what worked: {what_worked[0][:60]}")

        # Deduplicate and cap
        seen = set()
        deduped = []
        for a in top_retro_actions:
            normalized = a[:60]
            if normalized not in seen:
                seen.add(normalized)
                deduped.append(a)
        top_retro_actions = deduped[:3]

        # ---- Health confidence note ----
        if total < 10:
            _health_confidence_note = f"low confidence: only {total} events — treat verdict as indicative"
        else:
            match health_status:
                case "noisy":
                    _health_confidence_note = "low confidence: error_rate >20% — signal integrity compromised"
                case _ if health.get("low_conf_pressure") == "high":
                    _health_confidence_note = "moderate confidence: high low-conf decision pressure"
                case _:
                    _health_confidence_note = "confident verdict: sufficient data and low noise"

        return {
            # Identity
            "run_id": self._run_id,
            "total_events": total,
            # Sprint character
            "verdict": verdict,
            "posture": posture,
            "health": health_status,
            # Breakdown
            "breakdown": breakdown,
            "what_worked": what_worked,
            "biggest_weakness": biggest_weakness,
            # Recommendation
            "continue_or_pivot": continue_or_pivot,
            # Operator-facing
            "operator_takeaway": operator_takeaway,
            "top_retro_actions": top_retro_actions,
            "health_confidence_note": _health_confidence_note,
            # Compact operator retrospective delta (Sprint F150H)
            "operator_retro_brief": operator_takeaway,  # canonical one-liner
            "continue_reason": self._derive_continue_reason(continue_or_pivot, health_status, decision_count, biggest_weakness),  # noqa: E501
            "trust_level": self._derive_trust_level(total, health_status, health.get("low_conf_pressure", "none"), error_rate),  # noqa: E501
            "biggest_win": what_worked[0] if what_worked else "",
            "retro_priority": top_retro_actions[0] if top_retro_actions else "",
            # Underlying signals (for deep dive)
            "_health": health,
        }

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

            # Nejprve zpracuj zdroje (rekurzivně)
            for source_id in event.source_ids:
                traverse(source_id)

            # Pak přidej aktuální událost
            chain.append(event)

        traverse(event_id)
        return chain

