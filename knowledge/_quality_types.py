"""
_quality_types — Quality decision types shared across knowledge layer.

NEEDED BY:
    - knowledge/duckdb_store.py  (defers quality assessment to this module)
    - knowledge/quality_assessment.py  (quality assessor interface)
    - knowledge/sprint_facts/canonical_finding.py  (re-exports for public API)

CAN IMPORT:
    - msgspec
    - typing
    - standard library only

MUST NOT IMPORT:
    - knowledge/duckdb_store.py (breaks the circular dependency)
"""

from __future__ import annotations

import msgspec


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


__all__ = ["FindingQualityDecision"]
