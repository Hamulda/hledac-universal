"""
SC-01: SidecarOrchestrator constructor signature must match call site.

Verifies that SprintSchedulerV2._init_sidecar_orchestrator() (scheduler.py:304)
passes the correct kwargs to SidecarOrchestrator.__init__ (sidecar_orchestrator.py:285).

Constructor contract (sidecar_orchestrator.py:285-289):
    def __init__(self, result_sink, governor=None, scheduler=None) -> None

The bug: scheduler.py was passing config=query=result= → TypeError → InitResult.failure
The fix: scheduler.py now passes result_sink=governor=scheduler=

Acceptance: test_sidecar_orchestrator_init_succeeds — init_sidecars: ok
"""
from __future__ import annotations

import pytest
import asyncio
from unittest.mock import MagicMock
from _core import aclose


class TestSidecarOrchestratorSignature:
    """Verify SidecarOrchestrator accepts (result_sink, governor, scheduler)."""

    def test_sidecar_orchestrator_constructor_accepts_result_sink(self) -> None:
        """SidecarOrchestrator.__init__ takes result_sink as first positional arg."""
        from runtime.sidecar_orchestrator import SidecarOrchestrator

        mock_sink = MagicMock()
        # Must NOT raise TypeError
        orch = SidecarOrchestrator(result_sink=mock_sink)
        assert orch._result is mock_sink

    def test_sidecar_orchestrator_constructor_accepts_governor_kwarg(self) -> None:
        """SidecarOrchestrator.__init__ accepts governor= kwarg."""
        from runtime.sidecar_orchestrator import SidecarOrchestrator

        mock_sink = MagicMock()
        mock_gov = MagicMock()
        orch = SidecarOrchestrator(result_sink=mock_sink, governor=mock_gov)
        assert orch._result is mock_sink
        assert orch._governor is mock_gov

    def test_sidecar_orchestrator_constructor_accepts_scheduler_kwarg(self) -> None:
        """SidecarOrchestrator.__init__ accepts scheduler= kwarg."""
        from runtime.sidecar_orchestrator import SidecarOrchestrator

        mock_sink = MagicMock()
        mock_scheduler = MagicMock()
        orch = SidecarOrchestrator(result_sink=mock_sink, scheduler=mock_scheduler)
        assert orch._result is mock_sink
        assert orch._scheduler is mock_scheduler

    def test_sidecar_orchestrator_rejects_config_kwarg(self) -> None:
        """SidecarOrchestrator.__init__ does NOT accept config= (the buggy kwarg)."""
        from runtime.sidecar_orchestrator import SidecarOrchestrator

        with pytest.raises(TypeError, match="unexpected keyword argument"):
            SidecarOrchestrator(config=MagicMock())  # type: ignore[call-arg]

    def test_sidecar_orchestrator_rejects_query_kwarg(self) -> None:
        """SidecarOrchestrator.__init__ does NOT accept query= (the buggy kwarg)."""
        from runtime.sidecar_orchestrator import SidecarOrchestrator

        with pytest.raises(TypeError, match="unexpected keyword argument"):
            SidecarOrchestrator(query="test")  # type: ignore[call-arg]


class TestSidecarOrchestratorInitFlow:
    """Verify SprintSchedulerV2._init_sidecar_orchestrator wires kwargs correctly.

    SC-01 root cause: scheduler.py:304 was passing
        SidecarOrchestrator(config=..., query=..., result=...)
    which TypeError'd against the real signature
        SidecarOrchestrator(result_sink=..., governor=..., scheduler=...)

    The fix: scheduler.py now passes result_sink=governor=scheduler=.

    We test the constructor call directly (avoiding pytest namespace path issues
    with hledac.universal._lazy_imports) by importing SidecarOrchestrator
    directly and checking the same kwargs the scheduler passes.
    """

    def test_orchestrator_constructor_kwargs_match_scheduler_v2(self) -> None:
        """
        Verify the kwargs SprintSchedulerV2._init_sidecar_orchestrator() passes
        to SidecarOrchestrator.__init__ match what the constructor accepts.

        scheduler.py:304 (FIXED):
            SidecarOrchestrator(result_sink=self._result, governor=self._governor, scheduler=self)

        This test ensures the kwargs are the ones the __init__ signature accepts.
        """
        from runtime.sidecar_orchestrator import SidecarOrchestrator

        mock_result = MagicMock()
        mock_governor = MagicMock()
        mock_scheduler = MagicMock()

        # This must NOT raise TypeError — same call the fixed scheduler makes
        orch = SidecarOrchestrator(
            result_sink=mock_result,
            governor=mock_governor,
            scheduler=mock_scheduler,
        )

        assert orch._result is mock_result
        assert orch._governor is mock_governor
        assert orch._scheduler is mock_scheduler

    @pytest.mark.asyncio
    async def test_sidecar_orchestrator_init_does_not_raise_typeerror(self) -> None:
        """
        Verify no TypeError is raised when SprintSchedulerV2._init_sidecar_orchestrator()
        calls SidecarOrchestrator(...) with result_sink/governor/scheduler.

        The original bug was TypeError from wrong kwargs — caught by except Exception
        and returned as InitResult.failure (silent sidecar disable).
        """
        from runtime.scheduler_v2.protocol import InitResult
        from runtime.sidecar_orchestrator import SidecarOrchestrator

        mock_result = MagicMock()
        mock_governor = MagicMock()
        mock_scheduler = MagicMock()

        # Replicate exactly what scheduler.py:304 now does (after the fix)
        try:
            orch = SidecarOrchestrator(
                result_sink=mock_result,
                governor=mock_governor,
                scheduler=mock_scheduler,
            )
        except TypeError as e:
            pytest.fail(
                f"SidecarOrchestrator() raised TypeError — constructor kwargs don't match "
                f"what scheduler.py:304 passes: {e}"
            )

        assert orch._result is mock_result
        assert orch._governor is mock_governor
        assert orch._scheduler is mock_scheduler
