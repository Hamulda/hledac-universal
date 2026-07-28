"""STEP 4 Phase 5.1 — SprintBootstrap: service initialization for SprintSchedulerV2.

F350M-R / Issue SC-06.

Extracts all service-booting logic from SprintSchedulerV2:
- _initialize_sprint_run()
- _init_duckdb_store() / _init_governor() / _init_hermes_engine()
- _init_evidence_log() / _init_sidecar_orchestrator()
- _build_acquisition_plan()
- _prewarm_hermes()

Usage:
    bootstrap = SprintBootstrap(scheduler)   # scheduler provides config, result, cancel_event
    await bootstrap.run(query, wall_clock_start, ctx)  # mutates ctx in-place
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

from hledac.universal.utils.async_helpers import parallel, safe_create_task
from hledac.universal.runtime.scheduler_v2.protocol import InitResult

if TYPE_CHECKING:
    pass


class SprintBootstrap:
    """Service initialization bootstrap for SprintSchedulerV2.

    Encapsulates all fail-soft service init (DuckDB, Governor, Hermes, EvidenceLog,
    SidecarOrchestrator) and the concurrent parallel() boot sequence.
    """

    __slots__ = ()

    def __init__(self, scheduler: Any) -> None:
        # Store scheduler reference for lazy attribute access during init
        self._scheduler = scheduler
        self._config = getattr(scheduler, "_config", None)
        self._result = getattr(scheduler, "_result", None)
        self._cancel_event = getattr(scheduler, "_cancel_event", None)
        self._ctx: Any = getattr(scheduler, "_ctx", None)
        self._governor: Any = None
        self._hermes_engine: Any = None
        self._evidence_log: Any = None
        self._sidecar_orchestrator: Any = None
        self._lifecycle: Any = None
        self._acquisition_plan: Any = None
        self._container: Any = None

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(self, query: str, wall_clock_start: float, ctx: Any) -> Any:
        """Initialize all services and return updated (ctx, governor, hermes_engine,
        evidence_log, sidecar_orchestrator, lifecycle, acquisition_plan).

        Mutates ctx in-place via with_cycle/with_services.
        """
        from hledac.universal.runtime.sprint_lifecycle import SprintLifecycleManager

        _lifecycle_mgr = SprintLifecycleManager(
            config=self._config,
            result=self._result,
            cancel_event=self._cancel_event,
        )
        object.__setattr__(self._scheduler, "_lifecycle", _lifecycle_mgr)
        object.__setattr__(self._scheduler, "_runner", _lifecycle_mgr)

        # Build acquisition plan before with_cycle (Issue #029)
        _acq_plan = await self._build_acquisition_plan(query)
        object.__setattr__(self._scheduler, "_acquisition_plan", _acq_plan)

        # Concurrent service boot (Issue #21) — max(all), not sum(all)
        _init_result = await parallel(
            [
                self._init_duckdb_store(query),
                self._init_governor(),
                self._init_hermes_engine(query),
                self._init_evidence_log(),
            ],
            policy="collect",
            ctx="scheduler_v2:_init_services",
        )
        (
            _duckdb_store,
            _governor,
            _hermes_engine,
            _evidence_log,
        ) = _init_result.ok

        object.__setattr__(self._scheduler, "_governor", _governor)
        object.__setattr__(self._scheduler, "_hermes_engine", _hermes_engine)
        object.__setattr__(self._scheduler, "_evidence_log", _evidence_log)

        # SidecarOrchestrator needs duckdb reference — runs after duckdb init
        _sidecar_orch = await self._init_sidecar_orchestrator(query)
        object.__setattr__(self._scheduler, "_sidecar_orchestrator", _sidecar_orch)

        # Update ctx with per-cycle state + services
        # SC-05 FIX: duckdb_store ONLY in with_services() (InitResult-wrapped in SprintContext).
        # NOT in with_cycle() — _CycleState has no duckdb_store field.

        # F350M-R: Register rust.force from env vars into sprint-scoped container.
        # Resolution priority: env HLEDAC_FORCE_RUST/PYTHON → container → auto-probe.
        _container = self._build_container()
        object.__setattr__(self._scheduler, "_container", _container)

        _updated_ctx = ctx.with_cycle(
            wall_clock_start=wall_clock_start,
            lifecycle=_lifecycle_mgr,
            acquisition_plan=_acq_plan,
            sidecar_orchestrator=_sidecar_orch.value if _sidecar_orch else None,
            hermes_engine=_hermes_engine.value if _hermes_engine else None,
            evidence_log=_evidence_log.value if _evidence_log else None,
        ).with_services(
            duckdb_store=_duckdb_store,
            governor=_governor,
            hermes_engine=_hermes_engine,
            evidence_log=_evidence_log,
            runner=_lifecycle_mgr,
            lifecycle=_lifecycle_mgr,
            container=_container,
        )
        object.__setattr__(self._scheduler, "_ctx", _updated_ctx)

        # Hermes prewarm (fire-and-forget)
        safe_create_task(self._prewarm_hermes())

        return _updated_ctx

    # ── Acquisition plan ─────────────────────────────────────────────────────

    async def _build_acquisition_plan(self, query: str) -> Any | None:
        """Build AcquisitionPlan from query (Issue #029 fix)."""
        from hledac.universal.runtime.scheduler_v2.acquisition import AcquisitionPlanBuilder

        try:
            builder = AcquisitionPlanBuilder()
            plan = await builder.build(query, self._config)
            return plan
        except Exception:
            return None

    # ── Individual init methods (fail-soft) ─────────────────────────────────

    async def _init_duckdb_store(self, query: str) -> InitResult[Any]:
        """Initialize DuckDBShadowStore (fail-soft)."""
        import logging as _logging
        import time as _t

        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_DuckDBShadowStore

            DuckDBShadowStore = get_DuckDBShadowStore()
            store = DuckDBShadowStore()
            await store.async_init()
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] DuckDB init OK (%.1fms)", _elapsed)
            return InitResult.success(store, _elapsed)
        except Exception as e:
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] DuckDB init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    async def _init_governor(self) -> InitResult[Any]:
        """Initialize M1ResourceGovernor (fail-soft)."""
        import logging as _logging
        import time as _t

        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_M1ResourceGovernor

            M1ResourceGovernor = get_M1ResourceGovernor()
            governor = M1ResourceGovernor()
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] Governor init OK (%.1fms)", _elapsed)
            return InitResult.success(governor, _elapsed)
        except Exception as e:
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] Governor init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    async def _init_hermes_engine(self, _query: str) -> InitResult[Any]:
        """Initialize Hermes3Engine (fail-soft)."""
        import logging as _logging
        import time as _t

        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_Hermes3Engine

            Hermes3Engine = get_Hermes3Engine()
            engine = Hermes3Engine()
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] Hermes engine init OK (%.1fms)", _elapsed)
            return InitResult.success(engine, _elapsed)
        except Exception as e:
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] Hermes engine init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    async def _init_evidence_log(self) -> InitResult[Any]:
        """Initialize EvidenceLog (fail-soft)."""
        import logging as _logging
        import time as _t

        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_EvidenceLog

            EvidenceLog = get_EvidenceLog()
            elog = EvidenceLog()
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] EvidenceLog init OK (%.1fms)", _elapsed)
            return InitResult.success(elog, _elapsed)
        except Exception as e:
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] EvidenceLog init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    async def _init_sidecar_orchestrator(self, _query: str) -> InitResult[Any]:
        """Initialize SidecarOrchestrator (fail-soft)."""
        import logging as _logging
        import time as _t

        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_SidecarOrchestrator

            SidecarOrchestrator = get_SidecarOrchestrator()
            orch = SidecarOrchestrator(
                result_sink=self._result,
                governor=self._governor,
                scheduler=self._scheduler,
            )
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] SidecarOrchestrator init OK (%.1fms)", _elapsed)
            return InitResult.success(orch, _elapsed)
        except Exception as e:
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] SidecarOrchestrator init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    # ── Hermes prewarm ───────────────────────────────────────────────────────

    async def _prewarm_hermes(self) -> None:
        """Fire-and-forget Hermes warm-up."""
        try:
            _engine = self._hermes_engine.value if self._hermes_engine else None
            if _engine is not None and hasattr(_engine, "prepare"):
                await asyncio.sleep(0.1)  # let run() settle
                await _engine.prepare()
        except Exception:
            pass  # fire-and-forget

    # ── Container (A3) ─────────────────────────────────────────────────────────

    def _build_container(self) -> Any:
        """
        F350M-R: Build sprint-scoped ServiceContainer with rust.force registered.

        Resolution priority at probe time:
          1. env HLEDAC_FORCE_RUST=1     → force_rust()
          2. env HLEDAC_FORCE_PYTHON=1  → force_python()
          3. container.get('rust.force') → RustForce(python=True/False)
          4. default                    → auto-probe

        Container is attached to AccelBackend via set_container() so that
        rust.force is available before first domain access.
        """
        from hledac.universal.core.container import ServiceContainer
        from hledac.universal.core.rust_backend import RustForce, set_container

        _container = ServiceContainer()

        # Register rust.force from env vars (sprint-scoped override)
        _force_python_env = os.environ.get("HLEDAC_FORCE_PYTHON", "0") == "1"
        _force_rust_env = os.environ.get("HLEDAC_FORCE_RUST", "0") == "1"
        if _force_python_env:
            _container.register(
                "rust.force",
                factory=lambda: RustForce(python=True),
                scope="singleton",
            )
        elif _force_rust_env:
            _container.register(
                "rust.force",
                factory=lambda: RustForce(rust=True),
                scope="singleton",
            )
        # else: rust.force not registered → auto-probe at probe time

        # Wire container into AccelBackend before any domain access
        set_container(_container)

        return _container
