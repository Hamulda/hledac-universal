"""
evidence_log — backward-compatible façade for the EvidenceLog ledger.

The implementation now lives in :mod:`hledac.universal.evidence._log`.
Import from either path; both resolve to the same objects:

    from hledac.universal.evidence_log import EvidenceLog
    from hledac.universal.evidence import EvidenceLog  # canonical

ISSUE #20: this file no longer hosts the monolith — it re-exports the public
API from the ``evidence`` package so the two entry points stay in sync and the
circular-import workaround (``runtime/_shared/evidence_log_shared.py``) is gone.
"""

from hledac.universal.evidence import (  # noqa: F401
    EvidenceLog,
    EvidenceEvent,
    EvidenceWriter,
    EvidenceQuery,
    WARCArchiver,
    WARCWriter,
    WarcWriteResult,
    _RustMPSCBytes,
    _clear_warc_globals,
    get_warc_paths,
    get_warc_snippets,
    archive_http_response_cached,
    _normalize_payload,
    evidence_log_factory,
    evidence_log_init,
)

__all__ = [
    "EvidenceLog",
    "EvidenceEvent",
    "EvidenceWriter",
    "EvidenceQuery",
    "WARCArchiver",
    "WARCWriter",
    "WarcWriteResult",
    "_RustMPSCBytes",
    "_clear_warc_globals",
    "get_warc_paths",
    "get_warc_snippets",
    "archive_http_response_cached",
    "_normalize_payload",
    "evidence_log_factory",
    "evidence_log_init",
]
