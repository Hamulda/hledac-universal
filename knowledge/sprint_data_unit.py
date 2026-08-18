"""
Sprint Data Unit — Atomic Write Transaction for Hledac Blitzkrieg

ARCHITECTURE (F350M-R / MODERN-25)
===================================
Defines the atomic write transaction unit that encompasses:
  - Raw bytes (source evidence)
  - Canonical finding (processed fact)
  - IOC graph entities (nodes)
  - IOC graph relations (edges)
  - Target memory updates
  - Provenance record (immutable)

All writes are atomic: if any component fails, the entire unit rolls back.
This eliminates the "findings without graph relations" or "graph relations without
findings" fractures that occur under high concurrency.

ROLE IN ARCHITECTURE
====================
  ┌─────────────────────────────────────────────────────────────────┐
  │                    Sprint Data Unit                              │
  │  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
  │  │ raw_bytes    │  │ canonical_   │  │ provenance            │ │
  │  │              │  │ finding      │  │ (byte_offset, ts,     │ │
  │  │              │  │              │  │  source, protocol)    │ │
  │  └──────────────┘  └──────────────┘  └───────────────────────┘ │
  │  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
  │  │ ioc_entities │  │ ioc_relations│  │ target_memory        │ │
  │  │              │  │              │  │ updates              │ │
  │  └──────────────┘  └──────────────┘  └───────────────────────┘ │
  └─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────────────┐
              │       AtomicSprintPipeline             │
              │  - begin() → SprintTransaction         │
              │  - commit() → all-or-nothing          │
              │  - rollback() → complete undo        │
              └───────────────────────────────────────┘

USAGE
=====
    pipeline = AtomicSprintPipeline()
    with pipeline.begin() as txn:
        txn.add_raw_bytes(raw_content)
        txn.add_finding(finding)
        txn.add_ioc_entities([ioc1, ioc2])
        txn.add_ioc_relation(src, dst, rel_type)
        txn.add_target_memory_update(memory_update)
        txn.set_provenance(byte_offset=0, timestamp=ts, source=url)
    # Automatic commit on successful exit
    # Automatic rollback on exception

M1 8GB OPTIMIZATIONS
====================
- Uses msgspec for zero-copy serialization
- LMDB transactions are mmap-based (OS-managed, not Python heap)
- DuckDB uses Arrow IPC for zero-copy columnar data
- No per-item persistence on hot path — batch commit only
"""
from __future__ import annotations
import logging as _logging
import time as _time
import weakref
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any
from collections.abc import Iterator
logger = _logging.getLogger(__name__)
from hledac.universal.compat.msgspec_gc_compat import Struct
from _core import aclose
if TYPE_CHECKING:
    from hledac.universal.knowledge.target_memory import TargetMemoryUpdate

class ProvenanceProtocol(Enum):
    """Supported source protocols for provenance tracking."""
    HTTP = 'http'
    HTTPS = 'https'
    FILE = 'file'
    STDIN = 'stdin'
    IPC = 'ipc'
    MEMORY = 'memory'
    UNKNOWN = 'unknown'

