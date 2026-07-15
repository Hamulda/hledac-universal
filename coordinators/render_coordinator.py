"""
DEPRECATED: render_coordinator moved to archive/ on 2026-07-15.
===============================================================

This module is a shim for backwards compatibility.
The original implementation has been moved to:
    archive/coordinators_deprecated_2026_07_15/render_coordinator.py

To use the archived implementation:
    from archive.coordinators_deprecated_2026_07_15.render_coordinator import RenderCoordinator

No further development will occur on this module.
"""
import warnings

warnings.warn(
    "hledac.universal.coordinators.render_coordinator is deprecated and has been "
    "moved to archive/coordinators_deprecated_2026_07_15/. "
    "Import from there for continued access.",
    DeprecationWarning,
    stacklevel=2,
)


def __getattr__(name: str):
    """Lazy import to avoid circular dependency."""
    from archive.coordinators_deprecated_2026_07_15.render_coordinator import (
        RenderCoordinator,
        RenderResult,
    )
    mapping = {
        'RenderCoordinator': RenderCoordinator,
        'RenderResult': RenderResult,
    }
    if name in mapping:
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    'RenderCoordinator',
    'RenderResult',
]
