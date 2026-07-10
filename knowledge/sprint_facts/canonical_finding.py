"""
CanonicalFinding — Sprint Facts DTO Layer
=========================================

Canonical internal finding DTO for the sprint facts store.
Uses msgspec.Struct (frozen=True) for zero-copy serialization.

MIGRATION NOTE (Issue #2):
    CanonicalFinding was moved from knowledge/duckdb_store.py to this module.
    Import from here for new code: from knowledge.sprint_facts import CanonicalFinding
    The alias in duckdb_store.py is preserved for backward compatibility.
"""
from __future__ import annotations

from typing import Any

import msgspec


class CanonicalFinding(msgspec.Struct, frozen=True):
    """
    Sprint 8P: Canonical internal finding DTO.

    Minimální povinná pole:
      - finding_id: str       - unique identifier
      - query: str             - research query text
      - source_type: str       - source type (e.g., "web", "document", "synthetic")
      - confidence: float       - confidence score [0.0, 1.0]
      - ts: float              - Unix timestamp
      - provenance: tuple[str, ...] - tvrdý invariant, nesmí být None, default = ()

    Volitelná pole:
      - payload_text: str | None - supplementary text payload

    DTO invariants:
      - frozen=True  - immutabilní instance
      -      - zakázán garbage collector tracking (výkon)
      - msgspec.Struct - zero-copy decode/encode

    NOTE 8Q/8R: CanonicalFinding je používán napříč celým projektem jako univerzální
        typ pro všechny findingy. Přesun do sdíleného DTO modulu řeší circular
        import tím, že DTO je v samostatném modulu bez I/O závislostí.
    """

    finding_id: str
    query: str
    source_type: str
    confidence: float
    ts: float
    provenance: tuple[str, ...] = ()

    # Volitelné doplňkové pole - jde do LMDB WAL payloadu, ne do DuckDB INSERT
    payload_text: str | None = None

    @classmethod
    def dynamic_schema(cls) -> dict[str, Any]:
        """
        Issue 4.3: Dynamic schema via msgspec.json.schema().

        Replaces SCHEMA_VERSION constants. At startup, validates that in-memory
        CanonicalFinding shape matches the persisted DuckDB table schema.

        Returns JSON schema dict for runtime validation.
        """
        return msgspec.json.schema(cls)


class FindingQualityDecision(msgspec.Struct, frozen=True):
    """
    Sprint 8W: Quality decision contract for CanonicalFinding ingest.

    Fields:
        accepted:        True if finding passed quality gate
        reason:          Human-readable reason for reject/accept, or None
        entropy:         Computed entropy in bits per character
        normalized_hash: BLAKE2b fingerprint of normalized text (hex, 32 chars)
        duplicate:       True if exact-content duplicate detected
    """

    accepted: bool
    reason: str | None
    entropy: float
    normalized_hash: str | None
    duplicate: bool


class ActivationResult(msgspec.Struct, frozen=True):
    """
    Sprint F300: Result of activating a finding in the sprint facts store.

    Fields:
        finding_id:     Unique identifier of the finding
        lmdb_success:   True if LMDB WAL write succeeded
        duckdb_success: True if DuckDB write succeeded, False if it failed,
                        None if not yet attempted
        lmdb_key:       "finding:{id}" - LMDB key used
        desync:         True if LMDB OK but DuckDB FAIL (WAL-DuckDB desync)
        error:          Error message if there was an exception, None otherwise
        accepted:       True when finding passed quality gate and was stored
    """

    finding_id: str
    lmdb_success: bool | list[bool]
    duckdb_success: bool | None
    lmdb_key: str
    desync: bool
    error: str | None
    accepted: bool = False
