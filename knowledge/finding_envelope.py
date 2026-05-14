"""
Sprint F202A: Evidence Envelope and Signal Schema

Bounded audit layer for CanonicalFinding — each finding can carry:
  - audit_reason:     why this finding was accepted
  - evidence_pointers: URLs, file refs, raw inputs that informed the finding
  - signal_facets:     categorized quality signals (entropy, novelty, completeness)
  - suggested_pivots:  recommended next investigation directions

Envelope is serialized into CanonicalFinding.payload_text as JSON.
No new persistent write path — reuses existing LMDB payload storage.

Size guards: MAX_ENVELOPE_SIZE=4096 bytes JSON-serialized.
Fail-soft: invalid/corrupt envelope degrades to plain finding (no crash).

M1 safe: pure Python, no model load, no JS renderer.
"""
from __future__ import annotations

import json
import logging

__all__ = [
    "FindingEnvelope",
    "MAX_ENVELOPE_SIZE",
    "envelope_size_guard",
    "serialize_envelope",
    "deserialize_envelope",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Bounded, deterministic max size for serialized envelope JSON.
# 4096 bytes accommodates rich audit trails without unbounded growth.
MAX_ENVELOPE_SIZE: int = 4098


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

class FindingEnvelope:
    """
    Sprint F202A: Audit metadata attached to a CanonicalFinding.

    Fields:
        audit_reason:      Human-readable justification for finding acceptance.
                           Must be non-empty string when envelope is present.
        evidence_pointers: List of evidence references (URLs, paths, IDs).
                           Empty list means no explicit evidence tracked.
        signal_facets:     Dict of categorized quality signals.
                           Known keys: entropy_bits, novelty_score, completeness_pct.
                           callers expand facets arbitrarily — guard at serialize time.
        suggested_pivots:  List of recommended next investigation directions.
                           Each pivot is a dict with keys: direction, query_hint, priority.
                           Empty list means no pivots derived.
        chain_refs:        F203D: List of evidence chain IDs (root_finding_id) that
                           this finding is part of. Enables "why do we believe this"
                           traceability from derived findings back to raw sources.
                           Empty list means no chain participation.

    Invariants:
        - audit_reason must be non-empty str when envelope is present
        - evidence_pointers is list[str], may be empty
        - signal_facets is dict[str, float], may be empty
        - suggested_pivots is list[dict], may be empty
        - chain_refs is list[str], may be empty
        - Serialized JSON must fit within MAX_ENVELOPE_SIZE bytes
    """

    audit_reason: str
    evidence_pointers: list[str]
    signal_facets: dict[str, float]
    suggested_pivots: list[dict]
    chain_refs: list[str]

    def __init__(
        self,
        audit_reason: str = "",
        evidence_pointers: list[str] | None = None,
        signal_facets: dict[str, float] | None = None,
        suggested_pivots: list[dict] | None = None,
        chain_refs: list[str] | None = None,
    ) -> None:
        self.audit_reason = audit_reason
        self.evidence_pointers = evidence_pointers if evidence_pointers is not None else []
        self.signal_facets = signal_facets if signal_facets is not None else {}
        self.suggested_pivots = suggested_pivots if suggested_pivots is not None else []
        self.chain_refs = chain_refs if chain_refs is not None else []

    def is_populated(self) -> bool:
        """True if envelope carries any meaningful metadata beyond default empty."""
        return bool(
            self.audit_reason
            or self.evidence_pointers
            or self.signal_facets
            or self.suggested_pivots
            or self.chain_refs
        )


# ---------------------------------------------------------------------------
# Size Guard
# ---------------------------------------------------------------------------

def envelope_size_guard(envelope: FindingEnvelope) -> bool:
    """
    Returns True if envelope JSON representation fits within MAX_ENVELOPE_SIZE.

    Guard is deterministic — uses orjson if available for performance,
    falls back to stdlib json otherwise.
    """
    try:
        import orjson

        raw = orjson.dumps(envelope)
    except Exception:
        try:
            raw = json.dumps(envelope.__dict__, separators=(",", ":")).encode("utf-8")
        except Exception:
            return False

    return len(raw) <= MAX_ENVELOPE_SIZE


# ---------------------------------------------------------------------------
# Serialization / Deserialization
# ---------------------------------------------------------------------------

def serialize_envelope(envelope: FindingEnvelope) -> str | None:
    """
    Serialize FindingEnvelope to JSON string for storage in payload_text.

    Returns None if serialization fails OR if result exceeds MAX_ENVELOPE_SIZE.
    Fail-soft: caller degrades to plain finding when None is returned.
    """
    if not envelope.is_populated():
        return None

    try:
        import orjson

        raw = orjson.dumps(
            {
                "audit_reason": envelope.audit_reason,
                "evidence_pointers": envelope.evidence_pointers,
                "signal_facets": envelope.signal_facets,
                "suggested_pivots": envelope.suggested_pivots,
                "chain_refs": envelope.chain_refs,
            }
        )
    except Exception:
        try:
            raw = json.dumps(
                envelope.__dict__,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        except Exception:
            logger.warning("[ENVELOPE] serialize failed — will degrade to plain finding")
            return None

    if len(raw) > MAX_ENVELOPE_SIZE:
        logger.warning(
            "[ENVELOPE] envelope size %d exceeds MAX_ENVELOPE_SIZE %d — will degrade to plain finding",
            len(raw),
            MAX_ENVELOPE_SIZE,
        )
        return None

    try:
        return raw.decode("utf-8")
    except Exception:
        return None


def deserialize_envelope(payload_text: str | None) -> FindingEnvelope | None:
    """
    Deserialize JSON string from payload_text back to FindingEnvelope.

    Returns None if payload_text is None/empty, parsing fails, or
    required fields are missing. Fail-soft: None means caller degrades to plain finding.
    """
    if not payload_text:
        return None

    data: dict = {}
    try:
        import orjson

        data = orjson.loads(payload_text)
    except Exception:
        try:
            data = json.loads(payload_text)
        except Exception:
            # Fail-soft: non-JSON payload_text is expected for legacy findings
            # that were stored before the envelope format existed. Silent degrade.
            # Only log if payload_text looks like corrupted JSON (has '{' but failed parse)
            if payload_text.strip().startswith("{"):
                logger.warning(
                    "[ENVELOPE] deserialize failed for payload_text (malformed JSON) — will degrade to plain finding"
                )
            return None

    if not isinstance(data, dict):  # type: ignore[unreachable]
        return None

    # Require audit_reason at minimum — absence means this is not a real envelope
    audit_reason = data.get("audit_reason", "")
    if not isinstance(audit_reason, str) or not audit_reason:
        logger.debug("[ENVELOPE] payload_text has no audit_reason — treating as legacy finding")
        return None

    return FindingEnvelope(
        audit_reason=audit_reason,
        evidence_pointers=data.get("evidence_pointers", []),
        signal_facets=data.get("signal_facets", {}),
        suggested_pivots=data.get("suggested_pivots", []),
        chain_refs=data.get("chain_refs", []),
    )