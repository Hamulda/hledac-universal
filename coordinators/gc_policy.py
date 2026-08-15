"""
coordinators/gc_policy.py — DEPRECATED: Import from coordinators.resource.resource_coordinator
=======================================================================================

This module is DEPRECATED. All functionality has been moved to:
    coordinators.resource.resource_coordinator

Legacy import (still works for backwards compatibility):
    from hledac.universal.coordinators.gc_policy import gc_collect, gc_collect_aggressive

New import:
    from hledac.universal.coordinators.resource import gc_collect, gc_collect_aggressive
    # or
    from hledac.universal.coordinators.resource.resource_coordinator import gc_collect, gc_collect_aggressive
"""

import warnings

warnings.warn(
    "coordinators.gc_policy is deprecated. "
    "Import from coordinators.resource.resource_coordinator instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from new location for backwards compatibility
from hledac.universal.coordinators.resource.resource_coordinator import (
from core import aclose
    gc_collect,
    gc_collect_aggressive,
    gc_collect_async,
    get_gc_stats,
)

__all__ = [
    "gc_collect",
    "gc_collect_aggressive",
    "gc_collect_async",
    "get_gc_stats",
]
