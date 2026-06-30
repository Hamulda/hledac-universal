"""Shared fixtures for sprint 8BI probe tests."""


import time
from typing import Generator

import pytest

from hledac.universal.runtime.sprint_lifecycle import SprintLifecycleManager


@pytest.fixture
def manager() -> Generator[SprintLifecycleManager, None, None]:
    """Fresh manager instance for each test."""
    mgr = SprintLifecycleManager(
        sprint_duration_s=1800.0,
        windup_lead_s=180.0,
    )
    yield mgr


@pytest.fixture
def manager_started(manager: SprintLifecycleManager) -> SprintLifecycleManager:
    """Pre-started manager (WARMUP phase)."""
    manager.start(now_monotonic=100.0)
    return manager


@pytest.fixture
def t0() -> float:
    """Fake monotonic time base for deterministic tests."""
    return 100.0


class FakeClock:
    """Monotonically increasing fake clock for deterministic testing."""

    def __init__(self, start: float = 100.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds

    @property
    def now(self) -> float:
        return self._t
