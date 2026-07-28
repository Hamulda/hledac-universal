"""IOC Extractor — delegates to knowledge.ioc_processor (F350M-R).

This module is kept for backward compatibility (callers in forensics/ and runtime/).
All IOC extraction now routes through knowledge.ioc_processor which uses
AccelBackend (get_accel()) for proper lazy Rust probe.

F350M-R: Replaced broken `from core.rust_backend import rust` pattern
(which failed because rust.ioc is a domain object, not a module)
with the canonical get_accel() facade.

Hot path (canonical write): knowledge/duckdb_store.py uses
    batch_ioc_extract_unified directly from hledac_rust_extensions.
    NOT routed through here — hot path bypasses Python entirely.
"""

# Re-export everything from the unified facade for backward compatibility
from hledac.universal.knowledge.ioc_processor import (  # noqa: F401,E402,F811
    fast_ioc_extract,
    url_normalize,
    batch_dedup_urls,
    ioc_extract_to_canonical_findings,
    ioc_extract_to_canonical_findings_bulk,
    _IOC_PATTERNS,
    _IOC_COMBINED,
    _HASH_VALIDATORS,
    _TRACKING_PARAMS,
    _IOC_TYPE_NAMES,
)

# Backward compatibility — module-level flag for callers that checked this
_RUST_IOC_AVAILABLE = False  # Deprecated: always False (extraction now via ioc_processor)

# For callers that imported IOC_FINDINGS_MAX

IOC_FINDINGS_MAX = 100  # backward compat — same constant as in ioc_processor

# For runtime/enrichment_services.py that imports GLOBAL_IOC_BUDGET_DEFAULT
GLOBAL_IOC_BUDGET_DEFAULT = 1000  # max IOCs per enrichment cycle (backward compat)

__all__ = [
    "_RUST_IOC_AVAILABLE",
    "fast_ioc_extract",
    "url_normalize",
    "batch_dedup_urls",
    "ioc_extract_to_canonical_findings",
    "ioc_extract_to_canonical_findings_bulk",
    "IOC_FINDINGS_MAX",
    # Patterns re-export (used by core/rust_backend/ioc.py Python fallback)
    "_IOC_PATTERNS",
    "_IOC_COMBINED",
    "_HASH_VALIDATORS",
    "_TRACKING_PARAMS",
    "_IOC_TYPE_NAMES",
]
