"""
Knowledge typed schemas — msgspec Structs for findings, IOC, evidence.

ISSUE #13 (cutting-edge): canonical typed schemas for the JSON that flows through
``knowledge/``. Replaces ad-hoc dict JSON with msgspec-backed structs.

Design
------
- ``Struct`` (Rust) is 10× faster than orjson / 50× faster than stdlib json for
  known-shape payloads, and validates on decode — so a malformed finding is
  caught at the boundary, not deep in the pipeline.
- Bytes round-trip through :mod:`hledac.universal.utils.codec` (msgspec → orjson →
  stdlib fallback), so decoding stays lossless and fail-soft even when a payload
  carries fields the struct does not declare.
- Structs declare the *canonical* shape; ``as_*`` helpers validate/normalize a
  plain dict into the typed record, falling back to the raw dict on schema drift.

M1 8GB: Structs are zero-alloc at definition; encode/decode reuse the per-thread
msgspec pool from ``utils.codec``.

Usage
-----
    from hledac.universal.knowledge.schema import (
        FindingRecord, IOCRecord, EvidenceRecord,
        encode_record, decode_record, as_findings,
    )

    raw = encode_record(finding_dict)        # bytes (msgspec, fast)
    rec = decode_record(raw)                 # dict (lossless)
    typed = as_findings(finding_dict)        # FindingRecord | dict
"""

from __future__ import annotations

from typing import Any

import msgspec

from compat.msgspec_gc_compat import Struct
from hledac.universal.utils.codec import decode, decode_typed, encode, encode_typed


class FindingRecord(Struct, frozen=False):
    """Canonical finding record (mirrors ``_core.canonical_schema`` columns)."""

    id: str = ""
    query: str = ""
    source_type: str = ""
    confidence: float = 0.0
    ts: float = 0.0
    provenance: dict[str, Any] = msgspec.field(default_factory=dict)
    payload_text: str = ""
    claims: list[Any] = msgspec.field(default_factory=list)
    warc_record_id: str = ""
    warc_path: str = ""
    warc_url: str = ""
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)


class IOCRecord(Struct, frozen=False):
    """Canonical IOC (indicator of compromise) record."""

    ioc_type: str = "unknown"
    ioc_value: str = ""
    confidence: float = 0.0
    source: str = ""
    first_seen: str = ""
    last_seen: str = ""
    context: str = ""
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)


class EvidenceRecord(Struct, frozen=False):
    """Canonical evidence pointer / artifact record."""

    id: str = ""
    finding_id: str = ""
    kind: str = ""
    value: str = ""
    confidence: float = 0.0
    source: str = ""
    timestamp: str = ""
    url: str = ""
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)


def encode_record(obj: Any) -> bytes:
    """Encode a knowledge record to JSON bytes via the canonical (msgspec) codec."""
    return encode(obj)


def decode_record(data: bytes | str | memoryview | bytearray) -> Any:
    """Decode a knowledge record from JSON bytes/str (lossless, fail-soft)."""
    return decode(data)


def as_findings(obj: Any) -> FindingRecord | dict:
    """Validate/normalize a dict into a ``FindingRecord``; fall back to raw dict."""
    return decode_typed(encode(obj), FindingRecord)  # type: ignore[arg-type]


def as_ioc(obj: Any) -> IOCRecord | dict:
    """Validate/normalize a dict into an ``IOCRecord``; fall back to raw dict."""
    return decode_typed(encode(obj), IOCRecord)  # type: ignore[arg-type]


def as_evidence(obj: Any) -> EvidenceRecord | dict:
    """Validate/normalize a dict into an ``EvidenceRecord``; fall back to raw dict."""
    return decode_typed(encode(obj), EvidenceRecord)  # type: ignore[arg-type]


def encode_findings(obj: Any) -> bytes:
    """Typed encode of a finding using the canonical ``FindingRecord`` schema."""
    return encode_typed(obj, FindingRecord)


def encode_ioc(obj: Any) -> bytes:
    """Typed encode of an IOC using the canonical ``IOCRecord`` schema."""
    return encode_typed(obj, IOCRecord)


def encode_evidence(obj: Any) -> bytes:
    """Typed encode of evidence using the canonical ``EvidenceRecord`` schema."""
    return encode_typed(obj, EvidenceRecord)


__all__ = [
    "FindingRecord",
    "IOCRecord",
    "EvidenceRecord",
    "encode_record",
    "decode_record",
    "as_findings",
    "as_ioc",
    "as_evidence",
    "encode_findings",
    "encode_ioc",
    "encode_evidence",
]
