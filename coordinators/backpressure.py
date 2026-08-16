"""
coordinators/backpressure.py — DEPRECATED: Import from coordinators.resource.resource_coordinator
=========================================================================================

This module is DEPRECATED. All functionality has been moved to:
    coordinators.resource.resource_coordinator

Legacy import (still works for backwards compatibility):
    from hledac.universal.coordinators.backpressure import BackpressureMonitor, BackpressureDecision

New import:
    from hledac.universal.coordinators.resource import BackpressureMonitor, BackpressureDecision
    # or
    from hledac.universal.coordinators.resource.resource_coordinator import BackpressureMonitor, BackpressureDecision
"""

import warnings

warnings.warn(
    "coordinators.backpressure is deprecated. "
    "Import from coordinators.resource.resource_coordinator instead.",
    DeprecationWarning,
    stacklevel=2,
    )

# Re-export from new location for backwards compatibility
from hledac.universal.coordinators.resource.resource_coordinator import (
    BackpressureDecision,
    BackpressureMonitor,
    )
from _core import aclose

__all__ = [
    "BackpressureDecision",
    "BackpressureMonitor",
]
