"""
UMAGovernor — Canonical pressure state for Hledac Universal.

Provides:
- PressureState enum: canonical NORMAL, ELEVATED, HIGH, CRITICAL
- Mapping to/from UMAState (core/resource_governor.py)
- UMAGovernor Protocol for type-safe delegation

Issue #15: Resource governor — UMAGovernor jako canonical pressure state
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from hledac.universal.core.resource_governor import GovernorDecision


class PressureState(StrEnum):
    """
    Canonical memory pressure state for Hledac Universal.

    Replaces UMAState (core/resource_governor.py) and
    MemoryPressureLevel (coordinators/memory_coordinator.py) as the
    single source of truth for pressure state.

    Values match string literals for serialization (DuckDB, JSON, LMDB).
    """

    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"


# Mapping from UMAState (core/resource_governor.py) to PressureState
UMAStateToPressureState: dict[str, PressureState] = {
    "ok": PressureState.NORMAL,
    "soft_warn": PressureState.ELEVATED,
    "warn": PressureState.HIGH,
    "critical": PressureState.CRITICAL,
    "emergency": PressureState.CRITICAL,
}

# Reverse mapping from PressureState to UMAState (for logging/compat)
PressureStateToUMAState: dict[PressureState, str] = {
    PressureState.NORMAL: "ok",
    PressureState.ELEVATED: "soft_warn",
    PressureState.HIGH: "warn",
    PressureState.CRITICAL: "critical",
}


def uma_state_to_pressure_state(uma_state: str) -> PressureState:
    """
    Convert UMAState string to canonical PressureState.

    Args:
        uma_state: UMAState string value ("ok", "soft_warn", "warn", "critical", "emergency")

    Returns:
        Corresponding PressureState value

    Raises:
        ValueError: If uma_state is not a valid UMAState value
    """
    if uma_state not in UMAStateToPressureState:
        raise ValueError(f"Unknown UMAState value: {uma_state!r}")
    return UMAStateToPressureState[uma_state]


def pressure_state_to_uma_state(pressure_state: PressureState) -> str:
    """
    Convert PressureState to UMAState string.

    Args:
        pressure_state: PressureState enum value

    Returns:
        Corresponding UMAState string value
    """
    return PressureStateToUMAState[pressure_state]


class UMAGovernor(Protocol):
    """
    Protocol for UMA memory pressure governors.

    Both M1ResourceGovernor (core/resource_governor.py) and
    UniversalMemoryCoordinator (coordinators/memory_coordinator.py)
    implement this protocol for unified pressure state access.
    """

    async def get_pressure(self) -> PressureState:
        """Get current canonical pressure state."""
        ...

    async def evaluate(self) -> "GovernorDecision":
        """Evaluate and return governance decision."""
        ...

    def telemetry(self) -> dict[str, Any]:
        """Return telemetry data for monitoring."""
        ...
