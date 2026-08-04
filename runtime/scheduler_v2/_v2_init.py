"""STEP 4 Phase 6 — Consolidated V2 initialization: Bootstrap + Injector.

F350M-R / A2.

Single home for all bootstrap + declarative injection logic previously
duplicated across SprintBootstrap, Injector, and entrypoint_injections.

Single responsibility: initialize all services and apply all injections
for SprintSchedulerV2 before run() begins.

Usage:
    from hledac.universal.runtime.scheduler_v2._v2_init import V2Init
    init = V2Init(scheduler)
    await init.run(query, wall_clock_start, ctx, flags=flags,
                    sprint_id=sprint_id, duckdb_store=store,
                    rl_train_mode=False, logger=logger)
"""

from __future__ import annotations

import asyncio
import logging as _logging
import time as _t
from typing import TYPE_CHECKING, Any, Callable

import msgspec

from hledac.universal.runtime.scheduler_v2.protocol import InitResult
from hledac.universal.utils.async_helpers import parallel, safe_create_task

if TYPE_CHECKING:
    pass


# ─────────────────────────────────────────────────────────────────
# INJECTION TABLE — declarative, ordered
# ─────────────────────────────────────────────────────────────────


class _Injection(msgspec.Struct, frozen=True, gc=False):
    """One declarative injection entry."""

    name: str
    factory: "Callable[..., Any]"
    gate_attr: str | None = None
    fail_soft: bool = True
    order: int = 10


