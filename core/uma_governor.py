"""
UMAGovernor — Backward-Compatible Re-Export Stub
===============================================

F350M-R: Canonical PressureState and UMAGovernor Protocol
have been merged into core/resource_governor.py.

This stub exists for backward compatibility with any code that
imports from this module. New code should import from core/resource_governor.py.

Migration (F350M-R):
    OLD: from hledac.universal.core.uma_governor import PressureState
    NEW: from hledac.universal.core.resource_governor import PressureState

Canonical source:
    core/resource_governor.py:PressureState (StrEnum)
    core/resource_governor.py:UMAGovernor (Protocol)
    core/resource_governor.py:UMAStateToPressureState (dict)
    core/resource_governor.py:PressureStateToUMAState (dict)
    core/resource_governor.py:uma_state_to_pressure_state (function)
    core/resource_governor.py:pressure_state_to_uma_state (function)
"""

from __future__ import annotations

# Re-export everything from canonical source for backward compatibility
from hledac.universal.core.resource_governor import (
    PressureState,
    UMAGovernor,
    UMAStateToPressureState,
    PressureStateToUMAState,
    uma_state_to_pressure_state,
    pressure_state_to_uma_state,
)

__all__ = [
    "PressureState",
    "UMAGovernor",
    "UMAStateToPressureState",
    "PressureStateToUMAState",
    "uma_state_to_pressure_state",
    "pressure_state_to_uma_state",
]
