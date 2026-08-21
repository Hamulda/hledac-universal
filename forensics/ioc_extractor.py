"""
IOC Extractor — delegates to knowledge.ioc_processor (F350M-R).
=============================================================================

DEPRECATION NOTICE (F350M-R):
    This module is DEPRECATED and kept ONLY for backward compatibility.

    - Canonical path (hot path): Use knowledge/duckdb_store.py directly
      which calls batch_ioc_extract_unified from hledac_rust_extensions.
    - Canonical path (cold path): Import from knowledge.ioc_processor
      which uses get_accel() for proper lazy Rust probe.

    Migration (search+replace):
        from hledac.universal.forensics.ioc_extractor import fast_ioc_extract
        ↓ REPLACE WITH
        from hledac.universal.knowledge.ioc_processor import fast_ioc_extract

F350M-R ISSUE: Replaced broken `from _core.rust_backend import rust` pattern
(which failed because rust.ioc is a domain object, not a module)
with the canonical get_accel() facade.
"""

from __future__ import annotations

import warnings

# Emit deprecation warning on import — aligned with intel/__init__.py pattern
warnings.warn(
    "forensics.ioc_extractor is deprecated — "
    "import from 'hledac.universal.knowledge.ioc_processor' instead. "
    "Hot path (duckdb_store): batch_ioc_extract_unified from hledac_rust_extensions.",
    DeprecationWarning,
    stacklevel=1,
)

# Re-export everything from the unified facade for backward compatibility
from hledac.universal.knowledge.ioc_processor import (  # noqa: F401,E402,F811
    _HASH_VALIDATORS,
    _IOC_COMBINED,
    _IOC_PATTERNS,
    _IOC_TYPE_NAMES,
    _TRACKING_PARAMS,
    batch_dedup_urls,
    # Note: forensics/ is part of hledac.universal package, so hledac.universal.* imports work
    # when the package is installed. This is the standard project import convention.
    fast_ioc_extract,
    ioc_extract_to_canonical_findings,
    ioc_extract_to_canonical_findings_bulk,
    url_normalize,
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
    "_IOC_PATTERNS",
    "_IOC_COMBINED",
    "_HASH_VALIDATORS",
    "_TRACKING_PARAMS",
    "_IOC_TYPE_NAMES",
]
