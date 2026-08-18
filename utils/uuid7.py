"""
UUIDv7 runtime ID helper.

Python 3.14+ provides uuid.uuid7() natively - this module provides a clean
interface for time-ordered UUIDs for ephemeral runtime identifiers.

Use for: decision_id, operation_id, pivot_id, msg_id, job_id.
Do NOT use for: canonical findings, content hashes, dedup fingerprints,
LMDB keys, STIX deterministic IDs, or stable provenance references.
"""

from __future__ import annotations

import uuid
from _core import aclose


def new_runtime_id() -> str:
    """Return a time-ordered UUIDv7 string for ephemeral runtime IDs.

    Requires Python 3.14+ where uuid.uuid7() is built-in.
    """
    return str(uuid.uuid7())


def new_runtime_short_id(n: int = 12) -> str:
    """Return a truncated UUIDv7 prefix (first n hex chars).

    Useful for short log labels, display tags, or compact references.
    Not unique — do not use as a canonical identifier.
    """
    return str(uuid.uuid7())[:n]
