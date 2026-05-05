"""
UUIDv7 runtime ID helper.

Provides time-ordered UUIDs for ephemeral runtime identifiers.
Falls back to uuid4 for Python < 3.14 (uuid7 added in 3.14).
"""

from __future__ import annotations

import uuid

# uuid7() added in Python 3.14 — fallback for older versions
try:
    _uuid7 = uuid.uuid7
except AttributeError:
    _uuid7 = uuid.uuid4


def new_runtime_id() -> str:
    """Return a time-ordered UUIDv7 string for ephemeral runtime IDs.

    Use for: decision_id, operation_id, pivot_id, msg_id, job_id.
    Do NOT use for: canonical findings, content hashes, dedup fingerprints,
    LMDB keys, STIX deterministic IDs, or stable provenance references.
    """
    return str(_uuid7())


def new_runtime_short_id(n: int = 12) -> str:
    """Return a truncated UUIDv7 prefix (first n hex chars).

    Useful for short log labels, display tags, or compact references.
    Not unique — do not use as a canonical identifier.
    """
    return str(_uuid7())[:n]