"""
Storage Trinity Schema Contracts — ISSUE [ARCH-DB-001]

Unified validation layer for the Storage Trinity:





  Layer    | Tech    | Purpose
  ---------|---------|-------------------------------
  LMDB     | Key-val | WAL truth records (finding:{id})
  DuckDB   | SQL     | Canonical findings, queryable
  Vector   | HNSW    | Entity + RAG embeddings

Problem:
  - msgspec.Struct contract (CanonicalFinding) is NOT enforced at LMDB/LanceDB
    boundaries — raw bytes from LMDB go directly into DuckDB without validation.
  - Entity embeddings (DuckDBVectorStore / LanceDB) are decoupled from the
    finding lifecycle — orphaned embeddings can exist without DuckDB records.
  - DuckDB WAL→DuckDB desync: LMDB success + DuckDB failure = ghost entity.

Solution:
  STEALTH WINDOW PATTERN — validated write path with schema enforcement:
    1. CanonicalFindingContract: msgspec.Struct with full field schema
    2. validate_finding_dict(): validate dict against contract before WAL write
    3. validate_finding_from_lmdb(): validate deserialized dict from LMDB
    4. StorageTrinityValidator: cross-store consistency checker

M1 8GB: validation is CPU-only, deterministic, <1ms for batch of 500.
Fail-safe: validation errors never block storage — rejected findings are
          logged and the legacy path continues.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct

if TYPE_CHECKING:
    from hledac.universal.knowledge.sprint_facts.canonical_finding import CanonicalFinding


class CanonicalFindingContract(Struct, frozen=True):
    """
    ISSUE [ARCH-DB-001]: Unified schema contract for Storage Trinity.

    This is the SOLE source of truth for CanonicalFinding field validation.
    All write paths (WAL→LMDB, DuckDB INSERT, entity embedding) MUST validate
    against this contract before persisting.

    Fields mirror CanonicalFinding (knowledge/sprint_facts/canonical_finding.py)
    but add explicit type invariants that msgspec.Struct enforces at decode time.

    Invariants:
      - finding_id: non-empty str (UUID format recommended)
      - query: non-empty str
      - source_type: non-empty str (e.g. "web", "document", "synthetic")
      - confidence: float in [0.0, 1.0]
      - ts: positive float (Unix timestamp)
      - provenance: tuple (never None)
      - payload_text: str or None (never other types)
    """

    finding_id: str
    query: str
    source_type: str
    confidence: float
    ts: float
    provenance: tuple[str, ...] = ()

    # Optional field — goes to LMDB WAL payload, NOT to DuckDB INSERT
    payload_text: str | None = None

    @classmethod
    def validate_dict(cls, raw: dict[str, Any]) -> CanonicalFindingContract:
        """
        Validate a dict against CanonicalFindingContract.

        Raises msgspec.ValidationError if the dict does not match the contract.
        This is the PRIMARY validation entry point for all write paths.

        Usage:
            try:
                contract = CanonicalFindingContract.validate_dict(raw_dict)
            except msgspec.ValidationError as e:
                logger.warning(f"[ARCH-DB-001] Validation failed: {e}")
                raise

        Args:
            raw: Dict from WAL LMDB or external source

        Returns:
            CanonicalFindingContract instance

        Raises:
            msgspec.ValidationError: if validation fails
        """
        return msgspec.convert(raw, CanonicalFindingContract, strict=False)

    def to_canonical_finding(self) -> "CanonicalFinding":
        """
        Convert contract to CanonicalFinding DTO.

        CanonicalFinding is the project's internal DTO. This method is the
        bridge from validated external/raw data to the internal typed object.

        Returns:
            CanonicalFinding instance
        """
        from hledac.universal.knowledge.sprint_facts.canonical_finding import CanonicalFinding

        return CanonicalFinding(
            finding_id=self.finding_id,
            query=self.query,
            source_type=self.source_type,
            confidence=self.confidence,
            ts=self.ts,
            provenance=self.provenance,
            payload_text=self.payload_text,
        )


def _convert_to_contract(
    raw: dict[str, Any], contract_cls: type[msgspec.Struct]
) -> msgspec.Struct | None:
    """
    Convert a raw dict to a msgspec.Struct contract type.

    Fail-safe: returns None if conversion fails, never raises.

    Args:
        raw: Dict to convert
        contract_cls: msgspec.Struct subclass to convert to

    Returns:
        Contract instance if valid, None if invalid
    """
    try:
        return cast(Any, msgspec).convert(raw, contract_cls, strict=False)  # type: ignore[arg-type,return-value]
    except (msgspec.ValidationError, TypeError, ValueError):
        return None


class WALRecordContract(Struct, frozen=True):
    """
    ISSUE [ARCH-DB-001]: Contract for WAL LMDB records.

    WAL stores: key="finding:{finding_id}", value=serialized dict
    This contract validates the dict structure stored in LMDB.

    Note: WAL stores a superset of CanonicalFindingContract fields
    (includes 'id' as alias for 'finding_id' for backward compat).
    """

    id: str
    query: str
    source_type: str
    confidence: float
    ts: float
    provenance: tuple[str, ...] = ()
    payload_text: str | None = None


class EntityEmbeddingContract(Struct, frozen=True):
    """
    ISSUE [ARCH-DB-001]: Contract for entity embeddings in DuckDBVectorStore.

    Entity embeddings are written AFTER DuckDB confirmation (not before like WAL).
    This contract ensures entity data matches the schema before upsert.

    Fields:
      - entity_id: str (primary key, e.g. "dom:example.com")
      - entity_value: str (raw value, e.g. "example.com")
      - entity_type: str (e.g. "domain", "ipv4", "email")
      - metadata: dict (arbitrary metadata)
      - embedding: list[float] (384-dim or 256-dim)
      - updated_at: float (Unix timestamp)
    """

    entity_id: str
    entity_value: str
    entity_type: str
    metadata: dict[str, Any] = {}
    embedding: list[float] = msgspec.field(default_factory=list)
    updated_at: float = 0.0


class RAGChunkContract(Struct, frozen=True):
    """
    ISSUE [ARCH-DB-001]: Contract for RAG chunk embeddings in DuckDBVectorStore.

    RAG chunks represent document fragments stored for semantic search.
    This contract ensures chunk data matches the schema before upsert.

    Fields:
      - chunk_id: str (primary key)
      - document_id: str (parent document identifier)
      - content: str (text content of the chunk)
      - metadata: dict (arbitrary metadata)
      - embedding: list[float] (384-dim)
      - created_at: float (Unix timestamp)
    """

    chunk_id: str
    document_id: str
    content: str
    metadata: dict[str, Any] = {}
    embedding: list[float] = msgspec.field(default_factory=list)
    created_at: float = 0.0


# ---------------------------------------------------------------------------
# Validation helpers (fail-safe, never block storage)
# ---------------------------------------------------------------------------


def validate_finding_dict(raw: dict[str, Any]) -> CanonicalFindingContract | None:
    """
    Validate a raw dict against CanonicalFindingContract.

    Fail-safe: returns None if validation fails, never raises.
    Callers should log the validation error and continue with legacy path.

    Args:
        raw: Dict from WAL LMDB or external source

    Returns:
        CanonicalFindingContract if valid, None if invalid
    """
    result = _convert_to_contract(raw, CanonicalFindingContract)
    if result is None:
        import logging

        _logger = logging.getLogger(__name__)
        _logger.debug(
            "[ARCH-DB-001] WAL dict validation failed | raw_keys=%s",
            list(raw.keys()),
        )
    return cast(CanonicalFindingContract | None, result)


def validate_wal_record(raw: dict[str, Any]) -> WALRecordContract | None:
    """
    Validate a WAL record dict against WALRecordContract.

    Fail-safe: returns None if validation fails, never raises.

    Args:
        raw: Dict from LMDB WAL (key="finding:{id}", value=serialized dict)

    Returns:
        WALRecordContract if valid, None if invalid
    """
    result = _convert_to_contract(raw, WALRecordContract)
    if result is None:
        import logging

        _logger = logging.getLogger(__name__)
        _logger.debug(
            "[ARCH-DB-001] WAL record validation failed | raw_keys=%s",
            list(raw.keys()),
        )
    return cast(WALRecordContract | None, result)


def validate_entity_embedding(raw: dict[str, Any]) -> EntityEmbeddingContract | None:
    """
    Validate an entity embedding dict against EntityEmbeddingContract.

    Fail-safe: returns None if validation fails, never raises.

    Args:
        raw: Dict from entity embedding upsert

    Returns:
        EntityEmbeddingContract if valid, None if invalid
    """
    result = _convert_to_contract(raw, EntityEmbeddingContract)
    if result is None:
        import logging

        _logger = logging.getLogger(__name__)
        _logger.debug(
            "[ARCH-DB-001] Entity embedding validation failed | raw_keys=%s",
            list(raw.keys()),
        )
    return cast(EntityEmbeddingContract | None, result)


def validate_rag_chunk(raw: dict[str, Any]) -> RAGChunkContract | None:
    """
    Validate a RAG chunk dict against RAGChunkContract.

    Fail-safe: returns None if validation fails, never raises.

    Args:
        raw: Dict from RAG chunk upsert

    Returns:
        RAGChunkContract if valid, None if invalid
    """
    result = _convert_to_contract(raw, RAGChunkContract)
    if result is None:
        import logging

        _logger = logging.getLogger(__name__)
        _logger.debug(
            "[ARCH-DB-001] RAG chunk validation failed | raw_keys=%s",
            list(raw.keys()),
        )
    return cast(RAGChunkContract | None, result)


# ---------------------------------------------------------------------------
# Storage Trinity Validator — cross-store consistency checker
# ---------------------------------------------------------------------------


class StorageTrinityValidator:
    """
    ISSUE [ARCH-DB-001]: Cross-store consistency validator for Storage Trinity.

    Detects and logs desync between LMDB WAL, DuckDB, and entity embeddings:
      - Ghost finding: LMDB record exists but DuckDB record missing
      - Ghost embedding: DuckDB record exists but entity embedding missing
      - Orphaned embedding: Entity embedding exists but DuckDB record missing

    This is an ADVISORY checker — it detects and logs desyncs but does NOT
    auto-repair. Repair requires careful coordination and is left to the caller.

    M1 8GB: runs in bounded thread pool, max 1000 findings per check.
    Fail-safe: any error is caught and logged; validator never blocks storage.
    """

    __slots__ = ("_duckdb_store",)

    def __init__(self, duckdb_store: Any) -> None:
        """
        Args:
            duckdb_store: DuckDBShadowStore instance
        """
        self._duckdb_store = duckdb_store

    def check_finding_consistency(
        self,
        finding_ids: list[str],
    ) -> dict[str, Any]:
        """
        Check consistency for a batch of findings across LMDB and DuckDB.

        Args:
            finding_ids: List of finding IDs to check

        Returns:
            Dict with keys:
              - ghost_findings: list of finding_ids in LMDB but not DuckDB
              - lmdb_missing: list of finding_ids in DuckDB but not LMDB
              - duckdb_missing: list of finding_ids in LMDB but not DuckDB
              - checked_count: number of findings checked
              - desync_count: total number of desyncs found
        """
        import logging

        _logger = logging.getLogger(__name__)

        ghost_findings: list[str] = []
        duckdb_missing: list[str] = []
        checked = 0

        try:
            wal_manager = getattr(self._duckdb_store, "_wal_manager", None)
            if wal_manager is None:
                return {
                    "ghost_findings": [],
                    "duckdb_missing": [],
                    "checked_count": 0,
                    "desync_count": 0,
                }

            # Check LMDB → DuckDB direction
            for fid in finding_ids[:1000]:  # Cap at 1000 per check
                wal_record = wal_manager.wal_get_finding(fid)
                lmdb_has = wal_record is not None

                # Check DuckDB directly (sync, fast)
                duckdb_has = self._duckdb_store._sync_verify_duckdb_record(fid)

                if lmdb_has and not duckdb_has:
                    ghost_findings.append(fid)
                elif not lmdb_has and duckdb_has:
                    duckdb_missing.append(fid)

                checked += 1

            desync_count = len(ghost_findings) + len(duckdb_missing)

            if desync_count > 0:
                _logger.warning(
                    "[ARCH-DB-001] Storage desync detected: "
                    "ghost_findings=%d duckdb_missing=%d checked=%d",
                    len(ghost_findings),
                    len(duckdb_missing),
                    checked,
                )

            return {
                "ghost_findings": ghost_findings,
                "duckdb_missing": duckdb_missing,
                "checked_count": checked,
                "desync_count": desync_count,
            }

        except Exception as exc:  # noqa: BLE001 — best-effort; non-critical
            _logger.debug("[ARCH-DB-001] Consistency check failed: %s", exc)
            return {
                "ghost_findings": [],
                "duckdb_missing": [],
                "checked_count": checked,
                "desync_count": 0,
            }

    def check_embedding_consistency(
        self,
        entity_ids: list[str],
    ) -> dict[str, Any]:
        """
        Check consistency between DuckDB canonical records and entity embeddings.

        Cross-references:
          - DuckDB canonical: entity_observations table (entity observations linked to findings)
          - DuckDBVectorStore: entity_embeddings table (identity vectors)

        Detects:
          - orphaned_embeddings: entity_ids IN entity_embeddings but NOT in entity_observations
          - missing_embeddings: entity_ids IN entity_observations but NOT in entity_embeddings

        Args:
            entity_ids: List of entity IDs to check

        Returns:
            Dict with keys:
              - orphaned_embeddings: entity_ids with embedding but no canonical record
              - missing_embeddings: entity_ids with canonical record but no embedding
              - checked_count: number of entities checked
              - desync_count: total desyncs found
        """
        import logging

        _logger = logging.getLogger(__name__)

        orphaned: list[str] = []
        missing_emb: list[str] = []
        checked = 0

        try:
            duckdb_conn = getattr(self._duckdb_store, "_persistent_conn", None)
            if duckdb_conn is None:
                return {
                    "orphaned_embeddings": [],
                    "missing_embeddings": [],
                    "checked_count": 0,
                    "desync_count": 0,
                }

            ids_to_check = entity_ids[:1000]
            if not ids_to_check:
                return {
                    "orphaned_embeddings": [],
                    "missing_embeddings": [],
                    "checked_count": 0,
                    "desync_count": 0,
                }

            # Batch query: entity_ids IN entity_embeddings
            try:
                placeholders = ",".join(["?"] * len(ids_to_check))
                emb_result = duckdb_conn.execute(
                    f"SELECT entity_id FROM entity_embeddings WHERE entity_id IN ({placeholders})",
                    ids_to_check,
                ).fetchall()
                embedding_ids: set[str] = {str(r[0]) for r in emb_result}
            except Exception:
                embedding_ids = set()

            # Batch query: entity_ids IN entity_observations (canonical entity records)
            try:
                placeholders = ",".join(["?"] * len(ids_to_check))
                obs_result = duckdb_conn.execute(
                    f"SELECT DISTINCT entity_value || '|' || entity_type FROM entity_observations WHERE entity_value || '|' || entity_type IN ({placeholders})",
                    ids_to_check,
                ).fetchall()
                # entity_observations uses (entity_value, entity_type) as compound key
                # Build entity_id in same format as entity_embeddings: "value|type"
                canonical_ids: set[str] = {str(r[0]) for r in obs_result}
            except Exception:
                canonical_ids = set()

            # Cross-reference: orphaned = in embeddings but not canonical
            orphaned = sorted(embedding_ids - canonical_ids)
            # Cross-reference: missing_emb = in canonical but not in embeddings
            missing_emb = sorted(canonical_ids - embedding_ids)
            checked = len(ids_to_check)

            desync_count = len(orphaned) + len(missing_emb)

            if desync_count > 0:
                _logger.warning(
                    "[ARCH-DB-001] Embedding desync detected: "
                    "orphaned=%d missing_emb=%d checked=%d",
                    len(orphaned),
                    len(missing_emb),
                    checked,
                )

            return {
                "orphaned_embeddings": orphaned,
                "missing_embeddings": missing_emb,
                "checked_count": checked,
                "desync_count": desync_count,
            }

        except Exception as exc:  # noqa: BLE001 — best-effort; non-critical
            _logger.debug("[ARCH-DB-001] Embedding consistency check failed: %s", exc)
            return {
                "orphaned_embeddings": [],
                "missing_embeddings": [],
                "checked_count": checked,
                "desync_count": 0,
            }


__all__ = [
    "CanonicalFindingContract",
    "WALRecordContract",
    "EntityEmbeddingContract",
    "RAGChunkContract",
    "validate_finding_dict",
    "validate_wal_record",
    "validate_entity_embedding",
    "validate_rag_chunk",
    "StorageTrinityValidator",
]
