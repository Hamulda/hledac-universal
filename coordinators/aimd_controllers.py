"""
coordinators/aimd_controllers.py — DEPRECATED: Import from coordinators.resource.resource_coordinator
=============================================================================================

This module is DEPRECATED. All functionality has been moved to:
    coordinators.resource.resource_coordinator

Legacy import (still works for backwards compatibility):
    from coordinators.aimd_controllers import AIMDController

New import:
    from coordinators.resource import AIMDController
    # or
    from coordinators.resource.resource_coordinator import AIMDController
"""

import warnings

warnings.warn(
    "coordinators.aimd_controllers is deprecated. "
    "Import from coordinators.resource.resource_coordinator instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from new location for backwards compatibility
from coordinators.resource.resource_coordinator import AIMDController

__all__ = ["AIMDController"]
