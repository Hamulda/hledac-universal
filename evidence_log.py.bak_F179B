"""
EvidenceLog - Append-only log pro autonomní výzkum
===================================================

Tento modul implementuje append-only log pro ukládání důkazů
během autonomního výzkumu. Podporuje verifikaci pomocí hashů,
JSONL export pro replay mode a dotazování.

M1 8GB Optimalizace:
- Ring buffer v RAM (max 100 událostí)
- Append-only JSONL persistencer na disk
- Trimmované payloady (žádné fulltexty)
- Automatická rotace logů
"""

from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set

from pydantic import BaseModel, Field, field_validator

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
    from .utils.flow_trace import (
        trace_evidence_append, trace_evidence_flush, trace_queue_drop,
        trace_counter, is_enabled,
    )
except ImportError:
    # Fallback if flow_trace not available
    def trace_evidence_append(*args, **kwargs): pass
    def trace_evidence_flush(*args, **kwargs): pass
    def trace_queue_drop(*args, **kwargs): pass
    def trace_counter(*args, **kwargs): pass
    def is_enabled(): return False

logger = logging.getLogger(__name__)


class EvidenceEvent(BaseModel):
    """
    Událost v evidence logu.

    Každá událost má unikátní ID, typ, timestamp, payload
    a content hash pro verifikaci integrity.
    """

    event_id: str = Field(..., description="Unikátní ID události")
    event_type: Literal["tool_call", "observation", "synthesis", "error", "decision", "evidence_packet"] = Field(
        ..., description="Typ události"
    )
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    payload: Dict[str, Any] = Field(default_factory=dict, description="Data události")
    source_ids: List[str] = Field(default_factory=list, description="ID zdrojových událostí")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Spolehlivost 0-1")
    content_hash: str = Field(..., description="SHA-256 hash pro verifikaci")
    run_id: str = Field(..., description="ID běhu výzkumu")
    # Tamper-evident hash-chain fields (optional for backward compatibility with legacy JSONL)
    seq_no: int = Field(default=0, description="Sequence number in chain")
    prev_chain_hash: Optional[str] = Field(default=None, description="Previous event's chain hash")
    chain_hash: Optional[str] = Field(default=None, description="Chain hash for tamper detection")

    @field_validator('source_ids', mode='before')
    @classmethod
    def ensure_list(cls, v):
        if v is None:
            return []
        return v

    def calculate_hash(self) -> str:
        """Vypočítá SHA-256 hash obsahu události"""
        # Vytvoř serializovatelnou reprezentaci
        data = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "payload": self._normalize_payload(self.payload),
            "source_ids": sorted(self.source_ids),
            "confidence": round(self.confidence, 6),  # Zaokrouhlení pro konzistenci
            "run_id": self.run_id,
        }
        # Serializuj do JSON s konzistentním řazením klíčů
        json_str = json.dumps(data, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(json_str.encode('utf-8')).hexdigest()

    def _normalize_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalizuje payload pro konzistentní hashování"""
        normalized = {}
        for key in sorted(payload.keys()):
            value = payload[key]
            if isinstance(value, datetime):
                normalized[key] = value.isoformat()
            elif isinstance(value, (list, tuple)):
                normalized[key] = [self._normalize_value(v) for v in value]
            elif isinstance(value, dict):
                normalized[key] = self._normalize_payload(value)
            else:
                normalized[key] = self._normalize_value(value)
        return normalized

    def _normalize_value(self, value: Any) -> Any:
        """Normalizuje jednotlivou hodnotu"""
        if isinstance(value, float):
            return round(value, 6)  # Zaokrouhlení floatů
        elif isinstance(value, (set, frozenset)):
            return sorted(list(value))
        elif isinstance(value, bytes):
            return value.decode('utf-8', errors='replace')
        return value

    def verify_integrity(self) -> bool:
        """Ověří integritu události pomocí content hash"""
        calculated = self.calculate_hash()
        return calculated == self.content_hash

    def to_dict(self) -> Dict[str, Any]:
        """Převede událost na dictionary"""
        result = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
            "source_ids": self.source_ids,
            "confidence": self.confidence,
            "content_hash": self.content_hash,
            "run_id": self.run_id,
        }
        # Include chain fields only if set (backward compatibility)
        if self.seq_no > 0:
            result["seq_no"] = self.seq_no
        if self.prev_chain_hash:
            result["prev_chain_hash"] = self.prev_chain_hash
        if self.chain_hash:
            result["chain_hash"] = self.chain_hash
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceEvent":
        """Vytvoří událost z dictionary"""
        # Převeď timestamp string na datetime
        if isinstance(data.get("timestamp"), str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)

    def to_jsonl_line(self) -> str:
        """Převede událost na JSONL řádek"""
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(',', ':'))


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

    # M1 8GB RAM hard limity
    MAX_RAM_EVENTS = 100  # Ring buffer size
    MAX_PAYLOAD_PREVIEW = 200  # Max chars v payload preview
    JSONL_ROTATE_SIZE = 10 * 1024 * 1024  # 10MB rotace

    # Internal constant for fsync batching (no user toggle)
    # fsync every N events to avoid per-event IO bottleneck
    _FSYNC_EVERY_N_EVENTS = 25

    # SQLite batching constants
    _SQLITE_BATCH_SIZE = 50
    _SQLITE_FLUSH_INTERVAL = 0.5  # seconds

    def __init__(
        self,
        run_id: str,
        persist_path: Optional[Path] = None,
        enable_persist: bool = True,
        encrypt_at_rest: bool = False
    ):
        """
        Inicializuje EvidenceLog.

        Args:
            run_id: Unikátní ID běhu výzkumu
            persist_path: Cesta pro JSONL persistenci (None = auto)
            enable_persist: Zda povolit persistenci na disk
            encrypt_at_rest: Zda šifrovat data na disku
        """
        import os

        self._run_id: str = run_id
        self._log: deque = deque(maxlen=self.MAX_RAM_EVENTS)  # Ring buffer (max MAX_RAM_EVENTS)
        self._index_by_type: Dict[str, List[int]] = {
            "tool_call": [],
            "observation": [],
            "synthesis": [],
            "error": [],
            "decision": [],
            "evidence_packet": [],
        }
        self._index_by_source: Dict[str, List[int]] = {}
        self._created_at: datetime = datetime.utcnow()
        self._frozen: bool = False
        self._closed: bool = False  # H1: closed flag for post-close guards
        self._total_count: int = 0  # Celkový počet událostí (včetně na disku)
        self._dropped_count: int = 0  # Počet vyřazených z ring bufferu
        self._fsync_counter: int = 0  # Counter for fsync batching

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
        self._persist_path: Optional[Path] = None
        self._persist_file = None

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
                self._persist_file = open(
                    self._persist_path, 'ab' if self._encrypt_at_rest else 'a',
                    encoding='utf-8' if not self._encrypt_at_rest else None,
                    buffering=8192
                )
                logger.debug(f"EvidenceLog persistence: {self._persist_path}")
            except Exception as e:
                logger.error(f"Failed to open evidence log: {e}")
                self._enable_persist = False

        # SQLite async batching components
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._flush_task: Optional[asyncio.Task] = None
        self._db_path: Optional[Path] = None
        self._db: Optional[sqlite3.Connection] = None
        self._initialized = False
        self._closing = False  # Flag: aclose in progress, block queue access

    def __del__(self):
        """Cleanup - zavři persist file."""
        if self._persist_file and not self._persist_file.closed:
            try:
                self._persist_file.close()
            except Exception:
                pass

    async def initialize(self) -> None:
        """
        Initialize async SQLite components.

        Creates database, migrates from old JSONL file if exists,
        and starts background flush worker.
        """
        if self._initialized:
            return

        # Run in event loop thread — sqlite3.connect is I/O-bound, not CPU-bound,
        # and _db must be created in the same thread where it's used (event loop).
        self._init_db()
        self._migrate_from_file()
        self._flush_task = asyncio.create_task(self._flush_worker())
        self._initialized = True

    def _init_db(self) -> None:
        """Initialize SQLite database with WAL mode."""
        if self._db_path is None:
            from hledac.universal.paths import EVIDENCE_ROOT
            evidence_dir = EVIDENCE_ROOT
            evidence_dir.mkdir(parents=True, exist_ok=True)
            self._db_path = evidence_dir / f"{self._run_id}.db"

        self._db = sqlite3.connect(str(self._db_path))
        self._db.execute("PRAGMA journal_mode=WAL")

        self._db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                event_type TEXT NOT NULL,
                data TEXT NOT NULL,
                hash TEXT NOT NULL
            )
        """)
        self._db.commit()

    def _migrate_from_file(self) -> None:
        """Migrate events from old JSONL file if exists."""
        if self._persist_path is None or not self._persist_path.exists():
            return

        old_file = self._persist_path
        migrated_file = old_file.with_suffix('.migrated')

        # Check if already migrated
        if migrated_file.exists():
            return

        try:
            with open(old_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    timestamp = datetime.fromisoformat(data['timestamp']).timestamp()
                    event_type = data['event_type']
                    event_data = json.dumps(data)
                    content_hash = data.get('content_hash', '')

                    self._db.execute(
                        "INSERT INTO events (timestamp, event_type, data, hash) VALUES (?, ?, ?, ?)",
                        (timestamp, event_type, event_data, content_hash)
                    )
            self._db.commit()

            # Rename old file to mark as migrated
            old_file.rename(migrated_file)
            logger.info(f"Migrated {self._run_id} events to SQLite")
        except Exception as e:
            logger.warning(f"Migration failed: {e}")

    async def _flush_worker(self) -> None:
        """Background worker that flushes events in batches."""
        batch = []
        last_flush = datetime.now()

        while True:
            try:
                # Wait for event or timeout
                try:
                    event = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=self._SQLITE_FLUSH_INTERVAL
                    )
                    if event is None:  # Shutdown signal
                        break
                    batch.append(event)
                except asyncio.TimeoutError:
                    pass

                # Flush if batch full or timeout reached
                if len(batch) >= self._SQLITE_BATCH_SIZE or \
                   (batch and (datetime.now() - last_flush).total_seconds() >= self._SQLITE_FLUSH_INTERVAL):
                    flush_start = time.perf_counter()
                    # Run directly — _db was created in the event loop thread,
                    # and _flush_batch is a sync I/O call, not CPU-bound.
                    self._flush_batch(batch)
                    flush_latency_ms = (time.perf_counter() - flush_start) * 1000
                    trace_evidence_flush(len(batch), flush_latency_ms, "ok", len(batch))
                    batch = []
                    last_flush = datetime.now()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Flush worker error: {e}")
                trace_evidence_flush(0, 0.0, "error", None)

        # Final flush
        if batch:
            flush_start = time.perf_counter()
            # Run directly — same thread as the worker, _db was created in event loop thread
            self._flush_batch(batch)
            flush_latency_ms = (time.perf_counter() - flush_start) * 1000
            trace_evidence_flush(len(batch), flush_latency_ms, "ok", len(batch))

    def _flush_batch(self, batch: List[Dict[str, Any]]) -> None:
        """Flush a batch of events to SQLite."""
        if not batch or not self._db:
            return

        records = []
        for event_data in batch:
            timestamp = event_data.get('timestamp', datetime.now().timestamp())
            event_type = event_data.get('event_type', 'unknown')
            data = json.dumps(event_data)
            content_hash = event_data.get('content_hash', '')

            records.append((timestamp, event_type, data, content_hash))

        self._db.executemany(
            "INSERT INTO events (timestamp, event_type, data, hash) VALUES (?, ?, ?, ?)",
            records
        )
        self._db.commit()

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

    def _trim_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
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
    def persist_path(self) -> Optional[Path]:
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

        if self._initialized and self._queue and not self._closing:
            try:
                self._queue.put_nowait(event.to_dict())
            except asyncio.QueueFull:
                logger.warning("SQLite queue full, falling back to file")
                trace_queue_drop("sqlite_queue", queue_size + 1)

        # Persistuj na disk pokud je povoleno (append-only)
        if self._enable_persist and self._persist_file:
            try:
                line = event.to_jsonl_line()
                bytes_to_write = line.encode('utf-8') + b'\n'

                # Encrypt if enabled
                if self._encrypt_at_rest and self._cipher:
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

                # Write to file (text mode for non-encrypted, binary for encrypted)
                if self._encrypt_at_rest:
                    self._persist_file.write(bytes_to_write)
                else:
                    self._persist_file.write(line + '\n')
                self._persist_file.flush()  # Flush for replay
                # Batch fsync: only fsync every N events to avoid IO bottleneck
                # Finalize() will always fsync to preserve crash-safety
                self._fsync_counter += 1
                if self._fsync_counter >= self._FSYNC_EVERY_N_EVENTS:
                    os.fsync(self._persist_file.fileno())
                    self._fsync_counter = 0
            except Exception as e:
                logger.error(f"Failed to persist event: {e}")

        # Trim payload pro RAM šetření
        # NOTE: After trimming, content_hash must be RECOMPUTED to match the
        # trimmed payload in RAM. This ensures verify_integrity() passes
        # on in-memory events. The JSONL was already written with the correct
        # original-payload hash before this trim, so persisted events are fine.
        event.payload = self._trim_payload(event.payload)
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

        # If deque overflowed (was full before append), rebuild indexes
        if was_full:
            self._dropped_count += 1
            try:
                self._rebuild_indexes()
            except Exception:
                pass  # Fail-safe: never crash orchestration

        index = len(self._log) - 1

        # Aktualizuj indexy
        self._index_by_type[event.event_type].append(index)

        # Indexuj podle source_ids
        for source_id in event.source_ids:
            if source_id not in self._index_by_source:
                self._index_by_source[source_id] = []
            self._index_by_source[source_id].append(index)

        # ===== SHADOW ANALYTICS HOOK (Sprint 8AX) =====
        # Non-blocking, fail-open: extract finding metadata and enqueue for DuckDB shadow.
        # GHOST_DUCKDB_SHADOW=1 must be set to activate.
        # This runs AFTER the event is fully committed to the log — zero risk to main path.
        try:
            from .knowledge.analytics_hook import shadow_record_finding
            # Only emit shadow records for evidence_packet events with URL-bearing payloads
            if event.event_type == "evidence_packet":
                payload = event.payload or {}
                # Extract correlation from payload if present (flattened by create_event)
                _corr: Optional[Dict[str, Any]] = payload.get("_correlation")
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
        except Exception:
            # Fail-open: shadow hook never crashes the main path
            pass

    def _rebuild_indexes(self) -> None:
        """Přebuduj indexy po vyřazení z ring bufferu."""
        self._index_by_type = {
            "tool_call": [],
            "observation": [],
            "synthesis": [],
            "error": [],
            "decision": [],
            "evidence_packet": [],
        }
        self._index_by_source = {}

        for i, event in enumerate(self._log):
            self._index_by_type[event.event_type].append(i)
            for source_id in event.source_ids:
                if source_id not in self._index_by_source:
                    self._index_by_source[source_id] = []
                self._index_by_source[source_id].append(i)

    def create_event(
        self,
        event_type: Literal["tool_call", "observation", "synthesis", "error", "decision", "evidence_packet"],
        payload: Dict[str, Any],
        source_ids: Optional[List[str]] = None,
        confidence: float = 1.0,
        correlation: Optional[Dict[str, Optional[str]]] = None,
    ) -> EvidenceEvent:
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
            Vytvořená EvidenceEvent
        """
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
        event = EvidenceEvent(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
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
        summary: Dict[str, Any],
        source_ids: Optional[List[str]] = None,
        confidence: float = 1.0,
    ) -> EvidenceEvent:
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
        summary: Dict[str, Any],
        reasons: List[str],
        refs: Dict[str, List[str]],
        confidence: float = 1.0,
    ) -> EvidenceEvent:
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
            EvidenceEvent s trimmovaným payloadem
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

    def get(self, index: int) -> Optional[EvidenceEvent]:
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

    def get_by_id(self, event_id: str) -> Optional[EvidenceEvent]:
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
        event_type: Optional[str] = None,
        min_confidence: float = 0.0,
        after_timestamp: Optional[datetime] = None,
        before_timestamp: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> List[EvidenceEvent]:
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
            f"",
            f"Run ID: {self._run_id}",
            f"Total Events: {self.size}",
            f"Created: {self._created_at.isoformat()}",
            f"",
            "Event Counts by Type:",
        ]

        for event_type, indices in self._index_by_type.items():
            count = len(indices)
            if count > 0:
                lines.append(f"  {event_type}: {count}")

        lines.extend([
            f"",
            "-" * 40,
            f"Last {last_n} Events (newest first):",
            "-" * 40,
        ])

        # Poslední N událostí v reverzním pořadí
        recent_events = self._log[-last_n:] if len(self._log) >= last_n else self._log
        recent_events = list(reversed(recent_events))

        for i, event in enumerate(recent_events, 1):
            timestamp = event.timestamp.strftime("%H:%M:%S")
            payload_summary = self._summarize_payload(event.payload)

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

    def _summarize_payload(self, payload: Dict[str, Any], max_length: int = 60) -> str:
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

    def to_jsonl(self, path: Optional[Path] = None) -> None:
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
        run_id: Optional[str] = None,
        load_to_ram: bool = False,
        max_ram_events: int = 100
    ) -> "EvidenceLog":
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
            with open(path, 'r', encoding='utf-8') as f:
                first_line = f.readline().strip()
                if first_line:
                    data = json.loads(first_line)
                    detected_run_id = data.get("run_id", "unknown")

        # Vytvoř log bez persistence (pouze čtení)
        log = cls(
            run_id=detected_run_id or "unknown",
            enable_persist=False
        )

        # Spočítej celkový počet řádků
        total_lines = 0
        with open(path, 'r', encoding='utf-8') as f:
            for _ in f:
                total_lines += 1

        log._total_count = total_lines

        # Načti události do RAM - pouze poslední N pro ring buffer
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

            # Pokud nechceme vše v RAM, vem jen poslední max_ram_events
            if not load_to_ram and len(lines) > max_ram_events:
                lines = lines[-max_ram_events:]
                log._dropped_count = total_lines - len(lines)

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                event = EvidenceEvent.from_dict(data)
                # Přidej přímo do _log (skip append pro rychlost při načítání)
                index = len(log._log)
                log._log.append(event)
                log._index_by_type[event.event_type].append(index)
                for source_id in event.source_ids:
                    if source_id not in log._index_by_source:
                        log._index_by_source[source_id] = []
                    log._index_by_source[source_id].append(index)

        return log

    def freeze(self) -> None:
        """Zmrazí log - přepne do read-only režimu"""
        self._frozen = True

    def write_manifest(self) -> Optional[Path]:
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
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
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

        # 1. Cancel flush task first (guaranteed clean termination)
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass  # Expected
            except Exception as e:
                logger.warning(f"Flush worker shutdown error: {e}")
            self._flush_task = None

        # 2. Drain any remaining items from queue (items queued after _closing=True)
        drained = []
        while True:
            try:
                item = self._queue.get_nowait()
                if item is None:
                    break
                drained.append(item)
            except asyncio.QueueEmpty:
                break

        # Flush drained items synchronously (SQLite is thread-bound, runs in event loop thread)
        if drained:
            try:
                self._flush_batch(drained)
            except Exception as e:
                logger.warning(f"Failed to flush remaining items: {e}")

        # 3. Close SQLite connection via event loop (sqlite3.Connection is not thread-safe)
        # Sprint F200E: use a Future to wait for actual close completion
        if self._db is not None:
            try:
                loop = asyncio.get_running_loop()
                close_future = loop.create_future()

                def _do_close():
                    try:
                        self._db.close()
                    finally:
                        close_future.set_result(None)

                loop.call_soon_threadsafe(_do_close)
                # Wait for close to complete before proceeding
                await close_future
            except Exception as e:
                logger.warning(f"Failed to schedule SQLite close: {e}")
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
        Sync cleanup: run aclose in a dedicated thread with its own event loop.

        Idempotent: safe to call multiple times.
        Works from both sync and async (pytest-asyncio) contexts.
        """
        import concurrent.futures

        def _run_aclose():
            asyncio.run(self.aclose())

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

        logger.info(f"[EVIDENCE] Log finalized: run_id={self._run_id}, events={self._total_count}, chain_head={self._chain_head[:16]}...")

    def verify_all(self) -> Dict[str, Any]:
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
            "all_valid": len(invalid) == 0,
            # Chain verification results
            "chain_valid": chain_valid,
            "chain_invalid_reason": chain_invalid_reason,
            "chain_invalid": chain_invalid,
            "chain_head": self._chain_head,
            "last_seq_no": self._seq,
        }

    def get_event_funnel(self) -> Dict[str, Any]:
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

    def get_decision_summary(self) -> Dict[str, Any]:
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

        kind_counts: Dict[str, int] = {}
        all_reasons: List[str] = []
        confidences: List[float] = []

        for e in decisions:
            payload = e.payload or {}
            kind = payload.get("kind", "unknown")
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            reasons = payload.get("reasons", [])
            all_reasons.extend(reasons)
            confidences.append(e.confidence)

        # Top reason fragments (first 40 chars)
        top_reasons: Dict[str, int] = {}
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

    def get_error_rate(self) -> Dict[str, Any]:
        """
        Vrací error rate a low-confidence event breakdown.

        Praktický signál pro sprint kvalitu:
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
            payload = e.payload or {}
            recent_errors.append({
                "event_id": e.event_id,
                "timestamp": e.timestamp.isoformat(),
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

    def get_statistics(self) -> Dict[str, Any]:
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

    def get_sprint_health_summary(self) -> Dict[str, Any]:
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
            elif dominant[0] == "observation":
                posture = "observation_heavy"
            elif dominant[0] == "decision":
                posture = "decision_heavy"
            elif dominant[0] == "tool_call":
                posture = "tool_heavy"
            elif dominant[0] == "error":
                posture = "error_heavy"
            elif dominant[0] == "synthesis":
                posture = "synthesis_heavy"
            else:
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
        elif error_rate >= 20 or low_conf_rate >= 30:
            health = "noisy"
        elif error_rate >= 10 or low_conf_rate >= 20:
            health = "degraded"
        elif error_rate >= 5 or low_conf_rate >= 10:
            health = "warning"
        else:
            health = "healthy"

        # Override to error_heavy if errors dominate funnel
        if posture == "error_heavy" and error_rate > 15:
            health = "degraded" if health == "healthy" else health

        # ---- 5. TOP WEAK SPOTS (bounded raw access) ----
        weak_spots: Dict[str, int] = {}
        error_events = self.query(event_type="error", limit=100)
        for e in error_events:
            payload = e.payload or {}
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
                payload = e.payload or {}
                recent_high_conf_decisions.append({
                    "event_id": e.event_id[-12:],
                    "kind": payload.get("kind", ""),
                    "conf": e.confidence,
                    "timestamp": e.timestamp.isoformat(),
                })
                if len(recent_high_conf_decisions) >= 3:
                    break

        # ---- 7. LOW-CONFIDENCE PRESSURE ----
        low_conf_pressure = ""
        if low_conf_decisions > 0 and decision_count > 0:
            pressure_pct = low_conf_decisions / decision_count * 100
            if pressure_pct > 30:
                low_conf_pressure = "high"
            elif pressure_pct > 15:
                low_conf_pressure = "moderate"
            else:
                low_conf_pressure = "low"
        else:
            low_conf_pressure = "none"

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
            "low_conf_pressure": low_conf_pressure,
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

    def get_retrospective_bundle(self) -> Dict[str, Any]:
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

        # ---- Secondary bounded raw scan: only where helpers don't suffice ----
        # Scan last 200 events for "what_worked" and "breakdown" signals
        recent = list(self._log)[-200:] if self._log else []

        # What worked: event types with high avg_conf and high count
        what_worked: List[str] = []
        funnel = health.get("event_funnel") or {}
        for et, data in funnel.items():
            if data.get("avg_conf", 0) >= 0.85 and data.get("count", 0) >= 2:
                label = f"{et} (conf={data['avg_conf']:.2f}, n={data['count']})"
                what_worked.append(label)
        what_worked = what_worked[:4]  # Cap at 4

        # Breakdown: low-conf event types and error weak spots
        breakdown: List[str] = []
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
        elif health_status == "healthy":
            verdict = f"clean sprint: {posture}, {total} events, {decision_count} decisions"
        elif health_status == "warning":
            verdict = f"warning sprint: {posture}, {total} events, {error_rate:.1f}% errors"
        elif health_status == "degraded":
            verdict = f"degraded sprint: {posture}, {total} events, {error_rate:.1f}% errors"
        elif health_status == "noisy":
            verdict = f"noisy sprint: {posture}, {total} events, {error_rate:.1f}% errors — signal hard to trust"
        else:
            verdict = f"{posture} sprint: {total} events, health={health_status}"

        # ---- Continue / Pivot / Inspect recommendation ----
        continue_or_pivot = "continue"
        if health_status == "noisy":
            continue_or_pivot = "pivot"
        elif health_status == "degraded" and error_rate > 15:
            continue_or_pivot = "pivot"
        elif health_status == "degraded":
            continue_or_pivot = "inspect"
        elif health_status == "warning":
            continue_or_pivot = "inspect"
        elif health.get("low_conf_pressure") == "high":
            continue_or_pivot = "inspect"
        elif total < 10:
            continue_or_pivot = "inspect"  # Not enough data to trust verdict

        # ---- Operator takeaway: one-line bottom line ----
        if total == 0:
            operator_takeaway = "no data — sprint not started or all events dropped"
        elif health_status == "healthy":
            operator_takeaway = f"sprint healthy, {decision_count} decisions made, continue"
        elif health_status == "warning":
            operator_takeaway = f"sprint has warnings: {biggest_weakness[:60] if biggest_weakness else 'see breakdown'}"
        elif health_status == "degraded":
            operator_takeaway = f"sprint degraded: {biggest_weakness[:60] if biggest_weakness else 'errors above threshold'}"
        elif health_status == "noisy":
            operator_takeaway = f"sprint noisy: {biggest_weakness[:60] if biggest_weakness else 'too many errors to trust'}"
        else:
            operator_takeaway = f"sprint status={health_status}, verdict={verdict[:80]}"

        # ---- Top retro actions: 2-3 condensed items ----
        top_retro_actions: List[str] = []

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
        health_confidence_note = ""
        if total < 10:
            health_confidence_note = f"low confidence: only {total} events — treat verdict as indicative"
        elif health_status == "noisy":
            health_confidence_note = "low confidence: error_rate >20% — signal integrity compromised"
        elif health.get("low_conf_pressure") == "high":
            health_confidence_note = "moderate confidence: high low-conf decision pressure"
        else:
            health_confidence_note = "confident verdict: sufficient data and low noise"

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
            "health_confidence_note": health_confidence_note,
            # Compact operator retrospective delta (Sprint F150H)
            "operator_retro_brief": operator_takeaway,  # canonical one-liner
            "continue_reason": self._derive_continue_reason(continue_or_pivot, health_status, decision_count, biggest_weakness),
            "trust_level": self._derive_trust_level(total, health_status, health.get("low_conf_pressure", "none"), error_rate),
            "biggest_win": what_worked[0] if what_worked else "",
            "retro_priority": top_retro_actions[0] if top_retro_actions else "",
            # Underlying signals (for deep dive)
            "_health": health,
        }

    def get_chain(self, event_id: str) -> List[EvidenceEvent]:
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
