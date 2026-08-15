"""
coordinators/aimd_controllers.py — DEPRECATED: Import from coordinators.resource.resource_coordinator
=============================================================================================

This module is DEPRECATED. All functionality has been moved to:
    coordinators.resource.resource_coordinator

Legacy import (still works for backwards compatibility):
    from hledac.universal.coordinators.aimd_controllers import AIMDController

New import:
    from hledac.universal.coordinators.resource import AIMDController
    # or
    from hledac.universal.coordinators.resource.resource_coordinator import AIMDController
"""

import warnings

warnings.warn(
    "coordinators.aimd_controllers is deprecated. "
    "Import from coordinators.resource.resource_coordinator instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from new location for backwards compatibility
from hledac.universal.coordinators.resource.resource_coordinator import AIMDController
from core import aclose

__all__ = ["AIMDController", "make_enrich_aimd"]


def make_enrich_aimd() -> AIMDController:
    """Factory for EnrichStage AIMD controller — ceiling=16, aggressive scaling."""
    return AIMDController(
        min_value=1,
        max_value=16,
        additive_increment=1,
        decrease_factor=0.75,
        success_threshold=2,
        name="enrich",
    )
