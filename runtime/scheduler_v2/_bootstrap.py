"""STEP 4 Phase 6.1 — Bootstrap Init: single-source service initialization.

F350M-R / Pattern #15.

Single home for all bootstrap init logic previously duplicated across:
  - SprintBootstrap (bootstrap.py) — service init + lifecycle
  - V2Init._bootstrap() / _init_* methods (_v2_init.py)

Usage:
    from runtime.scheduler_v2._bootstrap import bootstrap_schedulerv2, BootstrapResult
    result = await bootstrap_schedulerv2(
        scheduler=scheduler,
        query=query,
        wall_clock_start=wall_clock_start,
        ctx=ctx,
        cancel_event=cancel_event,
    )
    # result.ctx, result.governor, result.hermes_engine, etc.
"""

from __future__ import annotations

import asyncio
import os
import time as _t
from typing import TYPE_CHECKING, Any

import msgspec

from hledac.universal.utils.async_helpers import parallel, safe_create_task
from runtime.scheduler_v2.protocol import InitResult

if TYPE_CHECKING:
    pass


# ─────────────────────────────────────────────────────────────────
# BootstrapResult — structured return value
# ─────────────────────────────────────────────────────────────────


class BootstrapResult(msgspec.Struct, gc=False, frozen=True):
    """Result of bootstrap_schedulerv2()."""

    ctx: Any
    governor: Any
    hermes_engine: Any
    evidence_log: Any
    sidecar_orchestrator: Any
    lifecycle: Any
    acquisition_plan: Any
    container: Any


# ─────────────────────────────────────────────────────────────────
# INJECTION TABLE — moved here to unify with _v2_init.py
# ─────────────────────────────────────────────────────────────────


class _Injection(msgspec.Struct, frozen=True, gc=False):
    """One declarative injection entry."""

    name: str
    factory: "Any"
    gate_attr: str | None = None
    fail_soft: bool = True
    order: int = 10


# F350M-R: evidence_log_factory and evidence_log_init centralized in
# runtime/_evidence_log_init.py to avoid duplication between v2 and legacy paths.
from hledac.universal.runtime._evidence_log_init import (
    evidence_log_factory as _evidence_log_factory,
    evidence_log_init as _evidence_log_init,
)


def _policy_manager_factory(*, rl_train_mode: bool) -> Any:
    from hledac.universal.rl.sprint_policy_manager import SprintPolicyManager
    return SprintPolicyManager(enabled=True, rl_train_mode=rl_train_mode)


def _duckdb_store_factory(*, duckdb_store: Any) -> Any:
    return duckdb_store


def _communication_layer_factory() -> Any:
    from hledac.universal.layers import get_communication_layer
    return get_communication_layer()


def _stealth_layer_factory() -> Any:
    from hledac.universal.layers import get_stealth_layer
    return get_stealth_layer()


def _ghost_layer_factory() -> Any:
    from hledac.universal.layers import get_ghost_layer
    return get_ghost_layer()


def _security_coordinator_factory() -> Any:
    from hledac.universal.coordinators.security_coordinator import (
        UniversalSecurityCoordinator,
    )
    return UniversalSecurityCoordinator(max_concurrent=3)


def _prefetch_oracle_factory() -> Any:
    from hledac.universal.prefetch.prefetch_oracle_integration import (
        PrefetchOracleIntegration,
    )
    return PrefetchOracleIntegration()


def _prefetch_pipeline_factory(*, duckdb_store: Any) -> Any:
    from hledac.universal.layers import get_temporal_signal_layer
    from hledac.universal.prefetch.prefetch_pipeline import (
        ContinuousPrefetchPipeline,
    )
    from hledac.universal.prefetch.temporal_predictor import (
        TemporalIOCPredictor,
    )

    temporal_predictor = TemporalIOCPredictor(
        temporal_layer=get_temporal_signal_layer(),
        duckdb_store=duckdb_store,
    )
    prefetch_pipeline = ContinuousPrefetchPipeline(
        prefetch_oracle=temporal_predictor,
        prefetch_cache=None,
        queue_depth=50,
        concurrent_fetches=3,
    )
    return (prefetch_pipeline, temporal_predictor)


