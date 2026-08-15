"""
Smoke test: scheduler_v2 module-level imports are resolvable against the filesystem.
F350M-R: verifies SprintLifecycleManager import is wired to the correct module.
"""
from __future__ import annotations

import pytest
from _core import aclose


def test_scheduler_v2_basic_import() -> None:
    """SprintSchedulerV2 must be importable without triggering ModuleNotFoundError."""
    from runtime.scheduler_v2.scheduler import SprintSchedulerV2

    assert callable(SprintSchedulerV2)


def test_scheduler_v2_protocol_imports() -> None:
    """SprintContext and InitResult must be resolvable from protocol.py."""
    from runtime.scheduler_v2.protocol import InitResult, SprintContext

    assert callable(SprintContext)
    assert callable(InitResult)


def test_sprint_lifecycle_manager_resolves() -> None:
    """
    SprintLifecycleManager lives in runtime.sprint_lifecycle, NOT in the removed
    runtime.scheduler_lifecycle_manager module. This test ensures the import chain
    used inside SprintSchedulerV2._run_internal() (scheduler.py:214) resolves.
    """
    from runtime.sprint_lifecycle import SprintLifecycleManager

    assert callable(SprintLifecycleManager)