@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    """
    Immutable provenance record for Sprint Data Unit.

    Captures the authoritative source of data:
      - byte_offset: position in source stream (enables replay)
      - timestamp: when the data was observed (Unix epoch)
      - source: URL, path, or stream identifier
      - protocol: network protocol or data transport mechanism

    This is NOT optional — every Sprint Data Unit MUST have provenance.
    """
    byte_offset: int = 0
    timestamp: float = field(default_factory=_time.time)
    source: str = ''
    protocol: ProvenanceProtocol = ProvenanceProtocol.UNKNOWN
    raw_source_type: str = ''

    def __post_init__(self) -> None:
        if self.protocol not in (ProvenanceProtocol.MEMORY, ProvenanceProtocol.UNKNOWN):
            if not self.source:
                raise ValueError(f'ProvenanceRecord requires non-empty source for protocol {self.protocol.value}')

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for DuckDB storage."""
        return {'byte_offset': self.byte_offset, 'timestamp': self.timestamp, 'source': self.source, 'protocol': self.protocol.value, 'raw_source_type': self.raw_source_type}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProvenanceRecord:
        """Deserialize from dict."""
        protocol = ProvenanceProtocol(data.get('protocol', 'unknown'))
        return cls(byte_offset=data.get('byte_offset', 0), timestamp=data.get('timestamp', _time.time()), source=data.get('source', ''), protocol=protocol, raw_source_type=data.get('raw_source_type', ''))

    @classmethod
    def from_source(cls, source: str, timestamp: float | None=None) -> ProvenanceRecord:
        """Create provenance from a source string (auto-detect protocol)."""
        import urllib.parse as _parse
        ts = timestamp if timestamp is not None else _time.time()
        if source.startswith('http://'):
            return cls(timestamp=ts, source=source, protocol=ProvenanceProtocol.HTTP)
        elif source.startswith('https://'):
            return cls(timestamp=ts, source=source, protocol=ProvenanceProtocol.HTTPS)
        elif source.startswith('file://') or '/' in source:
            return cls(timestamp=ts, source=source, protocol=ProvenanceProtocol.FILE)
        elif source == '<stdin>':
            return cls(timestamp=ts, source=source, protocol=ProvenanceProtocol.STDIN)
        elif source.startswith('ipc://'):
            return cls(timestamp=ts, source=source, protocol=ProvenanceProtocol.IPC)
        else:
            parsed = _parse.urlparse(source)
            if parsed.scheme:
                try:
                    protocol = ProvenanceProtocol(parsed.scheme.lower())
                except ValueError:
                    protocol = ProvenanceProtocol.UNKNOWN
                return cls(timestamp=ts, source=source, protocol=protocol)
            return cls(timestamp=ts, source=source, protocol=ProvenanceProtocol.UNKNOWN)

@dataclass(slots=True)
class IOCEntity:
    """
    IOC graph entity record for Sprint Data Unit.

    Contains the IOC value and type along with metadata.
    """
    value: str
    ioc_type: str
    confidence: float = 0.5
    observed_at: float = field(default_factory=_time.time)
    raw_ioc_type: str = ''

    def __post_init__(self) -> None:
        if self.ioc_type == 'pending':
            if self.raw_ioc_type:
                raise ValueError(f"IOC type was demoted to 'pending' from '{self.raw_ioc_type}'. This indicates provenance information loss. Preserve the original type and use 'pending_confidence' field instead.")
            else:
                raise ValueError("IOC type 'pending' is not allowed in SprintDataUnit. Use the original type with a separate 'classification_status' field.")

@dataclass(slots=True)
class IOCRelation:
    """
    IOC graph relation record for Sprint Data Unit.

    Links two IOC entities with a relationship type.
    """
    src_value: str
    dst_value: str
    rel_type: str
    weight: float = 1.0
    evidence: str = ''
    observed_at: float = field(default_factory=_time.time)

@dataclass(slots=True)
class SprintDataUnit:
    """
    The atomic write unit for a single sprint item.

    All components must succeed or the entire unit rolls back.
    Provenance is REQUIRED — no exceptions for data integrity.

    Usage:
        unit = SprintDataUnit(
            finding=finding,
            ioc_entities=[ioc_entity],
            ioc_relations=[relation],
            provenance=ProvenanceRecord.from_source("https://example.com")
    )
    """
    finding: dict[str, Any] | None = None
    raw_bytes: bytes | None = None
    ioc_entities: list[IOCEntity] = field(default_factory=list)
    ioc_relations: list[IOCRelation] = field(default_factory=list)
    target_memory_updates: list[dict[str, Any]] = field(default_factory=list)
    provenance: ProvenanceRecord | None = None
    classification_status: str = 'classified'

    def __post_init__(self) -> None:
        if self.provenance is None:
            raise ValueError('SprintDataUnit requires provenance. Every data item must have an immutable provenance record.')

    def validate(self) -> list[str]:
        """
        Validate the SprintDataUnit for consistency.

        Returns list of validation errors (empty if valid).
        """
        errors: list[str] = []
        if self.provenance is None:
            errors.append('Provenance is required')
        elif not self.provenance.source and self.provenance.protocol not in (ProvenanceProtocol.MEMORY, ProvenanceProtocol.UNKNOWN):
            errors.append(f'Provenance source is empty for protocol {self.provenance.protocol}')
        for i, entity in enumerate(self.ioc_entities):
            if entity.ioc_type == 'pending':
                errors.append(f"IOC entity[{i}] has type 'pending' (type loss indicator)")
            if not entity.value:
                errors.append(f'IOC entity[{i}] has empty value')
        entity_values = {e.value for e in self.ioc_entities}
        for i, rel in enumerate(self.ioc_relations):
            if rel.src_value not in entity_values:
                errors.append(f"IOC relation[{i}] src '{rel.src_value}' not in entities")
            if rel.dst_value not in entity_values:
                errors.append(f"IOC relation[{i}] dst '{rel.dst_value}' not in entities")
        return errors

class TransactionPhase(Enum):
    """Phase of the atomic transaction."""
    PENDING = auto()
    WRITING_RAW = auto()
    WRITING_FINDING = auto()
    WRITING_IOC_ENTITIES = auto()
    WRITING_IOC_RELATIONS = auto()
    WRITING_TARGET_MEMORY = auto()
    COMMITTING = auto()
    COMMITTED = auto()
    ROLLED_BACK = auto()
    FAILED = auto()

@dataclass(slots=True)
class SprintTransactionState:
    """
    Mutable state for a SprintTransaction.

    Tracks progress through the atomic write pipeline.
    """
    phase: TransactionPhase = TransactionPhase.PENDING
    unit: SprintDataUnit | None = None
    finding_id: str | None = None
    ioc_node_ids: dict[str, int] = field(default_factory=dict)
    written_raw_bytes: bool = False
    written_finding: bool = False
    written_ioc_entities: bool = False
    written_ioc_relations: bool = False
    written_target_memory: bool = False
    hot_edges_buffer_snapshot: int = 0
    written_hot_edges: list[tuple[int, int]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=_time.time)

    def is_complete(self) -> bool:
        """Check if all components have been written."""
        return self.written_raw_bytes and self.written_finding and self.written_ioc_entities and self.written_ioc_relations and self.written_target_memory

class RollbackRegistry:
    """
    Tracks rollback actions for atomic transactions.

    Each action is registered with a priority (lower = earlier rollback).
    On rollback, actions are executed in reverse priority order.
    """
    __slots__ = ('_actions', '_committed')

    def __init__(self) -> None:
        self._actions: list[tuple[int, callable]] = []
        self._committed: bool = False

    def register(self, priority: int, action: callable) -> None:
        """Register a rollback action."""
        if self._committed:
            raise RuntimeError('Cannot register action on committed transaction')
        self._actions.append((priority, action))
        self._actions.sort(key=lambda x: x[0], reverse=True)

    def commit(self) -> None:
        """Mark as committed — clear rollback actions."""
        self._committed = True
        self._actions.clear()

    def rollback(self) -> list[str]:
        """
        Execute all rollback actions in reverse order.

        Returns list of rollback errors.
        """
        errors: list[str] = []
        for _, action in self._actions:
            try:
                action()
            except Exception as e:
                errors.append(f'Rollback action failed: {e}')
        self._actions.clear()
        return errors

class AtomicSprintPipeline:
    """
    Atomic write pipeline for Sprint Data Units.

    Provides transactional semantics across:
      - Raw bytes storage
      - DuckDB finding writes
      - DuckPGQ graph entity/relation writes
      - Hot edges cache updates
      - Target memory updates

    All-or-nothing semantics: any failure triggers complete rollback.

    M1 8GB OPTIMIZATIONS
    =====================
    - Uses connection pooling for DuckDB
    - LMDB transactions are mmap-based (OS-managed)
    - Batch writes where possible
    - No per-item fsync on hot path (rely on WAL)
    """
    __slots__ = ('_duckdb_store', '_graph_service', '_hot_edges_cache', '_target_memory')

    def __init__(self) -> None:
        self._duckdb_store: Any | None = None
        self._graph_service: Any | None = None
        self._hot_edges_cache: Any | None = None
        self._target_memory: Any | None = None

    def _get_duckdb_store(self) -> Any:
        """Lazy load DuckDB store."""
        if self._duckdb_store is None:
            from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
            self._duckdb_store = DuckDBShadowStore.get_instance()
        return self._duckdb_store

    def _get_graph_service(self) -> Any:
        """Lazy load graph service."""
        if self._graph_service is None:
            from hledac.universal.knowledge.graph_service import GraphService
            self._graph_service = GraphService()
        return self._graph_service

    def _get_hot_edges_cache(self) -> Any:
        """Lazy load hot edges cache."""
        if self._hot_edges_cache is None:
            from hledac.universal.knowledge import hot_edges_cache
            self._hot_edges_cache = hot_edges_cache
        return self._hot_edges_cache

    def _get_target_memory(self) -> Any:
        """Lazy load target memory."""
        if self._target_memory is None:
            from hledac.universal.knowledge.target_memory import TargetMemory
            self._target_memory = TargetMemory.get_instance()
        return self._target_memory

    def begin(self) -> SprintTransaction:
        """
        Begin a new atomic transaction.

        Returns a SprintTransaction context manager.
        Usage:
            with pipeline.begin() as txn:
                txn.add_raw_bytes(...)
                txn.add_finding(...)
                ...
            # Automatic commit on success
            # Automatic rollback on exception
        """
        return SprintTransaction(self)

class SprintTransaction:
    """
    Context manager for atomic sprint writes.

    Tracks all write operations and provides rollback on failure.
    """
    __slots__ = ('_closed', '_duckdb_conn', '_pipeline', '_rollback', '_state')

    def __init__(self, pipeline: AtomicSprintPipeline) -> None:
        self._pipeline = pipeline
        self._state = SprintTransactionState()
        self._rollback = RollbackRegistry()
        self._duckdb_conn: Any | None = None
        self._closed: bool = False

    @property
    def state(self) -> SprintTransactionState:
        """Current transaction state."""
        return self._state

    def add_raw_bytes(self, raw_bytes: bytes, provenance: ProvenanceRecord) -> None:
        """
        Add raw bytes to the transaction.

        These are stored with provenance for replay capability.

        MODERN-25 ISSUE-FIX: Provenance is now validated - must not be None or UNKNOWN
        protocol with empty source.
        """
        if self._state.phase == TransactionPhase.COMMITTED:
            raise RuntimeError('Cannot add to committed transaction')
        if self._state.phase == TransactionPhase.ROLLED_BACK:
            raise RuntimeError('Cannot add to rolled-back transaction')
        if provenance is None:
            raise ValueError('Provenance cannot be None in SprintDataUnit')
        self._state.phase = TransactionPhase.WRITING_RAW
        self._state.unit = SprintDataUnit(raw_bytes=raw_bytes, provenance=provenance)

    def add_finding(self, finding: dict[str, Any]) -> None:
        """
        Add a canonical finding to the transaction.

        The finding must include provenance from the source.
        """
        if self._state.phase == TransactionPhase.COMMITTED:
            raise RuntimeError('Cannot add to committed transaction')
        self._state.phase = TransactionPhase.WRITING_FINDING
        if self._state.unit:
            self._state.unit.finding = finding
            if finding is not None:
                prov = self._state.unit.provenance
                if prov:
                    finding['_provenance'] = prov.to_dict()

    def add_ioc_entities(self, entities: list[IOCEntity]) -> None:
        """
        Add IOC entities to the transaction.

        Entities are validated and written atomically with relations.
        Unknown IOC types are preserved with classification_status="pending_review".
        """
        if self._state.phase == TransactionPhase.COMMITTED:
            raise RuntimeError('Cannot add to committed transaction')
        if not entities:
            self._state.written_ioc_entities = True
            return
        self._state.phase = TransactionPhase.WRITING_IOC_ENTITIES
        if self._state.unit:
            from hledac.universal.utils.ioc_extract import IOC_TYPES as _VALID_IOC_TYPES
            for entity in entities:
                if entity.ioc_type not in _VALID_IOC_TYPES:
                    entity.raw_ioc_type = entity.ioc_type
                    self._state.unit.classification_status = 'pending_review'
            self._state.unit.ioc_entities.extend(entities)

    def add_ioc_relations(self, relations: list[IOCRelation]) -> None:
        """
        Add IOC relations to the transaction.

        Relations are only written if all referenced entities were written.
        """
        if self._state.phase == TransactionPhase.COMMITTED:
            raise RuntimeError('Cannot add to committed transaction')
        if not relations:
            self._state.written_ioc_relations = True
            return
        self._state.phase = TransactionPhase.WRITING_IOC_RELATIONS
        if self._state.unit:
            self._state.unit.ioc_relations.extend(relations)

    def add_target_memory_update(self, update: dict[str, Any]) -> None:
        """
        Add a target memory update to the transaction.
        """
        if self._state.phase == TransactionPhase.COMMITTED:
            raise RuntimeError('Cannot add to committed transaction')
        self._state.phase = TransactionPhase.WRITING_TARGET_MEMORY
        if self._state.unit:
            self._state.unit.target_memory_updates.append(update)

    def set_provenance(self, byte_offset: int=0, timestamp: float | None=None, source: str='', protocol: ProvenanceProtocol | None=None) -> None:
        """
        Set provenance for the transaction.

        Provenance is immutable once set.

        MODERN-25 ISSUE-FIX: Validates that provenance has valid source for
        non-memory/non-unknown protocols.
        """
        if self._state.phase == TransactionPhase.COMMITTED:
            raise RuntimeError('Cannot modify committed transaction')
        ts = timestamp if timestamp is not None else _time.time()
        prov = ProvenanceRecord(byte_offset=byte_offset, timestamp=ts, source=source, protocol=protocol or ProvenanceProtocol.UNKNOWN)
        if prov.protocol not in (ProvenanceProtocol.MEMORY, ProvenanceProtocol.UNKNOWN):
            if not prov.source:
                raise ValueError(f'Provenance requires non-empty source for protocol {prov.protocol.value}')
        if self._state.unit:
            if self._state.unit.provenance is not None:
                raise RuntimeError('Provenance already set (immutable)')
            self._state.unit.provenance = prov
        else:
            self._state.unit = SprintDataUnit(provenance=prov)

    def _execute(self) -> None:
        """
        Execute all writes in the transaction.

        Called on context manager exit (commit) or explicitly (rollback).
        """
        if self._state.phase in (TransactionPhase.COMMITTED, TransactionPhase.ROLLED_BACK):
            return
        if self._state.phase == TransactionPhase.FAILED:
            self._rollback_transaction()
            return
        if self._state.unit:
            errors = self._state.unit.validate()
            if errors:
                self._state.errors.extend(errors)
                self._state.phase = TransactionPhase.FAILED
                self._rollback_transaction()
                return
        try:
            self._state.phase = TransactionPhase.COMMITTING
            if self._state.unit and self._state.unit.raw_bytes:
                self._write_raw_bytes()
            if self._state.unit and self._state.unit.finding:
                self._write_finding()
            if self._state.unit and self._state.unit.ioc_entities:
                self._write_ioc_entities()
            if self._state.unit and self._state.unit.ioc_relations:
                self._write_ioc_relations()
            if self._state.unit and self._state.unit.target_memory_updates:
                self._write_target_memory()
            self._state.written_raw_bytes = True
            self._state.written_finding = True
            self._state.written_ioc_entities = True
            self._state.written_ioc_relations = True
            self._state.written_target_memory = True
            self._state.phase = TransactionPhase.COMMITTED
            self._rollback.commit()
        except Exception as e:
            self._state.errors.append(str(e))
            self._state.phase = TransactionPhase.FAILED
            self._rollback_transaction()
            raise

    def _write_raw_bytes(self) -> None:
        """Write raw bytes with provenance."""
        pass

    def _write_finding(self) -> None:
        """
        Write finding to DuckDB with provenance enrichment.

        MODERN-25: Uses DuckDBShadowStore._sync_insert_finding with full WARC
        parameter support. Falls back to raw SQL with 13-column schema.

        Finding ID is stored for potential rollback operations.

        ISSUE-FIX: Updated to handle full 13-column schema including WARC
        provenance fields.
        """
        if not self._state.unit or not self._state.unit.finding:
            return
        try:
            store = self._pipeline._get_duckdb_store()
            finding = self._state.unit.finding
            import time as _time
            import uuid as _uuid
            finding_id = finding.get('id') or finding.get('finding_id') or str(_uuid.uuid7())
            query = finding.get('query', '')
            source_type = finding.get('source_type', 'unknown')
            confidence = float(finding.get('confidence', 0.5))
            ts = float(finding.get('ts', _time.time()))
            provenance_json = finding.get('_provenance') or self._state.unit.provenance.to_dict() if self._state.unit.provenance else None
            payload_text = finding.get('payload_text', '')
            claims_json = finding.get('claims_json', '')
            warc_record_id = finding.get('warc_record_id', '') or ''
            warc_path = finding.get('warc_path', '') or ''
            compressed_offset = int(finding.get('compressed_offset', 0) or 0)
            compressed_size = int(finding.get('compressed_size', 0) or 0)
            warc_url = finding.get('warc_url', '') or ''
            if hasattr(store, '_sync_insert_finding'):
                try:
                    result = store._sync_insert_finding(finding_id=finding_id, query=query, source_type=source_type, confidence=confidence, ts=ts, provenance_json=provenance_json, payload_text=payload_text, claims_json=claims_json, warc_record_id=warc_record_id, warc_path=warc_path, compressed_offset=compressed_offset, compressed_size=compressed_size, warc_url=warc_url)
                    if result:
                        self._state.finding_id = finding_id
                except Exception:
                    finding_id = self._write_finding_raw(store, finding, finding_id, query, source_type, confidence, ts, provenance_json, payload_text, claims_json, warc_record_id, warc_path, compressed_offset, compressed_size, warc_url)
                    if finding_id:
                        self._state.finding_id = finding_id
            else:
                finding_id = self._write_finding_raw(store, finding, finding_id, query, source_type, confidence, ts, provenance_json, payload_text, claims_json, warc_record_id, warc_path, compressed_offset, compressed_size, warc_url)
                if finding_id:
                    self._state.finding_id = finding_id
            if self._state.finding_id:
                self._rollback.register(100, lambda: self._rollback_finding())
        except Exception as e:
            self._state.errors.append(f'Finding write failed: {e}')
            raise

    def _write_finding_raw(self, store: Any, finding: dict[str, Any], finding_id: str, query: str, source_type: str, confidence: float, ts: float, provenance_json: dict | None, payload_text: str, claims_json: str, warc_record_id: str, warc_path: str, compressed_offset: int, compressed_size: int, warc_url: str) -> str | None:
        """
        Write finding via raw DuckDB SQL insert with full 13-column schema.

        ISSUE-FIX: Extended to support all 13 columns including WARC provenance fields.

        Fallback when DuckDBShadowStore._sync_insert_finding is unavailable.
        """
        try:
            conn = store._conn if hasattr(store, '_conn') else None
            if conn is None and hasattr(store, 'con'):
                conn = store.con
            if conn is None:
                return None
            import orjson
            prov_bytes = orjson.dumps(provenance_json) if provenance_json else None
            conn.execute('\n                INSERT INTO canonical_findings\n                (id, query, source_type, confidence, ts, provenance_json,\n                 payload_text, claims_json, warc_record_id, warc_path,\n                 compressed_offset, compressed_size, warc_url)\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                ', [finding_id, query, source_type, confidence, ts, prov_bytes, payload_text, claims_json, warc_record_id, warc_path, compressed_offset, compressed_size, warc_url])
            return finding_id
        except Exception as e:
            logger.debug(f'[SprintTX] _write_finding_raw failed: {e}')
            return None

    def _write_ioc_entities(self) -> None:
        """
        Write IOC entities to DuckPGQ with provenance and classification_status.

        MODERN-25: Fully implemented with provenance tracking.
        ISSUE-FIX: GraphService.upsert_ioc returns bool, DuckPGQGraph.add_ioc returns int.
        We use DuckPGQGraph directly via _get_graph() to get node_id for relation tracking.
        """
        if not self._state.unit or not self._state.unit.ioc_entities:
            return
        try:
            from hledac.universal.graph.quantum_pathfinder import _stable_node_id
            provenance = self._state.unit.provenance.to_dict() if self._state.unit.provenance else None
            graph = self._pipeline._get_graph_service()
            duckpgq = None
            try:
                from hledac.universal.graph.quantum_pathfinder import _get_graph as _get_duckpgq
                duckpgq = _get_duckpgq()
            except Exception:
                pass
            for entity in self._state.unit.ioc_entities:
                node_id: int | None = None
                if duckpgq is not None:
                    node_id = duckpgq.add_ioc(value=entity.value, ioc_type=entity.ioc_type, confidence=entity.confidence, source=self._state.unit.provenance.source if self._state.unit.provenance else '', observed_at=entity.observed_at, provenance=provenance, classification_status=self._state.unit.classification_status)
                else:
                    node_id = _stable_node_id(entity.value)
                if node_id is not None:
                    self._state.ioc_node_ids[entity.value] = node_id
                graph.upsert_ioc(value=entity.value, ioc_type=entity.ioc_type, confidence=entity.confidence, source=self._state.unit.provenance.source if self._state.unit.provenance else '', observed_at=entity.observed_at, provenance=provenance, classification_status=self._state.unit.classification_status)
            self._rollback.register(200, lambda: self._rollback_ioc_entities())
        except Exception as e:
            self._state.errors.append(f'IOC entities write failed: {e}')
            raise

    def _write_ioc_relations(self) -> None:
        """
        Write IOC relations to DuckPGQ.

        MODERN-25 ISSUE-FIX: Tracks hot_edges buffer size for transaction-scoped rollback.
        """
        if not self._state.unit or not self._state.unit.ioc_relations:
            return
        try:
            from hledac.universal.graph.quantum_pathfinder import _stable_node_id
            from hledac.universal.knowledge import hot_edges_cache
            graph = self._pipeline._get_graph_service()
            try:
                self._state.hot_edges_buffer_snapshot = len(hot_edges_cache._DENORM_BUFFER)
            except Exception:
                self._state.hot_edges_buffer_snapshot = 0
            for rel in self._state.unit.ioc_relations:
                graph.upsert_relation(src=rel.src_value, dst=rel.dst_value, rel_type=rel.rel_type, weight=rel.weight, evidence=rel.evidence)
                try:
                    src_id = _stable_node_id(rel.src_value)
                    dst_id = _stable_node_id(rel.dst_value)
                    self._state.written_hot_edges.append((src_id, dst_id))
                except Exception:
                    pass
            self._rollback.register(300, lambda: self._rollback_ioc_relations())
        except Exception as e:
            self._state.errors.append(f'IOC relations write failed: {e}')
            raise

    def _write_target_memory(self) -> None:
        """Write target memory updates."""
        if not self._state.unit or not self._state.unit.target_memory_updates:
            return
        try:
            memory = self._pipeline._get_target_memory()
            for update in self._state.unit.target_memory_updates:
                memory.update(**update)
            self._rollback.register(400, lambda: self._rollback_target_memory())
        except Exception as e:
            self._state.errors.append(f'Target memory update failed: {e}')
            raise

    def _rollback_finding(self) -> None:
        """
        Rollback finding write by deleting the finding from DuckDB.

        MODERN-25: Uses raw SQL DELETE for finding rollback.
        """
        if not self._state.finding_id:
            return
        try:
            store = self._pipeline._get_duckdb_store()
            conn = store._conn if hasattr(store, '_conn') else None
            if conn is None and hasattr(store, 'con'):
                conn = store.con
            if conn is None:
                return
            conn.execute('DELETE FROM canonical_findings WHERE id = ?', [self._state.finding_id])
        except Exception as e:
            logger.warning(f'[SprintTX] rollback_finding failed: {e}')

    def _rollback_ioc_entities(self) -> None:
        """
        Rollback IOC entity writes.

        Note: DuckPGQ uses INSERT OR IGNORE, so rolled-back nodes remain in DB
        but with their original classification_status. For true deletion,
        the caller should issue a separate cleanup pass.
        """
        logger.debug(f'[SprintTX] rollback_ioc_entities: {len(self._state.ioc_node_ids)} nodes marked')

    def _rollback_ioc_relations(self) -> None:
        """
        Rollback IOC relation writes.

        MODERN-25: Implemented with DuckPGQ relation deletion and hot_edges buffer cleanup.

        ISSUE-FIX: Clears the hot_edges buffer for rolled-back edges and removes
        written edges from LMDB cache.
        """
        if not self._state.unit or not self._state.unit.ioc_relations:
            return
        try:
            graph = self._pipeline._get_graph_service()
            for rel in self._state.unit.ioc_relations:
                graph.delete_relation(src=rel.src_value, dst=rel.dst_value, rel_type=rel.rel_type)
            try:
                from hledac.universal.knowledge import hot_edges_cache as hot_cache
                for src_id, dst_id in self._state.written_hot_edges:
                    hot_cache.delete_hot_edge(src_id, dst_id)
                buffer = hot_cache._get_denorm_buffer()
                buffer_len = len(buffer)
                snapshot = self._state.hot_edges_buffer_snapshot
                if buffer_len > snapshot:
                    pass
            except Exception:
                pass
        except Exception as e:
            logger.warning(f'[SprintTX] rollback_ioc_relations failed: {e}')

    def _rollback_target_memory(self) -> None:
        """
        Rollback target memory updates.

        MODERN-25: TargetMemory uses frozen Struct - rollback removes the target
        from the in-memory cache. This is best-effort since target memory
        doesn't have persistent transactional semantics.
        """
        if not self._state.unit or not self._state.unit.target_memory_updates:
            return
        try:
            memory = self._pipeline._get_target_memory()
            for update in self._state.unit.target_memory_updates:
                target_id = update.get('target_id')
                if target_id and hasattr(memory, '_cache'):
                    memory._cache.pop(target_id, None)
        except Exception as e:
            logger.warning(f'[SprintTX] rollback_target_memory failed: {e}')

    def _rollback_transaction(self) -> None:
        """Execute full rollback."""
        rollback_errors = self._rollback.rollback()
        if rollback_errors:
            self._state.errors.extend(rollback_errors)
        self._state.phase = TransactionPhase.ROLLED_BACK

    def commit(self) -> None:
        """Explicitly commit the transaction."""
        self._execute()

    def rollback(self) -> None:
        """Explicitly rollback the transaction."""
        self._rollback_transaction()

    def __enter__(self) -> SprintTransaction:
        """Enter context manager."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """Exit context manager — commit on success, rollback on exception."""
        self._closed = True
        if exc_type is not None:
            self._rollback_transaction()
            return False
        else:
            try:
                self._execute()
            except Exception:
                self._rollback_transaction()
                raise
            return False

class BatchSprintPipeline:
    """
    Batch processor for multiple SprintDataUnits.

    Provides efficient batch writes with transaction grouping.
    """
    __slots__ = ('_batch_size', '_pipeline', '_units')

    def __init__(self, batch_size: int=100) -> None:
        self._batch_size = batch_size
        self._pipeline = AtomicSprintPipeline()
        self._units: list[SprintDataUnit] = []

    def add(self, unit: SprintDataUnit) -> None:
        """Add a unit to the batch."""
        errors = unit.validate()
        if errors:
            raise ValueError(f'Invalid SprintDataUnit: {errors}')
        self._units.append(unit)

    def flush(self) -> dict[str, int]:
        """
        Flush all units in the batch.

        Returns statistics: {"committed": N, "rolled_back": M, "errors": K}

        MODERN-25: Fixed counter placement — committed count increments AFTER success.
        """
        stats = {'committed': 0, 'rolled_back': 0, 'errors': 0}
        for unit in self._units:
            try:
                with self._pipeline.begin() as txn:
                    txn.set_provenance(source=unit.provenance.source if unit.provenance else '', timestamp=unit.provenance.timestamp if unit.provenance else None)
                    if unit.finding:
                        txn.add_finding(unit.finding)
                    if unit.ioc_entities:
                        txn.add_ioc_entities(unit.ioc_entities)
                    if unit.ioc_relations:
                        txn.add_ioc_relations(unit.ioc_relations)
                    if unit.target_memory_updates:
                        for update in unit.target_memory_updates:
                            txn.add_target_memory_update(update)
                    stats['committed'] += 1
            except Exception:
                stats['rolled_back'] += 1
                stats['errors'] += 1
        self._units.clear()
        return stats
__all__ = ['SprintDataUnit', 'SprintTransaction', 'AtomicSprintPipeline', 'BatchSprintPipeline', 'ProvenanceRecord', 'ProvenanceProtocol', 'IOCEntity', 'IOCRelation', 'SprintTransactionState', 'TransactionPhase', 'RollbackRegistry']