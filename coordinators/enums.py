"""
Coordinator Enums
=================

Shared enums for coordinators. Single source of truth to avoid circular imports.
"""

from __future__ import annotations

from enum import Enum, auto


class MemoryPressureLevel(Enum):
    """Memory pressure levels for M1 8GB optimization."""
    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"