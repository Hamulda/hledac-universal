"""
Backward-compatibility stub for bounded_collections.

This module was moved to core/bounded_collections.py as part of the
F320 architecture refactoring. This stub re-exports everything from
core for backward compatibility with existing code.

New code should import directly from core:
    from hledac.universal.core.bounded_collections import BoundedList

This stub will be removed in a future version.
"""
from __future__ import annotations

import warnings

# Re-export from core location
from hledac.universal.core.bounded_collections import (
    BoundedList,
    SlottedBoundedList,
)

__all__ = [
    "BoundedList",
    "SlottedBoundedList",
]

# Emit deprecation warning when imported
warnings.warn(
    "utils.bounded_collections is deprecated; "
    "import from hledac.universal.core.bounded_collections instead",
    DeprecationWarning,
    stacklevel=2,
)