# EvidenceLog init from shared module (F350M-R)
from hledac.universal.runtime._shared.evidence_log_shared import (
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


def _meta_reasoning_coordinator_factory(
    *,
    duckdb_store: Any,
    sprint_id: str = "",
    resume_from: dict | None = None,
    resume_step: int = 0,
    query_hash: str = "",  # UNIFIED-006
) -> Any:
    """UNIFIED-006: Create MetaReasoningCoordinator with optional resume state."""
    from hledac.universal.coordinators.meta_reasoning_coordinator import (
        UniversalMetaReasoningCoordinator,
    )
    return UniversalMetaReasoningCoordinator(
        max_concurrent=3,
        duckdb_store=duckdb_store,
        sprint_id=sprint_id,
        resume_from=resume_from,
        resume_step=resume_step,
        query_hash=query_hash,
    )


INJECTIONS: tuple[_Injection, ...] = (
    _Injection(name="policy_manager", factory=_policy_manager_factory, fail_soft=False, order=1),
    _Injection(
        name="duckdb_store", factory=_duckdb_store_factory, fail_soft=False, order=1
    ),
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
    _Injection(
        name="prefetch_pipeline",
        factory=_prefetch_pipeline_factory,
        fail_soft=True,
        order=6,
    ),
    _Injection(
        name="meta_reasoning_coordinator",  # UNIFIED-006
        factory=_meta_reasoning_coordinator_factory,
        fail_soft=True,
        order=7,
    ),
)


# ─────────────────────────────────────────────────────────────────
# V2Init — unified bootstrap + injection
# ─────────────────────────────────────────────────────────────────


class V2Init:
    """Unified V2 initialization: bootstrap + declarative injections.

    Single class that:
      1. Bootstraps core services (DuckDB, Governor, Hermes, EvidenceLog,
         SidecarOrchestrator, SprintLifecycleManager) via parallel()
      2. Applies all declarative injections via apply_injections()
      3. Returns updated ctx with all services wired

    Usage:
        init = V2Init(scheduler)
        ctx = await init.run(query, wall_clock_start, ctx,
                             flags=flags, sprint_id=sprint_id,
                             duckdb_store=store, rl_train_mode=False,
                             logger=logger)
    """

    __slots__ = ()

    def __init__(self, scheduler: Any) -> None:
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

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(
        self,
        query: str,
        wall_clock_start: float,
        ctx: Any,
        *,
        cancel_event: asyncio.Event,
        flags: Any,
        sprint_id: str,
        sprint_duration_s: float,
        windup_lead_s: float,
        duckdb_store: Any,
        rl_train_mode: bool,
        logger: _logging.Logger,
        resume_from: dict | None = None,  # UNIFIED-006: ToT checkpoint nodes
        resume_step: int = 0,             # UNIFIED-006: step at resume point
        query_hash: str = "",             # UNIFIED-006: BLAKE2b-16 of query
    ) -> Any:
        """Initialize all services + apply all injections.

        Mutates ctx in-place via with_cycle/with_services.
        Returns updated SprintContext.
        """
        # 1. Bootstrap core services
        await self._bootstrap(query, wall_clock_start, ctx, cancel_event=cancel_event)

        # 2. Apply declarative injections
        await self._apply_injections(
            query=query,
            flags=flags,
            sprint_id=sprint_id,
            sprint_duration_s=sprint_duration_s,
            windup_lead_s=windup_lead_s,
            duckdb_store=duckdb_store,
            rl_train_mode=rl_train_mode,
            logger=logger,
            resume_from=resume_from,  # UNIFIED-006
            resume_step=resume_step,  # UNIFIED-006
            query_hash=query_hash,    # UNIFIED-006
        )

        return self._ctx

    # ── Bootstrap ─────────────────────────────────────────────────────────────

    async def _bootstrap(
        self, query: str, wall_clock_start: float, ctx: Any, *, cancel_event: asyncio.Event
    ) -> None:
        """Bootstrap core services concurrently."""
        from hledac.universal.runtime.sprint_lifecycle import SprintLifecycleManager

        # Store cancel_event on scheduler (used by scheduler.run() and aclose)
        object.__setattr__(self._scheduler, "_cancel_event", cancel_event)

        # Lifecycle manager
        _lifecycle_mgr = SprintLifecycleManager(
            sprint_duration_s=self._config.sprint_duration_s if self._config else 1800.0,
            windup_lead_s=self._config.windup_lead_s if self._config else 180.0,
        )
        object.__setattr__(self._scheduler, "_lifecycle", _lifecycle_mgr)
        object.__setattr__(self._scheduler, "_runner", _lifecycle_mgr)

        # [FINAL]-019-08: Wire DEGRADED phase transitions to rayon pool resize.
        # When the lifecycle enters DEGRADED, RayonPoolManager drops to (2, 2)
        # threads to reduce memory/thermal pressure. Callback fires even if
        # the rayon manager is not yet initialized (fail-soft).
        def _on_degraded_enter(from_phase, to_phase):
            from hledac.universal.core.isolated_executors import get_rayon_pool_manager
            try:
                rm = get_rayon_pool_manager()
                rm.set_phase("DEGRADED")
            except Exception:
                pass

        _lifecycle_mgr.add_phase_exit_callback(_on_degraded_enter)

        # Acquisition plan
        _acq_plan = await self._build_acquisition_plan(query)
        object.__setattr__(self._scheduler, "_acquisition_plan", _acq_plan)

        # Concurrent service boot
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

        # META-001: Inject DuckDB store into CrossSprintGate for pre-fetch gating
        try:
            from hledac.universal.knowledge.cross_sprint_gate import get_cross_sprint_gate
            _gate = get_cross_sprint_gate()
            _duckdb_raw = _duckdb_store.value if hasattr(_duckdb_store, 'value') else _duckdb_store
            _gate.inject_duckdb_store(_duckdb_raw)
        except Exception:  # noqa: BLE001 — fail-soft; gate injection is non-critical
            pass

        # SidecarOrchestrator (needs duckdb — runs after)
        _sidecar_orch = await self._init_sidecar_orchestrator(query)
        object.__setattr__(self._scheduler, "_sidecar_orchestrator", _sidecar_orch)

        # Update ctx
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
        )
        object.__setattr__(self._scheduler, "_ctx", _updated_ctx)
        self._ctx = _updated_ctx

        # Hermes prewarm (fire-and-forget)
        safe_create_task(self._prewarm_hermes())

    # ── Declarative injections ──────────────────────────────────────────────────

    async def _apply_injections(
        self,
        *,
        query: str,
        flags: Any,
        sprint_id: str,
        sprint_duration_s: float,
        windup_lead_s: float,
        duckdb_store: Any,
        rl_train_mode: bool,
        logger: _logging.Logger,
        resume_from: dict | None = None,  # UNIFIED-006
        resume_step: int = 0,             # UNIFIED-006
        query_hash: str = "",             # UNIFIED-006
    ) -> None:
        """Apply all declarative injections to scheduler."""
        if flags is None:
            flags = _FlagsEmpty()

        sorted_injections = sorted(INJECTIONS, key=lambda i: i.order)

        for inj in sorted_injections:
            if inj.gate_attr is not None and getattr(flags, inj.gate_attr, False):
                logger.debug("V2Init: %s skipped (gate: %s)", inj.name, inj.gate_attr)
                continue

            factory_kwargs: dict[str, Any] = {}
            if inj.name == "policy_manager":
                factory_kwargs["rl_train_mode"] = rl_train_mode
            elif inj.name in ("duckdb_store", "prefetch_pipeline"):
                factory_kwargs["duckdb_store"] = duckdb_store
            elif inj.name == "meta_reasoning_coordinator":  # UNIFIED-006
                factory_kwargs["duckdb_store"] = duckdb_store
                factory_kwargs["sprint_id"] = sprint_id
                factory_kwargs["resume_from"] = resume_from
                factory_kwargs["resume_step"] = resume_step
                factory_kwargs["query_hash"] = query_hash
                # SILICON-05: Create and wire semantic gravity field
                try:
                    from hledac.universal.knowledge.semantic_gravity import (
                        SemanticGravityField,
                    )
                    _gravity_field = SemanticGravityField()
                    factory_kwargs["gravity_field"] = _gravity_field
                    # Also inject into scheduler so the pipeline can push embeddings
                    gravity_inject = getattr(
                        self._scheduler, "inject_gravity_field", None
                    )
                    if gravity_inject:
                        gravity_inject(_gravity_field)
                except Exception:
                    logger.debug(
                        'V2Init: SemanticGravityField init failed — '
                        'continuing without gravity field'
                    )

            try:
                obj = inj.factory(**factory_kwargs)

                if inj.name == "prefetch_pipeline" and obj is not None:
                    prefetch_pipeline, temporal_predictor = obj
                    inj_method = getattr(self._scheduler, f"inject_{inj.name}", None)
                    if inj_method:
                        inj_method(prefetch_pipeline)
                    tp_inject = getattr(self._scheduler, "inject_temporal_predictor", None)
                    if tp_inject and temporal_predictor is not None:
                        tp_inject(temporal_predictor)
                else:
                    inj_method = getattr(self._scheduler, f"inject_{inj.name}", None)
                    if inj_method and obj is not None:
                        inj_method(obj)

            except Exception as e:
                if inj.fail_soft:
                    logger.debug("V2Init: %s injection failed (fail-soft): %s", inj.name, e)
                else:
                    raise

        # WARMUP event on EvidenceLog created in _bootstrap
        _elog_raw = getattr(self._scheduler, "_evidence_log", None)
        if _elog_raw is not None:
            _elog = _elog_raw.value if hasattr(_elog_raw, "value") else _elog_raw
            if _elog is not None:
                try:
                    _evidence_log_init(_elog, sprint_id, query, sprint_duration_s, windup_lead_s)
                except Exception:
                    pass

    # ── Acquisition plan ───────────────────────────────────────────────────────

    async def _build_acquisition_plan(self, query: str) -> Any | None:
        from hledac.universal.runtime.scheduler_v2.acquisition import AcquisitionPlanBuilder

        try:
            builder = AcquisitionPlanBuilder()
            plan = await builder.build(query, self._config)
            return plan
        except Exception:
            return None

    # ── Individual init methods ─────────────────────────────────────────────────

    async def _init_duckdb_store(self, query: str) -> InitResult[Any]:
        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_DuckDBShadowStore
            from hledac.universal.paths import RAMDISK_ROOT

            DuckDBShadowStore = get_DuckDBShadowStore()
            store = DuckDBShadowStore()
            await store.async_init()

            # ARCH-STR-001: Inject SemanticStore for LanceDB-backed embedding buffering.
            # SemanticStore is created and initialized here so that buffer_findings()
            # in DuckDBWriteCoordinator actually persists embeddings to LanceDB.
            try:
                from hledac.universal.knowledge.semantic_store import SemanticStore

                lancedb_path = RAMDISK_ROOT / "lancedb"
                semantic_store = SemanticStore(db_path=lancedb_path)
                await semantic_store.initialize()
                store.inject_semantic_store(semantic_store)
            except Exception as sem_exc:
                # Fail-soft: SemanticStore injection failure must not block DuckDB init.
                # buffer_findings() is already fail-open (no-op when store is None).
                _logging.getLogger(__name__).warning(
                    "[V2Init] SemanticStore injection failed (non-critical): %s",
                    sem_exc,
                )

            return InitResult.success(store, (_t.monotonic() - _t0) * 1000)
        except Exception as e:
            return InitResult.failure(str(e), (_t.monotonic() - _t0) * 1000)

    async def _init_governor(self) -> InitResult[Any]:
        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_M1ResourceGovernor
            M1ResourceGovernor = get_M1ResourceGovernor()
            governor = M1ResourceGovernor()
            return InitResult.success(governor, (_t.monotonic() - _t0) * 1000)
        except Exception as e:
            return InitResult.failure(str(e), (_t.monotonic() - _t0) * 1000)

    async def _init_hermes_engine(self, query: str) -> InitResult[Any]:
        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_Hermes3Engine
            Hermes3Engine = get_Hermes3Engine()
            engine = Hermes3Engine()
            return InitResult.success(engine, (_t.monotonic() - _t0) * 1000)
        except Exception as e:
            return InitResult.failure(str(e), (_t.monotonic() - _t0) * 1000)

    async def _init_evidence_log(self) -> InitResult[Any]:
        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_EvidenceLog
            EvidenceLog = get_EvidenceLog()
            elog = EvidenceLog()
            return InitResult.success(elog, (_t.monotonic() - _t0) * 1000)
        except Exception as e:
            return InitResult.failure(str(e), (_t.monotonic() - _t0) * 1000)

    async def _init_sidecar_orchestrator(self, query: str) -> InitResult[Any]:
        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_SidecarOrchestrator
            SidecarOrchestrator = get_SidecarOrchestrator()
            orch = SidecarOrchestrator(
                result_sink=self._result,
                governor=self._governor,
                scheduler=self._scheduler,
            )
            return InitResult.success(orch, (_t.monotonic() - _t0) * 1000)
        except Exception as e:
            return InitResult.failure(str(e), (_t.monotonic() - _t0) * 1000)

    async def _prewarm_hermes(self) -> None:
        try:
            _engine = self._hermes_engine.value if self._hermes_engine else None
            if _engine is not None and hasattr(_engine, "prepare"):
                await asyncio.sleep(0.1)
                await _engine.prepare()
        except Exception:
            pass


class _FlagsEmpty:
    """Neutral flags object used when flags=None."""

    def __getattr__(self, _name: str) -> bool:
        return False

    def __getitem__(self, _name: str) -> bool:
        return False
