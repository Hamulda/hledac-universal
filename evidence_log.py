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
from core.env_config import ENV
from hledac.universal.utils.async_helpers import safe_create_task, safe_wait_for
_arrow = None

def _get_arrow():
    """Lazy Arrow IPC loader — only loads pyarrow if HLEDAC_ARROW_EVIDENCE=1."""
    global _arrow
    if _arrow is None:
        import os as _os
        if _os.environ.get('HLEDAC_ARROW_EVIDENCE', '0') == '1':
            try:
                import pyarrow as _pa
                import pyarrow.ipc as _ipc
                _arrow = (_pa, _ipc)
            except ImportError:
                logger.debug('[Arrow] pyarrow not available, falling back to SQLite')
                _arrow = False
        else:
            _arrow = False
    return _arrow if _arrow else None
try:
    from utils.flow_trace import is_enabled, trace_counter, trace_evidence_append, trace_evidence_flush, trace_queue_drop
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
logger = logging.getLogger(__name__)

class EvidenceEvent(msgspec.Struct, frozen=False):
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
        encoded_payload = orjson.dumps(payload)
        content_hash = cls._calculate_hash(event_id=event_id, event_type=event_type, timestamp=timestamp, payload=payload, source_ids=source_ids, confidence=confidence, run_id=run_id)
        return cls(event_id=event_id, event_type=event_type, timestamp=timestamp, payload=encoded_payload, source_ids=source_ids, confidence=confidence, content_hash=content_hash, run_id=run_id, seq_no=seq_no, prev_chain_hash=prev_chain_hash, chain_hash=None)

    @staticmethod
    def _calculate_hash(event_id: str, event_type: str, timestamp: float, payload: dict[str, Any], source_ids: list[str], confidence: float, run_id: str) -> str:
        """Calculate SHA-256 hash of normalized event content."""
        data = {'event_id': event_id, 'event_type': event_type, 'timestamp': timestamp, 'payload': _normalize_payload(payload), 'source_ids': sorted(source_ids), 'confidence': round(confidence, 6), 'run_id': run_id}
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
        """Deserialize event from msgspec bytes."""
        decoded = msgspec.msgpack.decode(data)
        return cls(*decoded)

