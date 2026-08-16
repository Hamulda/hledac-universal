"""
evidence/ — Evidence ledger components.

Architecture (Sprint Split-Brain):
- EvidenceLog: Main orchestrator (facade)
- EvidenceWriter: Write path (create_event, persist, chain hash)
- EvidenceQuery: Read path (get, query, verify)
- WARCArchiver: WARC HTTP archival
"""

from hledac.universal.evidence._archiver import (
    WarcWriteResult,
    WARCWriter,
    WARCArchiver,
    get_warc_paths,
    get_warc_snippets,
    _clear_warc_globals,
    )
from hledac.universal.evidence._writer import (
    EvidenceEvent,
    _RustMPSCBytes,
    EvidenceWriter,
    )
from hledac.universal.evidence._query import (

    EvidenceQuery,
    )

__all__ = [
    'WarcWriteResult',
    'WARCWriter',
    'WARCArchiver',
    'get_warc_paths',
    'get_warc_snippets',
    '_clear_warc_globals',
    'EvidenceEvent',
    '_RustMPSCBytes',
    'EvidenceWriter',
    'EvidenceQuery',
]
