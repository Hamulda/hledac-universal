"""
Shared fixtures for Sprint F206C lifecycle runner refactor tests.
"""

from unittest.mock import MagicMock

import pytest


class MockSprintLifecycle:
    """
    Mock of SprintLifecycleManager that supports the property-based current_phase interface.

    Unlike MagicMock (where properties don't work as descriptors on instances),
    this class properly supports the current_phase property interface used by
    SprintLifecycleRunner.teardown().
    """

    sprint_duration_s: float = 1800.0
    windup_lead_s: float = 180.0

    def __init__(self):
        self._started_at: float | None = None
        self._current_phase = MagicMock()
        self._current_phase.name = "BOOT"
        self._abort_requested = False
        self._abort_reason = ""
        self._export_started = False
        self._teardown_started = False

    def start(self, now_monotonic=None):
        self._started_at = now_monotonic or 100.0
        self._current_phase.name = "WARMUP"

    def tick(self, now_monotonic=None):
        now = now_monotonic or self._started_at or 100.0
        if self._started_at is None:
            return self._current_phase
        elapsed = now - self._started_at
        remaining = self.sprint_duration_s - elapsed
        if remaining <= self.windup_lead_s:
            self._current_phase.name = "WINDUP"
        else:
            self._current_phase.name = "ACTIVE"
        return self._current_phase

    def should_enter_windup(self, now_monotonic=None):
        now = now_monotonic or self._started_at or 100.0
        if self._started_at is None:
            return False
        remaining = self.sprint_duration_s - (now - self._started_at)
        return remaining <= self.windup_lead_s

    def is_terminal(self):
        return self._teardown_started

    def request_abort(self, reason=""):
        self._abort_requested = True
        self._abort_reason = reason

    def mark_export_started(self, _now_monotonic=None):
        self._export_started = True
        # Only set _current_phase.name if it's a mock (not an enum)
        if isinstance(self._current_phase, MagicMock):
            self._current_phase.name = "EXPORT"

    def mark_teardown_started(self, _now_monotonic=None):
        self._teardown_started = True
        # Only set _current_phase.name if it's a mock (not an enum)
        if isinstance(self._current_phase, MagicMock):
            self._current_phase.name = "TEARDOWN"

    def snapshot(self):
        return {
            "current_phase": self._current_phase.name,
            "abort_requested": self._abort_requested,
        }

    def recommended_tool_mode(self, now_monotonic=None, thermal_state="nominal"):
        now = now_monotonic or self._started_at or 100.0
        remaining = self.sprint_duration_s - (now - self._started_at) if self._started_at else 9999.0
        if self._abort_requested or remaining <= 30.0 or thermal_state == "critical":
            return "panic"
        if remaining <= self.windup_lead_s or thermal_state in ("throttled", "fair"):
            return "prune"
        return "normal"

    @property
    def current_phase(self):
        """Mirrors SprintLifecycleManager.current_phase property."""
        return self._current_phase


@pytest.fixture
def mock_lifecycle():
    """Mock SprintLifecycleManager with proper current_phase property."""
    return MockSprintLifecycle()