def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize payload for consistent hashing.

    P3-4: Handles msgspec.Struct nested in payload dicts — converts via
    msgspec.to_builtins() for consistent dict representation before hashing.
    """
    normalized = {}
    for key in sorted(payload.keys()):
        value = payload[key]
        if isinstance(value, datetime):
            normalized[key] = value.isoformat()
        elif isinstance(value, msgspec.Struct):
            # P3-4: msgspec.Struct → dict via to_builtins() (bytes→base64)
            normalized[key] = _normalize_value(value)
        elif isinstance(value, (list, tuple)):
            normalized[key] = [_normalize_value(v) for v in value]
        elif isinstance(value, dict):
            normalized[key] = _normalize_payload(value)
        else:
            normalized[key] = _normalize_value(value)
    return normalized

def _normalize_value(value: Any) -> Any:
    """Normalize individual value.

    P3-4: msgspec.Struct instances are converted via msgspec.to_builtins()
    for consistent representation in payload hashing.
    """
    if isinstance(value, float):
        return round(value, 6)
    elif isinstance(value, (set, frozenset)):
        return sorted(value)
    elif isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    elif isinstance(value, msgspec.Struct):
        # P3-4: msgspec.Struct → dict for hashing (e.g. CycleResult in payload)
        return msgspec.to_builtins(value)
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
    """
    __slots__ = tuple(('_impl', '_pool', '_queue', '_sender_ptr', '_wake_fd', 'fallback'))

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
        except Exception:
            self._pool = None
            self._sender_ptr = 0
            self._wake_fd = -1
            if asyncio_fallback:
                self._queue = asyncio.Queue(maxsize=capacity)
            else:
                self._queue = None
            self.fallback = True
            self._impl = 'asyncio'

    def send(self, item: bytes) -> bool:
        """Send raw bytes to the pool. Non-blocking (Rust) or blocking (asyncio).

        ISSUE-006: bytes-only — serialization is caller's responsibility.
        This eliminates the redundant orjson.dumps() that _RustMPSC did internally.
        """
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

    async def send_async(self, item: bytes) -> bool:
        """Async send — blocks if queue is full (used by worker)."""
        if self._impl == 'rust' and self._pool is not None:
            return self.send(item)
        elif self._queue is not None:
            try:
                self._queue.put_nowait(item)
                return True
            except asyncio.QueueFull:
                return False
        return False

    def recv_batch(self, max_items: int | None=None) -> list[bytes]:
        """Drain up to max_items as raw bytes (non-blocking).

        ISSUE-006: Returns bytes directly — caller deserializes only when needed.
        SQLite path now uses _flush_batch_bytes() for zero-copy BLOB insert.
        """
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
    __slots__ = ('_run_id', '_log', '_index_by_type', '_index_by_source', '_created_at', '_frozen', '_closed', '_total_count', '_dropped_count', '_seq', '_chain_head', '_genesis_hash', '_encrypt_at_rest', '_encryption_key', '_cipher', '_enable_persist', '_persist_path', '_persist_file', '_persist_path_str', '_mpsc', '_mpsc2', '_flush_task', '_async_write_queue', '_async_write_task', '_mpsc2_reader', '_db_path', '_db', '_initialized', '_arrow_path', '_arrow_writer', '_arrow_schema', '_closing', '_manifest_dirty', '_flush_shutdown', '_async_write_shutdown', '_loop', '_silent_failure', '_sample_rate')
    MAX_RAM_EVENTS = 50
    MAX_PAYLOAD_PREVIEW = 200
    JSONL_ROTATE_SIZE = 10 * 1024 * 1024
    _FSYNC_EVERY_N_EVENTS = 25
    _MANIFEST_EVERY_N_EVENTS = 100
    _SQLITE_BATCH_SIZE = 500
    _SQLITE_FLUSH_INTERVAL = 1.5
    _ASYNC_WRITE_QUEUE_MAXSIZE = 500

    def __init__(self, run_id: str, persist_path: Path | None=None, enable_persist: bool=True, encrypt_at_rest: bool=False, silent_failure: bool=False, sample_rate: float=1.0):
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
            logger.info('[ENCRYPT] enabled=True target=evidence')
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
                logger.debug(f'EvidenceLog persistence: {self._persist_path}')
            except Exception as e:
                logger.error(f'Failed to open evidence log: {e}')
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
        self._flush_shutdown: asyncio.Event = asyncio.Event()
        self._async_write_shutdown: asyncio.Event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

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
            except Exception:
                pass
            self._arrow_writer = None
        if self._persist_file and (not self._persist_file.closed):
            try:
                self._persist_file.close()
            except Exception:
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
            except Exception:
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
        except Exception as _mig_err:
            logger.warning(f'[F11C] Migration from JSONL failed (non-fatal): {_mig_err}')
        try:
            self._flush_task = safe_create_task(self._flush_worker())
        except Exception as _task_err:
            logger.warning(f'[F11C] Flush worker task creation failed (non-fatal): {_task_err}')
            self._flush_task = None
        try:
            self._async_write_task = safe_create_task(self._async_write_worker())
        except Exception as _write_task_err:
            logger.warning(f'[F290] Async write worker task creation failed (non-fatal): {_write_task_err}')
            self._async_write_task = None
        self._initialized = True

    async def _init_db(self) -> None:
        """Initialize SQLite database with WAL mode."""
        if self._db_path is None:
            from hledac.universal.paths import EVIDENCE_ROOT
            evidence_dir = EVIDENCE_ROOT
            evidence_dir.mkdir(parents=True, exist_ok=True)
            self._db_path = evidence_dir / f'{self._run_id}.db'
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
        except Exception:
            pass
        await self._db.execute('\n            CREATE TABLE IF NOT EXISTS events (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                timestamp REAL NOT NULL,\n                event_type TEXT NOT NULL,\n                data TEXT NOT NULL,\n                hash TEXT NOT NULL\n            )\n        ')
        await self._db.commit()
        arrow_loader = _get_arrow()
        if arrow_loader:
            pa, ipc = arrow_loader
            from hledac.universal.paths import EVIDENCE_ROOT
            evidence_dir = EVIDENCE_ROOT
            self._arrow_path = evidence_dir / f'{self._run_id}.arrow'
            self._arrow_schema = pa.schema([('timestamp', pa.float64()), ('event_type', pa.string()), ('data', pa.string()), ('hash', pa.string())])
            self._arrow_writer = ipc.new_file(str(self._arrow_path), self._arrow_schema)
            logger.info(f'[Arrow] IPC enabled: {self._arrow_path}')

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
                        await self._db.execute('INSERT INTO events (timestamp, event_type, data, hash) VALUES (?, ?, ?, ?)', (timestamp, event_type, event_data, content_hash))
                await self._db.commit()
            except Exception:
                await self._db.rollback()
                if migrated_file.exists():
                    migrated_file.unlink()
                raise
            old_file.rename(migrated_file)
            logger.info(f'Migrated {self._run_id} events to SQLite')
        except Exception as e:
            logger.warning(f'Migration failed: {e}')

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
        while True:
            try:
                async with asyncio.timeout(1.0):
                    received = self._mpsc.recv_batch(max_items=None)
                    if received:
                        batch.extend(received)
            except TimeoutError:
                pass
            if self._flush_shutdown.is_set():
                break
            if len(batch) >= self._SQLITE_BATCH_SIZE or (batch and (datetime.now(UTC) - last_flush).total_seconds() >= self._SQLITE_FLUSH_INTERVAL):
                flush_start = time.perf_counter()
                try:
                    await self._flush_batch_bytes(batch)
                    flush_latency_ms = (time.perf_counter() - flush_start) * 1000
                    trace_evidence_flush(len(batch), flush_latency_ms, 'ok', len(batch))
                except Exception as _flush_err:
                    flush_latency_ms = (time.perf_counter() - flush_start) * 1000
                    logger.warning(f'Flush batch failed (dropping {len(batch)} events): {_flush_err}')
                    trace_evidence_flush(len(batch), flush_latency_ms, 'flush_error', 0)
                batch = []
                last_flush = datetime.now(UTC)
        remaining = self._mpsc.recv_batch(max_items=None)
        if remaining:
            batch.extend(remaining)
        if batch and self._db is not None:
            flush_start = time.perf_counter()
            await self._flush_batch_bytes(batch)
            flush_latency_ms = (time.perf_counter() - flush_start) * 1000
            trace_evidence_flush(len(batch), flush_latency_ms, 'ok', len(batch))

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
        except Exception as _open_err:
            logger.warning(f'[F290] aiofiles open failed, using sync fallback: {_open_err}')
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
                except Exception:
                    # Fallback: sync write each item individually
                    try:
                        with open(cast(str, self._persist_path_str), 'ab') as _sf:
                            for _data in _write_buf:
                                _sf.write(_data)
                    except Exception:
                        pass
            else:
                # Batch write: join all data and write in one syscall
                try:
                    with open(cast(str, self._persist_path_str), 'ab') as _sf:
                        _sf.write(b''.join(_write_buf))
                except Exception:
                    pass
            _write_buf.clear()
        while True:
            if self._mpsc2.fallback:
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
                    await _flush_buf()
            if not self._mpsc2.fallback and (not self._mpsc2.is_empty()):
                continue
        remaining = self._mpsc2.recv_batch(max_items=None)
        if remaining:
            _write_buf.extend(remaining)
        if _write_buf:
            await _flush_buf()
        if _afile is not None:
            try:
                await _afile.close()
            except Exception:
                pass
    _ARROW_SUB_BATCH = 256

    async def _flush_batch_bytes(self, batch: list[bytes]) -> None:
        """Flush a batch of bytes directly to SQLite BLOB (zero-copy).

        ISSUE-006: New method for zero-copy path.
        - Takes bytes directly from recv_batch()
        - Inserts into SQLite as BLOB without re-serialization
        - Falls back to _flush_batch() for legacy TEXT data or Arrow IPC
        """
        if not batch:
            return
        arrow_loader = _get_arrow()
        if arrow_loader and self._arrow_writer is not None:
            pa, _ = arrow_loader
            try:
                # Decode batch for Arrow IPC (Arrow needs string columns)
                decoded_batch = []
                for b in batch:
                    try:
                        decoded_batch.append(msgspec.msgpack.decode(b))
                    except Exception:
                        continue
                if not decoded_batch:
                    return
                for i in range(0, len(decoded_batch), self._ARROW_SUB_BATCH):
                    sub = decoded_batch[i:i + self._ARROW_SUB_BATCH]
                    arrays = [
                        pa.array([e.get('timestamp', datetime.now(UTC).timestamp()) for e in sub], type=pa.float64()),
                        pa.array([e.get('event_type', 'unknown') for e in sub], type=pa.string()),
                        pa.array([orjson.dumps(e.get('data', {})).decode() for e in sub], type=pa.string()),
                        pa.array([e.get('content_hash', '') for e in sub], type=pa.string())
                    ]
                    batch_arrow = pa.record_batch(arrays, schema=self._arrow_schema)
                    self._arrow_writer.write_batch(batch_arrow)
                return
            except Exception as e:
                logger.warning(f'[Arrow] IPC write failed, falling back to SQLite: {e}')

        # SQLite BLOB path — zero-copy insert
        # Each bytes item is: msgspec encoded EvidenceEvent
        # Schema: (timestamp REAL, event_type TEXT, data BLOB, hash TEXT)
        records: list[tuple[float, str, bytes, str]] = []
        for b in batch:
            try:
                event = msgspec.msgpack.decode(b)
                # event is a tuple: (event_id, event_type, timestamp, payload, source_ids,
                #                     confidence, content_hash, run_id, seq_no, prev_chain_hash, chain_hash)
                timestamp = event[2] if len(event) > 2 else datetime.now(UTC).timestamp()
                event_type = event[1] if len(event) > 1 else 'unknown'
                payload_bytes = event[3] if len(event) > 3 else b''
                content_hash = event[6] if len(event) > 6 else ''
                records.append((timestamp, event_type, payload_bytes, content_hash))
            except Exception:
                # Legacy format or decode error — skip or insert raw
                continue

        db = self._db
        if db is None:
            return
        if not hasattr(db, 'executemany'):
            logger.warning('EvidenceLog._db not initialized as aiosqlite.Connection')
            return

        # ISSUE-006: BLOB insert — data is stored as BLOB, not TEXT
        # This eliminates the orjson.dumps() decode/re-encode cycle
        try:
            await db.executemany(
                'INSERT INTO events (timestamp, event_type, data, hash) VALUES (?, ?, ?, ?)',
                records
            )
            await db.commit()
        except Exception as e:
            logger.warning(f'[_flush_batch_bytes] BLOB insert failed, falling back: {e}')
            # Fallback: encode as TEXT
            text_records = []
            for b in batch:
                try:
                    event = msgspec.msgpack.decode(b)
                    timestamp = event[2] if len(event) > 2 else datetime.now(UTC).timestamp()
                    event_type = event[1] if len(event) > 1 else 'unknown'
                    data_str = orjson.dumps(event).decode()
                    content_hash = event[6] if len(event) > 6 else ''
                    text_records.append((timestamp, event_type, data_str, content_hash))
                except Exception:
                    continue
            if text_records:
                await db.executemany(
                    'INSERT INTO events (timestamp, event_type, data, hash) VALUES (?, ?, ?, ?)',
                    text_records
                )
                await db.commit()

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
                    arrays = [pa.array([e.get('timestamp', datetime.now(UTC).timestamp()) for e in sub], type=pa.float64()), pa.array([e.get('event_type', 'unknown') for e in sub], type=pa.string()), pa.array([orjson.dumps(e.get('data', {})).decode() for e in sub], type=pa.string()), pa.array([e.get('content_hash', '') for e in sub], type=pa.string())]
                    batch_arrow = pa.record_batch(arrays, schema=self._arrow_schema)
                    self._arrow_writer.write_batch(batch_arrow)
                return
            except Exception as e:
                logger.warning(f'[Arrow] IPC write failed, falling back to SQLite: {e}')
        records = []
        for event_data in batch:
            timestamp = event_data.get('timestamp', datetime.now(UTC).timestamp())
            event_type = event_data.get('event_type', 'unknown')
            data = orjson.dumps(event_data).decode()
            content_hash = event_data.get('content_hash', '')
            records.append((timestamp, event_type, data, content_hash))
        db = self._db
        if db is None:
            return
        if not hasattr(db, 'executemany'):
            logger.warning('EvidenceLog._db not initialized as aiosqlite.Connection')
            return
        await db.executemany('INSERT INTO events (timestamp, event_type, data, hash) VALUES (?, ?, ?, ?)', records)
        await db.commit()

    def _init_encryption(self):
        """Initialize encryption cipher."""
        if not self._encryption_key:
            self._encryption_key = secrets.token_bytes(32)
            logger.warning('[ENCRYPT] No ENCRYPTION_KEY env - using temporary key')
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            self._cipher = (Cipher, algorithms, modes)
        except ImportError:
            logger.warning('[ENCRYPT] cryptography not available, encryption disabled')
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
                logger.warning('MPSCPool full, falling back to direct sync write')
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
                    conn.execute('INSERT INTO events (timestamp, event_type, data, hash) VALUES (?, ?, ?, ?)', (_event_dict.get('timestamp', 0.0), _event_dict.get('event_type', 'unknown'), orjson.dumps(_event_dict).decode(), _event_dict.get('content_hash', '')))
                    conn.commit()
                    conn.close()
                t = threading.Thread(target=_sync_insert, daemon=True)
                t.start()
                trace_evidence_append(event.event_type, 0, 'sync_sqlite')
            except Exception as _sync_err:
                logger.debug(f'[F11C] Sync SQLite fallback failed (non-fatal): {_sync_err}')
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
                        logger.debug(f'[ENCRYPT] stored bytes_in={len(line)} bytes_out={len(bytes_to_write)}')
                    except Exception as e:
                        logger.warning(f'[ENCRYPT] failed: {e}')
                _sent = self._mpsc2.send(bytes_to_write)
                if not _sent:
                    self._sync_write_fallback(line, bytes_to_write)
            except Exception as e:
                logger.critical(f'[F286] SWAL write failed (FATAL): {e}')
                raise RuntimeError(f'EvidenceLog SWAL write failed: {e}') from e
        decoded_payload = orjson.loads(event.payload)
        trimmed_payload = self._trim_payload(decoded_payload)
        event.payload = orjson.dumps(trimmed_payload)
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
            except Exception:
                pass
            return
        index = len(self._log) - 1
        self._index_by_type[event.event_type].append(index)
        for source_id in event.source_ids:
            if source_id not in self._index_by_source:
                self._index_by_source[source_id] = deque(maxlen=self.MAX_RAM_EVENTS)
            self._index_by_source[source_id].append(index)
        try:
            from knowledge.analytics_hook import shadow_record_finding
            if event.event_type == 'evidence_packet':
                payload: dict[str, Any] = orjson.loads(event.payload) if event.payload else {}
                _corr: dict[str, Any] | None = payload.get('_correlation')
                shadow_record_finding(finding_id=event.event_id, query=payload.get('query', ''), source_type='evidence_packet', confidence=event.confidence, run_id=event.run_id, url=payload.get('url'), title=payload.get('title'), source=payload.get('source'), relevance_score=payload.get('relevance_score'), branch_id=_corr.get('branch_id') if _corr else None, provider_id=_corr.get('provider_id') if _corr else None, action_id=_corr.get('action_id') if _corr else None)
        except Exception:
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

        for event_type, payload, source_ids, confidence in events:
            if event_type != "error" and self._sample_rate < 1.0:
                if _random.random() > self._sample_rate:
                    continue

            event_id = f"{self._run_id}_{uuid.uuid4().hex[:12]}"
            event = EvidenceEvent(
                event_id=event_id,
                event_type=event_type,
                timestamp=datetime.now(UTC).timestamp(),
                payload=orjson.dumps(payload),
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
                except Exception:
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

        # Batch MPSC send — single Python→Rust call
        _worker_alive = (
            self._initialized
            and self._flush_task is not None
            and (not self._flush_task.done())
        )
        if _worker_alive and (not self._closing):
            _mpsc_payloads = [e.to_bytes() for e in created]
            _sent = self._mpsc.send_batch(_mpsc_payloads)
            for e in created:
                trace_evidence_append(e.event_type, self._mpsc.len(), 'queued')
            if _sent < len(created):
                logger.warning(f'ISSUE-007 MPSCPool full: sent {_sent}/{len(created)}')
                trace_queue_drop('mpsc_batch', len(created) - _sent)

        # Batch SWAL send — single send_batch call for JSONL persist
        if self._enable_persist:
            _jsonl_payloads: list[bytes] = []
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
                    except Exception as _enc_err:
                        logger.warning(f'[ENCRYPT] batch failed: {_enc_err}')
                _jsonl_payloads.append(bytes_to_write)
            try:
                _sent2 = self._mpsc2.send_batch(_jsonl_payloads)
                if _sent2 < len(created):
                    for e in created[_sent2:]:
                        line = e.to_jsonl_line()
                        bytes_to_write = line.encode('utf-8') + b'\n'
                        self._sync_write_fallback(line, bytes_to_write)
            except Exception as _swal_err:
                logger.critical(f'[F286] SWAL batch send failed: {_swal_err}')

        # analytics_hook per event (only evidence_packet type)
        try:
            from knowledge.analytics_hook import shadow_record_finding

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
        except Exception:
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
            logger.warning('[FORENSIC] attach_forensic_analysis called with empty finding_id')
            return None
        if forensic_result is None:
            logger.debug(f'[FORENSIC] attach_forensic_analysis: no forensic_result for {finding_id}')
            return None
        if not isinstance(forensic_result, dict):
            logger.warning(f'[FORENSIC] attach_forensic_analysis: forensic_result must be dict, got {type(forensic_result).__name__} for {finding_id}')
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
            logger.warning(f'[FORENSIC] attach_forensic_analysis failed for {finding_id}: {exc}')
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
            logger.warning(f"[DECISION] Invalid kind={kind}, using 'drift'")
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

    def get_summary(self, last_n: int=10) -> str:
        """
        Vytvoří shrnutí logu pro Hermes.

        Vrací stručné shrnutí posledních N událostí - ne celý raw log.

        Args:
            last_n: Počet posledních událostí k zahrnutí

        Returns:
            Formátovaný string shrnutí
        """
        lines = ['=' * 60, 'EVIDENCE LOG SUMMARY', '=' * 60, '', f'Run ID: {self._run_id}', f'Total Events: {self.size}', f'Created: {self._created_at.isoformat()}', '', 'Event Counts by Type:']
        for event_type, indices in self._index_by_type.items():
            count = len(indices)
            if count > 0:
                lines.append(f'  {event_type}: {count}')
        lines.extend(['', '-' * 40, f'Last {last_n} Events (newest first):', '-' * 40])
        recent_events = list(self._log)[-last_n:] if len(self._log) >= last_n else list(self._log)
        recent_events = list(reversed(recent_events))
        for i, event in enumerate(recent_events, 1):
            timestamp = datetime.fromtimestamp(event.timestamp, UTC).strftime('%H:%M:%S')
            payload_summary = self._summarize_payload(orjson.loads(event.payload) if event.payload else {})
            lines.append(f'{i}. [{timestamp}] {event.event_type.upper()} (conf: {event.confidence:.2f})')
            lines.append(f'   {payload_summary}')
            if event.source_ids:
                sources_str = ', '.join(event.source_ids[:3])
                if len(event.source_ids) > 3:
                    sources_str += f' (+{len(event.source_ids) - 3} more)'
                lines.append(f'   Sources: {sources_str}')
            lines.append('')
        lines.extend(['=' * 60])
        return '\n'.join(lines)

    def _summarize_payload(self, payload: dict[str, Any], max_length: int=60) -> str:
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
        total_lines = 0
        with open(path, encoding='utf-8') as f:
            for _ in f:
                total_lines += 1
        log._total_count = total_lines
        with open(path, encoding='utf-8') as f:
            lines = f.readlines()
            if not load_to_ram and len(lines) > max_ram_events:
                lines = lines[-max_ram_events:]
                log._dropped_count = total_lines - len(lines)
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                data = orjson.loads(line)
                event = EvidenceEvent.from_dict(data)
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
            logger.warning('Cannot write manifest: no persist_path set')
            return None
        manifest = {'run_id': self._run_id, 'chain_head': self._chain_head, 'total_count': self._total_count, 'created_at': self._created_at.isoformat(), 'last_seq_no': self._seq, 'persist_path': str(self._persist_path), 'genesis_hash': self._genesis_hash}
        manifest_path = self._persist_path.with_suffix('.manifest.json')
        try:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(manifest_path, 'wb') as f:
                f.write(orjson.dumps(manifest, option=orjson.OPT_INDENT_2))
            logger.info(f'[EVIDENCE] Manifest written: {manifest_path}')
            return manifest_path
        except Exception as e:
            logger.error(f'Failed to write manifest: {e}')
            return None

    async def aclose(self) -> None:
        """
        Async cleanup: shutdown flush worker, close SQLite, close persist file.

        This is the canonical async cleanup path. All resources are closed
        in order with proper shutdown signaling.

        Idempotent: safe to call multiple times.
        """
        if self._closed:
            return
        self._closing = True
        if self._flush_shutdown:
            self._flush_shutdown.set()
        if self._flush_task:
            try:
                await safe_wait_for(self._flush_task, timeout=10.0, label='_flush_task')
            except TimeoutError:
                logger.warning('Flush worker did not exit in 10s, cancelling')
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
                logger.warning('Async write worker did not exit in 5s, cancelling')
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
                logger.info(f'[Arrow] IPC writer closed: {self._arrow_path}')
            except Exception as e:
                logger.warning(f'[Arrow] Failed to close writer: {e}')
            finally:
                self._arrow_writer = None
        if drained and self._db is not None:
            try:
                await self._flush_batch_bytes(drained)
            except Exception as e:
                logger.warning(f'Failed to flush remaining items: {e}')
        if mpsc2_drained and self._persist_file:
            try:
                # Write remaining mpsc2 items (JSONL path) before closing
                _combined = b''.join(mpsc2_drained)
                self._persist_file.write(_combined)
                self._persist_file.flush()
            except Exception as e:
                logger.warning(f'Failed to flush mpsc2 remaining items: {e}')
        if self._db is not None:
            try:
                await self._db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                await self._db.close()
            except Exception as e:
                logger.warning(f'Failed to close SQLite: {e}')
            finally:
                self._db = None
        self._close_persist_file()
        self._closed = True
        self._closing = False
        self.freeze()
        logger.debug(f'[EVIDENCE] aclose complete: run_id={self._run_id}')

    def _close_persist_file(self) -> None:
        """Close persist file with idempotency guard (runs in thread)."""
        if self._persist_file and (not self._persist_file.closed):
            try:
                self._persist_file.flush()
                os.fsync(self._persist_file.fileno())
                self._persist_file.close()
            except Exception as e:
                logger.warning(f'Failed to close persist file: {e}')
            finally:
                self._persist_file = None
        elif self._persist_file is not None:
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
                future = asyncio.run_coroutine_threadsafe(self.aclose(), stored_loop)
                future.result()
            else:
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
        self.write_manifest()
        self.close()
        self.freeze()
        logger.info(f'[EVIDENCE] Log finalized: run_id={self._run_id}, events={self._total_count}, chain_head={self._chain_head[:16]}...')

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
        except Exception:
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