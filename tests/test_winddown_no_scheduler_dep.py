"""SC-07: WinddownOrchestrator must not receive a scheduler instance.

Test verifies that WinddownOrchestrator has no coupling to SprintSchedulerV2
— it receives only SprintContext + lifecycle at runtime, enabling isolated testing.

Run: pytest tests/test_winddown_no_scheduler_dep.py -v
"""

from __future__ import annotations

import asyncio

from runtime.scheduler_v2.winddown import WinddownOrchestrator


class TestSC07WinddownNoSchedulerDep:
    """SC-07: WinddownOrchestrator decoupling from SprintScheduler."""

    def test_winddown_orchestrator_init_no_args(self) -> None:
        """WinddownOrchestrator.__init__ accepts no arguments (SC-07 fix)."""
        orch = WinddownOrchestrator()
        # __slots__ is empty — no _scheduler attribute
        assert not hasattr(orch, "_scheduler")
        assert not hasattr(orch, "_ctx")

    def test_winddown_orchestrator_slots_empty(self) -> None:
        """__slots__ must be empty — no scheduler reference stored (SC-07)."""
        assert WinddownOrchestrator.__slots__ == ()

    def test_winddown_run_requires_explicit_ctx(self) -> None:
        """run() must receive ctx explicitly — no implicit scheduler coupling (SC-07)."""

        # Create a minimal mock context with required attributes
        class _MinimalResult:
            export_paths: list[str] = []
            final_phase: str = "WINDDOWN"

        class _MinimalConfig:
            export_enabled: bool = False

        class _MinimalCycle:
            wall_clock_start: float = 0.0

        class _MinimalCtx:
            config: _MinimalConfig = _MinimalConfig()
            query: str = "test query"
            result: _MinimalResult = _MinimalResult()
            duckdb_store = None
            graph_service = None
            hermes_engine = None
            governor = None
            evidence_log = None
            ct_log_client = None
            runner = None
            lifecycle = None
            cancel_event: asyncio.Event = asyncio.Event()
            bg_tasks: set = set()
            _cycle: _MinimalCycle = _MinimalCycle()
            _export_result = None
            _acquisition_plan = None
            _lifecycle = None

        # run() must be callable with explicit ctx — no scheduler required
        import inspect

        sig = inspect.signature(WinddownOrchestrator.run)
        params = list(sig.parameters.keys())
        # Expected: self, ctx, lifecycle, query
        assert params == ["self", "ctx", "lifecycle", "query"], (
            f"run() signature changed: {params}. Must be (ctx, lifecycle, query) — no implicit scheduler coupling."
        )

    def test_winddown_orchestrator_no_scheduler_coupling(self) -> None:
        """WinddownOrchestrator must not store or reference scheduler (SC-07).

        This test confirms the architectural fix: WinddownOrchestrator is now
        a stateless orchestrator that receives all state via SprintContext.
        SprintSchedulerV2 no longer passes `self` to WinddownOrchestrator.
        """
        # Verify the class has no private attributes that could hold scheduler ref
        for attr in WinddownOrchestrator.__slots__:
            assert attr != "_scheduler", (
                f"Found _scheduler in __slots__: {WinddownOrchestrator.__slots__}. "
                "SC-07 fix requires complete removal of scheduler coupling."
            )

        # WinddownOrchestrator should be instantiable with no args
        orch = WinddownOrchestrator()

        # Verify no _scheduler attribute exists (was removed in SC-07)
        assert not any(attr.startswith("_scheduler") for attr in dir(orch))

        # Verify the instance has no unexpected state
        assert orch.__slots__ == ()