INJECTIONS: tuple[_Injection, ...] = (
    _Injection(name="evidence_log", factory=_evidence_log_factory, fail_soft=True, order=0),
    _Injection(name="policy_manager", factory=_policy_manager_factory, fail_soft=False, order=1),
    _Injection(name="duckdb_store", factory=_duckdb_store_factory, fail_soft=False, order=1),
    _Injection(
        name="communication_layer",
        factory=_communication_layer_factory,
        gate_attr="no_communication",
        fail_soft=True,
        order=2,
    ),
    _Injection(
        name="stealth_layer",
        factory=_stealth_layer_factory,
        gate_attr="no_stealth",
        fail_soft=True,
        order=3,
    ),
    _Injection(
        name="ghost_layer",
        factory=_ghost_layer_factory,
        gate_attr="no_ghost",
        fail_soft=True,
        order=4,
    ),
    _Injection(
        name="security_coordinator",
        factory=_security_coordinator_factory,
        gate_attr="no_stealth",
        fail_soft=True,
        order=5,
    ),
    _Injection(name="prefetch_oracle", factory=_prefetch_oracle_factory, fail_soft=True, order=6),
    _Injection(name="prefetch_pipeline", factory=_prefetch_pipeline_factory, fail_soft=True, order=7),
)


# ─────────────────────────────────────────────────────────────────
# FlagsEmpty — neutral flags object when flags=None
# ─────────────────────────────────────────────────────────────────


class _FlagsEmpty:
    """Neutral flags object used when flags=None."""

    __slots__ = ()

    def __getattr__(self, _name: str) -> bool:
        return False

    def __getitem__(self, _name: str) -> bool:
        return False


# ─────────────────────────────────────────────────────────────────
# bootstrap_schedulerv2 — single-source bootstrap
# ─────────────────────────────────────────────────────────────────


