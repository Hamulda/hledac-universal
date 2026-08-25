"""Canonical IOC type — unified output of the dual-engine extraction.

Both the Rust regex engine and the Brain NER engine normalise their results
into ``CanonicalIOC`` so the rest of the pipeline (match/build stages, DuckDB
findings, graph upserts) consumes a single stable schema regardless of which
engine produced a given indicator.

Design notes (M1 8GB / Python 3.14+):
* Frozen ``msgspec.Struct`` for zero-copy Arrow conversion and immutable,
  hashable records (enables O(1) dedup by key).
* ``context_hash`` is a stable dedup key derived from
  ``(ioc_type, value, raw_context)`` so the same indicator extracted by both
  engines at different times collapses to a single record.
* Fail-safe constructors never raise — empty/useless values yield ``None``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from hledac.universal.compat.msgspec_gc_compat import Struct

# Engine source tags.
RUST_REGEX_SOURCE = "rust_regex"
BRAIN_NER_SOURCE = "brain_ner"

# Confidence defaults per engine: Rust regex is deterministic/structured,
# Brain NER (spaCy/GLiNER) is probabilistic and is upgraded by downstream
# corroboration.
_RUST_DEFAULT_CONFIDENCE = 0.8
_NER_DEFAULT_CONFIDENCE = 0.65


class CanonicalIOC(Struct, frozen=True):
    """A single indicator of compromise, normalised across extraction engines.

    Fields:
        value:        The indicator string (e.g. ``8.8.8.8``, ``evil.com``).
        ioc_type:     Normalised type (ipv4, domain, url, email, cve, ...).
        confidence:   Combined 0.0-1.0 confidence score.
        source:       Originating engine (``rust_regex`` | ``brain_ner``).
        raw_context:  Optional surrounding snippet for provenance.
        context_hash: ``sha1(ioc_type|value|raw_context)[:16]`` dedup key.
    """

    value: str
    ioc_type: str
    confidence: float = 0.0
    source: str = "unknown"
    raw_context: str = ""
    context_hash: str = ""

    @staticmethod
    def _hash(ioc_type: str, value: str, raw_context: str) -> str:
        data = f"{ioc_type}|{value}|{raw_context}".encode()
        return hashlib.sha1(data).hexdigest()[:16]

    def with_context(self, raw_context: str) -> CanonicalIOC:
        """Return a copy with ``raw_context`` (and matching ``context_hash``) set."""
        return CanonicalIOC(
            value=self.value,
            ioc_type=self.ioc_type,
            confidence=self.confidence,
            source=self.source,
            raw_context=raw_context,
            context_hash=self._hash(self.ioc_type, self.value, raw_context),
        )


def make_canonical_ioc(
    value: str,
    ioc_type: str,
    *,
    confidence: float | None = None,
    source: str = "unknown",
    raw_context: str = "",
) -> CanonicalIOC | None:
    """Construct a ``CanonicalIOC``, dropping empty/useless values (fail-safe).

    Returns ``None`` for empty value/type so callers can filter cheaply.
    """
    v = (value or "").strip()
    t = (ioc_type or "").strip()
    if not v or not t:
        return None
    if confidence is None:
        confidence = _RUST_DEFAULT_CONFIDENCE if source == RUST_REGEX_SOURCE else _NER_DEFAULT_CONFIDENCE
    return CanonicalIOC(
        value=v,
        ioc_type=t,
        confidence=float(confidence),
        source=source,
        raw_context=raw_context,
        context_hash=CanonicalIOC._hash(t, v, raw_context),
    )


def canonical_ioc_key(ioc: CanonicalIOC) -> tuple[str, str, str]:
    """Dedup key: ``(ioc_type, value, context_hash)``."""
    return (ioc.ioc_type, ioc.value, ioc.context_hash)


def dedup_canonical_iocs(iocs: Iterable[CanonicalIOC]) -> list[CanonicalIOC]:
    """Deduplicate by ``(ioc_type, value, context_hash)``.

    Keeps the highest-confidence record per key; preserves first-seen order.
    """
    best: dict[tuple[str, str, str], CanonicalIOC] = {}
    for ioc in iocs:
        key = (ioc.ioc_type, ioc.value, ioc.context_hash)
        existing = best.get(key)
        if existing is None or ioc.confidence > existing.confidence:
            best[key] = ioc
    return list(best.values())
