"""
DEPRECATED: swarm_coordinator moved to archive/ on 2026-07-15.
=================================================================

This module is a shim for backwards compatibility.
The original implementation has been moved to:
    archive/coordinators_deprecated_2026_07_15/swarm_coordinator.py

To use the archived implementation:
    from archive.coordinators_deprecated_2026_07_15.swarm_coordinator import UniversalSwarmCoordinator

No further development will occur on this module.
"""
import warnings

warnings.warn(
    "hledac.universal.coordinators.swarm_coordinator is deprecated and has been "
    "moved to archive/coordinators_deprecated_2026_07_15/. "
    "Import from there for continued access.",
    DeprecationWarning,
    stacklevel=2,
)


def __getattr__(name: str):
    """Lazy import to avoid circular dependency."""
    from archive.coordinators_deprecated_2026_07_15.swarm_coordinator import (
        UniversalSwarmCoordinator,
        SwarmState,
        SwarmMetrics,
        AdaptiveStrategy,
        SwarmAgent,
    )
    mapping = {
        'UniversalSwarmCoordinator': UniversalSwarmCoordinator,
        'SwarmState': SwarmState,
        'SwarmMetrics': SwarmMetrics,
        'AdaptiveStrategy': AdaptiveStrategy,
        'SwarmAgent': SwarmAgent,
    }
    if name in mapping:
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    'UniversalSwarmCoordinator',
    'SwarmState',
    'SwarmMetrics',
    'AdaptiveStrategy',
    'SwarmAgent',
]
