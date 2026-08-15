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
from hledac.universal.compat.msgspec_gc_compat import Struct


class CanonicalFinding(Struct, frozen=True):
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

    ISSUE F5-FIX: WARC provenance fields for court-admissible evidence replay:
      - warc_record_id: URN-UUID of WARC record
      - warc_path: Absolute path to .warc.gz file
      - compressed_offset: Compressed (seekable) byte offset in WARC file
      - compressed_size: Compressed record block size
      - warc_url: Archived URL from WARC-Target-URI

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

    # ISSUE F5-FIX: WARC provenance fields for court-admissible evidence replay
    # These fields are populated when the finding was extracted from archived web content
    warc_record_id: str | None = None  # URN-UUID from WARC-Record-ID header
    warc_path: str | None = None  # Absolute path to .warc.gz file
    compressed_offset: int = 0  # Compressed (seekable) byte offset
    compressed_size: int = 0  # Compressed record block size
    warc_url: str | None = None  # Archived URL from WARC-Target-URI

    @classmethod
    def dynamic_schema(cls) -> dict[str, Any]:
        """
        Issue 4.3: Dynamic schema via msgspec.json.schema().

        Replaces SCHEMA_VERSION constants. At startup, validates that in-memory
        CanonicalFinding shape matches the persisted DuckDB table schema.

        Returns JSON schema dict for runtime validation.
        """
        return msgspec.json.schema(cls)

    @classmethod
    def from_warc_record(
        cls,
        *,
        warc_record_id: str,
        warc_path: str,
        compressed_offset: int,
        compressed_size: int,
        warc_url: str,
        query: str,
        source_type: str = "warc_replay",
        confidence: float = 0.9,
        payload_text: str | None = None,
        provenance: tuple[str, ...] | None = None,
    ) -> "CanonicalFinding":
        """
        ISSUE F5-FIX: Factory method to create CanonicalFinding from WARC record metadata.

        Single aggregation point for all WARC → CanonicalFinding conversions.
        Called by:
          - duckdb_store.py (replay path)
          - warc_replay.py (playback extraction)
          - 5+ other call sites via this factory

        Args:
            warc_record_id: URN-UUID from WARC-Record-ID header
            warc_path: Absolute path to .warc.gz file
            compressed_offset: Compressed (seekable) byte offset in WARC file
            compressed_size: Compressed record block size
            warc_url: Archived URL from WARC-Target-URI
            query: Research query that triggered this archive fetch
            source_type: Source type (default "warc_replay")
            confidence: Confidence score (default 0.9 for archived content)
            payload_text: Optional extracted text from WARC record
            provenance: Provenance chain (auto-prepended with WARC metadata)

        Returns:
            CanonicalFinding with WARC fields populated for court-admissible replay
        """
        import hashlib
        import time as _time

        # Generate stable finding_id from WARC metadata (deterministic)
        fid_input = f"{warc_record_id}\x00{warc_url}\x00{compressed_offset}"
        finding_id = hashlib.sha256(fid_input.encode()).hexdigest()[:16]

        # Build provenance chain with WARC metadata
        warc_provenance = (
            f"warc:{warc_record_id}",
            f"offset:{compressed_offset}",
            f"size:{compressed_size}",
        )
        if provenance:
            final_provenance = tuple(list(provenance) + list(warc_provenance))
        else:
            final_provenance = warc_provenance

        return cls(
            finding_id=finding_id,
            query=query,
            source_type=source_type,
            confidence=confidence,
            ts=_time.time(),
            provenance=final_provenance,
            payload_text=payload_text,
            warc_record_id=warc_record_id,
            warc_path=warc_path,
            compressed_offset=compressed_offset,
            compressed_size=compressed_size,
            warc_url=warc_url,
        )

    @classmethod
    def from_adapters(
        cls,
        adapters: list[Any],
        query: str,
        source_type: str,
    ) -> list["CanonicalFinding"]:
        """
        Gap C FIX: Unified factory for adapter-based finding creation.

        Replaces scattered to_canonical_findings() calls across 7 adapters:
          - discovery/academic/arxiv_adapter.py
          - discovery/academic/core_adapter.py
          - discovery/academic/openalex_adapter.py
          - discovery/academic/s2orc_adapter.py
          - discovery/academic/unpaywall_adapter.py
          - recon/bgp_lane.py
          - recon/wayback_cdx.py

        Single entry point: adapters implement to_canonical_findings() and this
        factory aggregates them.

        Args:
            adapters: List of adapters with to_canonical_findings(query) method
            query: Research query
            source_type: Base source type for all findings

        Returns:
            List of CanonicalFinding from all adapters
        """
        import logging
        _logger = logging.getLogger(__name__)
        
        findings: list[CanonicalFinding] = []
        for adapter in adapters:
            adapter_name = type(adapter).__name__
            try:
                if hasattr(adapter, "to_canonical_findings"):
                    results = adapter.to_canonical_findings(query)
                    if results:
                        findings.extend(results)
                        _logger.debug(
                            "[CanonicalFinding] %s produced %d findings for query '%s'",
                            adapter_name,
                            len(results),
                            query[:50],
                        )
            except Exception as e:  # noqa: BLE001 — fail-soft per adapter
                _logger.warning(
                    "[CanonicalFinding] %s.to_canonical_findings() failed for query '%s': %s",
                    adapter_name,
                    query[:50],
                    e,
                )
        return findings


# FindingQualityDecision is defined in knowledge/_quality_types.py.
# Re-exported here for backward compatibility with code that imports it from this module.
from .._quality_types import FindingQualityDecision
from _core import aclose


class ActivationResult(Struct, frozen=True):
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