async def bootstrap_schedulerv2(
    *,
    scheduler: Any,
    query: str,
    wall_clock_start: float,
    ctx: Any,
    cancel_event: asyncio.Event,
    config: Any,
    result_sink: Any,
    logger: Any,
) -> BootstrapResult:
    """Bootstrap all core services concurrently.

    Initializes in order:
      1. SprintLifecycleManager
      2. AcquisitionPlanBuilder
      3. DuckDBShadowStore, M1ResourceGovernor, Hermes3Engine, EvidenceLog (parallel)
      4. SidecarOrchestrator (after duckdb)
      5. ServiceContainer with rust.force env registration

    Returns BootstrapResult with all initialized services.
    Mutates ctx in-place via with_cycle/with_services.
    """
    import logging as _logging

    _config = config
    _result = result_sink

    # ── Lifecycle manager ────────────────────────────────────────────

    from runtime.sprint_lifecycle import SprintLifecycleManager

    _lifecycle_mgr = SprintLifecycleManager(
        config=_config,
        result=_result,
        cancel_event=cancel_event,
    )
    object.__setattr__(scheduler, "_lifecycle", _lifecycle_mgr)
    object.__setattr__(scheduler, "_runner", _lifecycle_mgr)
    object.__setattr__(scheduler, "_cancel_event", cancel_event)

    # ── Acquisition plan ─────────────────────────────────────────────

    _acq_plan = await _build_acquisition_plan(query, _config)
    object.__setattr__(scheduler, "_acquisition_plan", _acq_plan)

    # ── Concurrent service boot ──────────────────────────────────────

    _init_result = await parallel(
        [
            _init_duckdb_store(query),
            _init_governor(),
            _init_hermes_engine(query),
            _init_evidence_log(),
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

    object.__setattr__(scheduler, "_governor", _governor)
    object.__setattr__(scheduler, "_hermes_engine", _hermes_engine)
    object.__setattr__(scheduler, "_evidence_log", _evidence_log)

    # ── SidecarOrchestrator (needs duckdb — runs after) ──────────────

    _sidecar_orch = await _init_sidecar_orchestrator(
        query, result_sink=_result, governor=_governor, scheduler=scheduler
    )
    object.__setattr__(scheduler, "_sidecar_orchestrator", _sidecar_orch)

    # ── ServiceContainer (rust.force env registration) ────────────────

    _container = _build_container()
    object.__setattr__(scheduler, "_container", _container)

    # ── Update ctx ───────────────────────────────────────────────────

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
    object.__setattr__(scheduler, "_ctx", _updated_ctx)

    # Hermes prewarm (fire-and-forget)
    safe_create_task(_prewarm_hermes(_hermes_engine))

    return BootstrapResult(
        ctx=_updated_ctx,
        governor=_governor,
        hermes_engine=_hermes_engine,
        evidence_log=_evidence_log,
        sidecar_orchestrator=_sidecar_orch,
        lifecycle=_lifecycle_mgr,
        acquisition_plan=_acq_plan,
        container=_container,
    )


# ─────────────────────────────────────────────────────────────────
# _SprintBootstrapCompat — backward-compat wrapper for legacy callers
# ─────────────────────────────────────────────────────────────────


class _SprintBootstrapCompat:
    """Backward-compat wrapper that presents the old SprintBootstrap API.

    Wraps bootstrap_schedulerv2() and stores results on the scheduler
    in the same layout the old SprintBootstrap did.
    """

    __slots__ = ()

    def __init__(self, scheduler: Any) -> None:
        self._scheduler = scheduler
        self._config = getattr(scheduler, "_config", None)
        self._result = getattr(scheduler, "_result", None)
        self._cancel_event = getattr(scheduler, "_cancel_event", None)
        self._ctx = getattr(scheduler, "_ctx", None)
        self._governor: Any = None
        self._hermes_engine: Any = None
        self._evidence_log: Any = None
        self._sidecar_orchestrator: Any = None
        self._lifecycle: Any = None
        self._acquisition_plan: Any = None
        self._container: Any = None

    async def run(self, query: str, wall_clock_start: float, ctx: Any) -> Any:
        """Run bootstrap and mutate scheduler in-place (legacy API)."""
        result = await bootstrap_schedulerv2(
            scheduler=self._scheduler,
            query=query,
            wall_clock_start=wall_clock_start,
            ctx=ctx,
            cancel_event=self._cancel_event,
            config=self._config,
            result_sink=self._result,
            logger=None,
        )
        self._ctx = result.ctx
        self._governor = result.governor
        self._hermes_engine = result.hermes_engine
        self._evidence_log = result.evidence_log
        self._sidecar_orchestrator = result.sidecar_orchestrator
        self._lifecycle = result.lifecycle
        self._acquisition_plan = result.acquisition_plan
        self._container = result.container
        return result.ctx
# ─────────────────────────────────────────────────────────────────
# apply_injections — declarative injection applier
# ─────────────────────────────────────────────────────────────────


async def apply_injections(
    *,
    scheduler: Any,
    injections: tuple[_Injection, ...],
    query: str,
    flags: Any,
    sprint_id: str,
    sprint_duration_s: float,
    windup_lead_s: float,
    duckdb_store: Any,
    rl_train_mode: bool,
    logger: Any,
) -> None:
    """Apply all declarative injections to scheduler.

    Sorted by injection.order, skips when flags.gate_attr is True.
    """
    if injections is None or len(injections) == 0:
        return
    if flags is None:
        flags = _FlagsEmpty()

    sorted_injections = sorted(injections, key=lambda i: i.order)

    for inj in sorted_injections:
        if inj.gate_attr is not None and getattr(flags, inj.gate_attr, False):
            logger.debug("V2Init: %s skipped (gate: %s)", inj.name, inj.gate_attr)
            continue

        factory_kwargs: dict[str, Any] = {}
        if inj.name == "evidence_log":
            factory_kwargs["sprint_id"] = sprint_id
        elif inj.name == "policy_manager":
            factory_kwargs["rl_train_mode"] = rl_train_mode
        elif inj.name in ("duckdb_store", "prefetch_pipeline"):
            factory_kwargs["duckdb_store"] = duckdb_store

        try:
            obj = inj.factory(**factory_kwargs)

            if inj.name == "evidence_log" and obj is not None:
                _evidence_log_init(obj, sprint_id, query, sprint_duration_s, windup_lead_s)

            if inj.name == "prefetch_pipeline" and obj is not None:
                prefetch_pipeline, temporal_predictor = obj
                inj_method = getattr(scheduler, f"inject_{inj.name}", None)
                if inj_method:
                    inj_method(prefetch_pipeline)
                tp_inject = getattr(scheduler, "inject_temporal_predictor", None)
                if tp_inject and temporal_predictor is not None:
                    tp_inject(temporal_predictor)
            else:
                inj_method = getattr(scheduler, f"inject_{inj.name}", None)
                if inj_method and obj is not None:
                    inj_method(obj)

        except Exception as e:
            if inj.fail_soft:
                logger.debug("V2Init: %s injection failed (fail-soft): %s", inj.name, e)
            else:
                raise

    # Phase 2: oracle wiring
    _oracle = getattr(scheduler, "_prefetch_oracle", None)
    if _oracle is not None and duckdb_store is not None:
        try:
            _oracle.inject_duckdb_store(duckdb_store)
        except Exception as e:
            logger.debug("V2Init: oracle duckdb_store wiring failed (fail-soft): %s", e)

        try:
            from hledac.universal.knowledge.graph_service import _get_graph

            _ioc_graph = _get_graph()
            if _ioc_graph is not None:
                _oracle.inject_ioc_graph(_ioc_graph)
        except Exception as e:
            logger.debug("V2Init: IOC graph injection failed (fail-soft): %s", e)


# ─────────────────────────────────────────────────────────────────
# Internal init helpers (fail-soft)
# ─────────────────────────────────────────────────────────────────


async def _build_acquisition_plan(query: str, config: Any) -> Any | None:
    from runtime.scheduler_v2.acquisition import AcquisitionPlanBuilder

    try:
        builder = AcquisitionPlanBuilder()
        plan = await builder.build(query, config)
        return plan
    except Exception:
        return None


async def _init_duckdb_store(query: str) -> InitResult[Any]:
    _t0 = _t.monotonic()
    try:
        from hledac.universal._lazy_imports import get_DuckDBShadowStore

        DuckDBShadowStore = get_DuckDBShadowStore()
        store = DuckDBShadowStore()
        await store.async_init()
        return InitResult.success(store, (_t.monotonic() - _t0) * 1000)
    except Exception as e:
        return InitResult.failure(str(e), (_t.monotonic() - _t0) * 1000)


async def _init_governor() -> InitResult[Any]:
    _t0 = _t.monotonic()
    try:
        from hledac.universal._lazy_imports import get_M1ResourceGovernor

        M1ResourceGovernor = get_M1ResourceGovernor()
        governor = M1ResourceGovernor()
        return InitResult.success(governor, (_t.monotonic() - _t0) * 1000)
    except Exception as e:
        return InitResult.failure(str(e), (_t.monotonic() - _t0) * 1000)


async def _init_hermes_engine(query: str) -> InitResult[Any]:
    _t0 = _t.monotonic()
    try:
        from hledac.universal._lazy_imports import get_Hermes3Engine

        Hermes3Engine = get_Hermes3Engine()
        engine = Hermes3Engine()
        return InitResult.success(engine, (_t.monotonic() - _t0) * 1000)
    except Exception as e:
        return InitResult.failure(str(e), (_t.monotonic() - _t0) * 1000)


async def _init_evidence_log() -> InitResult[Any]:
    _t0 = _t.monotonic()
    try:
        from hledac.universal._lazy_imports import get_EvidenceLog

        EvidenceLog = get_EvidenceLog()
        elog = EvidenceLog()
        return InitResult.success(elog, (_t.monotonic() - _t0) * 1000)
    except Exception as e:
        return InitResult.failure(str(e), (_t.monotonic() - _t0) * 1000)


async def _init_sidecar_orchestrator(
    query: str, *, result_sink: Any, governor: Any, scheduler: Any
) -> InitResult[Any]:
    _t0 = _t.monotonic()
    try:
        from hledac.universal._lazy_imports import get_SidecarOrchestrator

        SidecarOrchestrator = get_SidecarOrchestrator()
        orch = SidecarOrchestrator(
            result_sink=result_sink,
            governor=governor,
            scheduler=scheduler,
        )
        return InitResult.success(orch, (_t.monotonic() - _t0) * 1000)
    except Exception as e:
        return InitResult.failure(str(e), (_t.monotonic() - _t0) * 1000)


async def _prewarm_hermes(hermes_engine_init_result: Any) -> None:
    try:
        _engine = hermes_engine_init_result.value if hermes_engine_init_result else None
        if _engine is not None and hasattr(_engine, "prepare"):
            await asyncio.sleep(0.1)  # let run() settle
            await _engine.prepare()
    except Exception:
        pass  # fire-and-forget


def _build_container() -> Any:
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
    from core.container import ServiceContainer
    from core.rust_backend import RustForce, set_container

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
