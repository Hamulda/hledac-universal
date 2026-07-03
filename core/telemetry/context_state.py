"""
Context state via ContextVar — TaskGroup child task visibility.

Issue 8.4: Sprint phase and stealth state must be visible to all
TaskGroup child tasks without passing explicit parameters.
"""

from __future__ import annotations

import contextvars

# Sprint phase ContextVar — set by SprintScheduler.phase_transition_callback
_sprint_phase_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "sprint_phase", default=""
)


def set_sprint_phase(phase: str) -> None:
    """Set the current sprint phase for TaskGroup child task visibility."""
    _sprint_phase_var.set(phase)


def get_sprint_phase() -> str:
    """Get the current sprint phase."""
    return _sprint_phase_var.get()


# Stealth layer ContextVar — set by SprintScheduler when stealth is enabled
_stealth_enabled_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "stealth_enabled", default=False
)


def set_stealth_enabled(enabled: bool) -> None:
    """Set the stealth layer enabled state for TaskGroup child task visibility."""
    _stealth_enabled_var.set(enabled)


def is_stealth_enabled() -> bool:
    """Check if stealth layer is currently enabled."""
    return _stealth_enabled_var.get()
