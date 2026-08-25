"""
evidence/ — Evidence ledger components.

Architecture (Sprint Split-Brain):
- EvidenceLog: Main orchestrator (facade)
- EvidenceWriter: Write path (create_event, persist, chain hash)
- EvidenceQuery: Read path (get, query, verify)
- WARCArchiver: WARC HTTP archival
"""

from hledac.universal.evidence._archiver import (
    WARCArchiver,
    WARCWriter,
    WarcWriteResult,
    _clear_warc_globals,
    get_warc_paths,
    get_warc_snippets,
)
from hledac.universal.evidence._query import (
    EvidenceQuery,
)
from hledac.universal.evidence._writer import (
    EvidenceEvent,
    EvidenceWriter,
    _RustMPSCBytes,
)

# ISSUE #20: EvidenceLog orchestrator + shared factory live inside the package
# so `from hledac.universal.evidence import EvidenceLog` is the canonical path
# and evidence_log.py is just a backward-compat façade.
from hledac.universal.evidence._log import (
    EvidenceLog,
    archive_http_response_cached,
    _normalize_payload,
)
from hledac.universal.evidence.shared import (
    evidence_log_factory,
    evidence_log_init,
)

__all__ = [
    "WarcWriteResult",
    "WARCWriter",
    "WARCArchiver",
    "get_warc_paths",
    "get_warc_snippets",
    "_clear_warc_globals",
    "EvidenceEvent",
    "_RustMPSCBytes",
    "EvidenceWriter",
    "EvidenceQuery",
    "EvidenceLog",
    "archive_http_response_cached",
    "_normalize_payload",
    "evidence_log_factory",
    "evidence_log_init",
]
