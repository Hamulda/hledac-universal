"""STEP 4 Phase 6 — Consolidated V2 initialization: Bootstrap + Injector.

F350M-R / A2 / A7-FIX.

A7-FIX: Root-cause muffling antipattern remediation.
- Narrowed except blocks to specific exception types (ImportError, RuntimeError, OSError)
- Full traceback logging before InitResult.failure
- Exception chaining with __cause__ preservation
- Fail-loud for critical services (DuckDB, Governor) with clear diagnostics

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
import traceback
import time as _t
from typing import TYPE_CHECKING, Any, Callable

from operator import attrgetter, itemgetter
import msgspec

from hledac.universal.runtime.scheduler_v2.protocol import InitResult
from hledac.universal.utils.asyncx import parallel, safe_create_task

if TYPE_CHECKING:
    pass


def _hasattr_safe(obj: Any, attr: str) -> bool:
    """Safe hasattr that doesn't trigger AttributeError on __getattr__."""
    try:
        return hasattr(obj, attr)
    except Exception:  # noqa: BLE001
        return False


def _init_failure(
    exc: BaseException,
    elapsed_ms: float,
    logger: _logging.Logger,
    service_name: str,
    *,
    reraise: bool = False,
) -> InitResult[Any]:
    """Create InitResult.failure with full traceback logging.

    A7-FIX: This replaces `InitResult.failure(str(e), ms)` which muffled
    root causes. Now logs the complete traceback for debugging while still
    returning InitResult for fail-soft services.

    Args:
        exc: The exception that caused the init failure
        elapsed_ms: Time taken before failure (for logging/InitResult)
        logger: Logger instance for traceback output
        service_name: Human-readable service name for log messages
        reraise: If True, also raise the exception after logging (fail-loud)

    Returns:
        InitResult with error message including traceback summary

    Raises:
        (optional) The original exception if reraise=True
    """
    # Capture full traceback for diagnostics
    tb_summary = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    # Log at ERROR level for critical services, WARNING for optional ones
    logger.error(
        "[A7-FIX] %s init failed after %.1fms.\n"
        "Root cause preserved:\n%s"
        "\nException: %s: %s",
        service_name,
        elapsed_ms,
        tb_summary,
        type(exc).__name__,
        str(exc),
    )

    if reraise:
        raise

    # Include traceback summary in error for forensic debugging
    # A7-FIX: Increased from 500 to 2000 chars to capture deeply nested exception chains
    # While this slightly increases InitResult payload size, it preserves critical context
    # for debugging complex failure scenarios (e.g., import cascades, runtime composition)
    tb_excerpt = tb_summary[:2000] if len(tb_summary) > 2000 else tb_summary
    return InitResult.failure(
        f"[A7] {type(exc).__name__}: {exc}\nTrace: {tb_excerpt}",
        elapsed_ms,
    )


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

    __slots__ = (
        "_scheduler",
        "_config",
        "_result",
        "_cancel_event",
        "_ctx",
        "_governor",
        "_hermes_engine",
        "_evidence_log",
        "_sidecar_orchestrator",
        "_lifecycle",
        "_acquisition_plan",
    )

    def __init__(self, scheduler: Any) -> None:
        # Type guard: reject non-object types early
        if not hasattr(scheduler, "__dict__") and not hasattr(scheduler, "__slots__"):
            raise TypeError(
                f"V2Init requires an object with __dict__ or __slots__, "
                f"got {type(scheduler).__name__}"
            )
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
        from hledac.universal.runtime.scheduler.core.lifecycle import SprintLifecycleAdapter
        from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

        # Store cancel_event on scheduler (used by scheduler.run() and aclose)
        object.__setattr__(self._scheduler, "_cancel_event", cancel_event)

        # Lifecycle manager (canonical state machine)
        _lifecycle_mgr = SprintLifecycleManager(
            sprint_duration_s=self._config.sprint_duration_s if self._config else 1800.0,
            windup_lead_s=self._config.windup_lead_s if self._config else 180.0,
        )
        object.__setattr__(self._scheduler, "_lifecycle", _lifecycle_mgr)

        # [F-1 P0] Lifecycle runner: wraps manager via adapter to provide
        # windup_guard() and string current_phase (SprintLifecycleManager lacks both).
        # Runner is the mechanical boundary; manager is the canonical state.
        _lifecycle_adapter = SprintLifecycleAdapter(_lifecycle_mgr)
        _lifecycle_runner = SprintLifecycleRunner(_lifecycle_mgr, _lifecycle_adapter)
        object.__setattr__(self._scheduler, "_runner", _lifecycle_runner)

        # [ULTIMATE]-002: Wire cognitive saturation detector into lifecycle manager.
        # The detector monitors entity discovery rate and triggers WINDUP when
        # discovery stops for configured persistence period (default: 3 min).
        _cs_detector = None
        try:
            from hledac.universal.runtime.cognitive_saturation_detector import CognitiveSaturationDetector
            from hledac.universal.coordinators.fetch_coordinator import set_cognitive_saturation_detector

            _cs_detector = CognitiveSaturationDetector()
            _lifecycle_mgr.set_cognitive_saturation_detector(_cs_detector)
            # Also register detector with FetchCoordinator via global registry
            set_cognitive_saturation_detector(_cs_detector)
            _logging.getLogger(__name__).info(
                "[ULTIMATE]-002] CognitiveSaturationDetector wired: window=%.0fs, persist=%.0fs, min_active=%.0fs",
                _cs_detector._window_s,
                _cs_detector._persist_s,
                _cs_detector._min_active_s,
            )
        except (ImportError, AttributeError, TypeError) as _cs_exc:
            # A7-FIX: Narrowed to specific exceptions; fail-soft (cognitive saturation is non-critical)
            _logging.getLogger(__name__).warning(
                "[ULTIMATE]-002] Failed to wire CognitiveSaturationDetector (non-critical): %s", _cs_exc
            )

        # [FINAL]-019-08: Wire DEGRADED phase transitions to rayon pool resize.
        # When the lifecycle enters DEGRADED, RayonPoolManager drops to (2, 2)
        # threads to reduce memory/thermal pressure. Callback fires even if
        # the rayon manager is not yet initialized (fail-soft).
        def _on_degraded_enter(from_phase, to_phase):
            from hledac.universal.core.isolated_executors import get_rayon_pool_manager
            try:
                rm = get_rayon_pool_manager()
                rm.set_phase("DEGRADED")
            except (RuntimeError, OSError):
                # A7-FIX: Narrowed — rayon manager may not be initialized yet
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

        # [A1-FIX]: Assert critical service availability after parallel init.
        # Before this fix, failures in _lazy_imports.py were silent → services None.
        # Now we log which services failed and raise AssertionError for critical ones.
        _bootstrap_logger = _logging.getLogger(__name__)
        _failed_services: list[str] = []
        _service_results = [
            ("duckdb_store", _duckdb_store),
            ("governor", _governor),
            ("hermes_engine", _hermes_engine),
            ("evidence_log", _evidence_log),
        ]
        for _svc_name, _svc_result in _service_results:
            if _svc_result is None or (_hasattr_safe(_svc_result, "value") and _svc_result.value is None):
                _svc_error = getattr(_svc_result, "error", None) if _svc_result else "Unknown"
                _failed_services.append(f"{_svc_name}: {_svc_error or 'init failed'}")
                _bootstrap_logger.error(
                    "[V2Init] Service init failed: %s — error: %s",
                    _svc_name,
                    _svc_error or "unknown",
                )

        if _failed_services:
            _bootstrap_logger.error(
                "[V2Init] CRITICAL: %d service(s) failed to initialize: %s",
                len(_failed_services),
                "; ".join(_failed_services),
            )
            # A1: Raise AssertionError for critical services — fail fast, don't silently degrade
            # DuckDB and Governor are considered critical for sprint operation.
            _critical_failed = [
                s for s in _failed_services
                if "duckdb" in s.lower() or "governor" in s.lower()
            ]
            if _critical_failed:
                raise AssertionError(
                    f"[A1-CRITICAL] V2Init cannot start: critical services unavailable: "
                    f"{'; '.join(_critical_failed)}. "
                    f"This indicates _lazy_imports.py is missing or modules failed to import. "
                    f"Check that hledac/universal/_lazy_imports.py exists."
                )

        object.__setattr__(self._scheduler, "_governor", _governor)
        object.__setattr__(self._scheduler, "_hermes_engine", _hermes_engine)
        object.__setattr__(self._scheduler, "_evidence_log", _evidence_log)

        # P0-5 FIX: Start the lifecycle manager (BOOT → WARMUP transition).
        # Without this, _started_at stays None, phase stays BOOT forever,
        # tick() is a no-op, and DEGRADED/WINDUP phases are unreachable.
        _lifecycle_mgr.start()
        # [F-1 P0] Initialize runner: sets _wall_clock_start and prev_phase.
        # Must be called after manager.start() so adapter has valid phase state.
        _lifecycle_runner.setup()

        # META-001: Inject DuckDB store into CrossSprintGate for pre-fetch gating
        try:
            from hledac.universal.knowledge.cross_sprint_gate import get_cross_sprint_gate
            _gate = get_cross_sprint_gate()
            _duckdb_raw = _duckdb_store.value if hasattr(_duckdb_store, 'value') else _duckdb_store
            _gate.inject_duckdb_store(_duckdb_raw)
        except (ImportError, AttributeError, RuntimeError):
            # A7-FIX: Narrowed to specific exceptions; fail-soft (gate injection is non-critical)
            pass

        # SidecarOrchestrator (needs duckdb — runs after)
        _sidecar_orch = await self._init_sidecar_orchestrator(query)
        # [A1-FIX]: Log sidecar orchestrator init status
        if _sidecar_orch is None or (_hasattr_safe(_sidecar_orch, "value") and _sidecar_orch.value is None):
            _sidecar_error = getattr(_sidecar_orch, "error", None) if _sidecar_orch else "Unknown"
            _bootstrap_logger.error(
                "[V2Init] SidecarOrchestrator init failed — error: %s",
                _sidecar_error or "unknown",
            )
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
            runner=_lifecycle_runner,  # [F-1 P0] SprintLifecycleRunner (has windup_guard + string current_phase)
            lifecycle=_lifecycle_mgr,
        )
        object.__setattr__(self._scheduler, "_ctx", _updated_ctx)
        self._ctx = _updated_ctx

        # Hermes prewarm (fire-and-forget)
        safe_create_task(self._prewarm_hermes())

    # ── Declarative injections ──────────────────────────────────────────────────

    def _build_injection_kwargs(
        self,
        inj,
        *,
        duckdb_store: Any,
        rl_train_mode: bool,
        sprint_id: str,
        resume_from: dict | None,
        resume_step: int,
        query_hash: str,
    ) -> dict[str, Any]:
        """Build factory kwargs based on injection type."""
        kwargs: dict[str, Any] = {}
        name = inj.name

        if name == "policy_manager":
            kwargs["rl_train_mode"] = rl_train_mode
        elif name in ("duckdb_store", "prefetch_pipeline"):
            kwargs["duckdb_store"] = duckdb_store
        elif name == "meta_reasoning_coordinator":
            kwargs["duckdb_store"] = duckdb_store
            kwargs["sprint_id"] = sprint_id
            kwargs["resume_from"] = resume_from
            kwargs["resume_step"] = resume_step
            kwargs["query_hash"] = query_hash
            # SILICON-05: wire semantic gravity field
            self._inject_gravity_field()
        return kwargs

    def _inject_gravity_field(self) -> None:
        """Create and inject semantic gravity field into scheduler."""
        try:
            from hledac.universal.knowledge.semantic_gravity import SemanticGravityField
            _gravity_field = SemanticGravityField()
            gravity_inject = getattr(self._scheduler, "inject_gravity_field", None)
            if gravity_inject:
                gravity_inject(_gravity_field)
        except (ImportError, AttributeError, TypeError):
            # A7-FIX: Narrowed to specific exceptions (gravity field is non-critical)
            pass

    def _inject_object(self, inj, obj: Any) -> None:
        """Inject object into scheduler using standard inject pattern."""
        inj_method = getattr(self._scheduler, f"inject_{inj.name}", None)
        if inj_method and obj is not None:
            inj_method(obj)

    def _inject_prefetch_pipeline(self, obj: tuple[Any, Any]) -> None:
        """Inject prefetch pipeline with its temporal predictor."""
        if obj is None:
            return
        prefetch_pipeline, temporal_predictor = obj
        inj_method = getattr(self._scheduler, "inject_prefetch_pipeline", None)
        if inj_method:
            inj_method(prefetch_pipeline)
        tp_inject = getattr(self._scheduler, "inject_temporal_predictor", None)
        if tp_inject and temporal_predictor is not None:
            tp_inject(temporal_predictor)

    def _warmup_evidence_log(
        self,
        sprint_id: str,
        query: str,
        sprint_duration_s: float,
        windup_lead_s: float,
    ) -> None:
        """WARMUP event on EvidenceLog created in _bootstrap."""
        _elog_raw = getattr(self._scheduler, "_evidence_log", None)
        if _elog_raw is None:
            return
        _elog = _elog_raw.value if hasattr(_elog_raw, "value") else _elog_raw
        if _elog is None:
            return
        try:
            _evidence_log_init(_elog, sprint_id, query, sprint_duration_s, windup_lead_s)
        except (TypeError, AttributeError, RuntimeError) as e:
            # A7-FIX: Narrowed to specific exceptions that evidence_log_init may raise
            # This is fail-soft (evidence warmup is non-critical)
            _logging.getLogger(__name__).warning(
                "[V2Init] EvidenceLog warmup failed (non-critical): %s", e
            )

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

        sorted_injections = sorted(INJECTIONS, key=attrgetter("order"))

        for inj in sorted_injections:
            if inj.gate_attr is not None and getattr(flags, inj.gate_attr, False):
                logger.debug("V2Init: %s skipped (gate: %s)", inj.name, inj.gate_attr)
                continue

            factory_kwargs = self._build_injection_kwargs(
                inj,
                duckdb_store=duckdb_store,
                rl_train_mode=rl_train_mode,
                sprint_id=sprint_id,
                resume_from=resume_from,
                resume_step=resume_step,
                query_hash=query_hash,
            )

            try:
                obj = inj.factory(**factory_kwargs)
                if inj.name == "prefetch_pipeline":
                    self._inject_prefetch_pipeline(obj)
                else:
                    self._inject_object(inj, obj)
            except (ImportError, TypeError, RuntimeError, OSError) as e:
                # A7-FIX: Narrowed to specific exception types that factories may raise
                if inj.fail_soft:
                    logger.debug("V2Init: %s injection failed (fail-soft): %s", inj.name, e)
                else:
                    # A1-FIX: Critical service — re-raise with full context
                    raise type(e)(f"{inj.name} injection failed: {e}") from e

        self._warmup_evidence_log(sprint_id, query, sprint_duration_s, windup_lead_s)

    # ── Acquisition plan ───────────────────────────────────────────────────────

    async def _build_acquisition_plan(self, query: str) -> Any | None:
        from hledac.universal.runtime.scheduler_v2.acquisition import AcquisitionPlanBuilder

        try:
            builder = AcquisitionPlanBuilder()
            plan = await builder.build(query, self._config)
            return plan
        except (ImportError, TypeError, RuntimeError, OSError) as e:
            # A7-FIX: Narrowed to specific exceptions; log traceback for debugging
            _logging.getLogger(__name__).warning(
                "[V2Init] AcquisitionPlan build failed: %s", e
            )
            return None

    # ── Individual init methods ─────────────────────────────────────────────────

    async def _init_duckdb_store(self, query: str) -> InitResult[Any]:
        _t0 = _t.monotonic()
        _logger = _logging.getLogger(__name__)
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
            except (ImportError, OSError, RuntimeError) as sem_exc:
                # Fail-soft: SemanticStore injection failure must not block DuckDB init.
                # buffer_findings() is already fail-open (no-op when store is None).
                _logger.warning(
                    "[V2Init] SemanticStore injection failed (non-critical): %s",
                    sem_exc,
                )

            return InitResult.success(store, (_t.monotonic() - _t0) * 1000)

        except ImportError as e:
            # A7-FIX: Narrow to ImportError — module/dependency not found
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "DuckDB", reraise=True)

        except (OSError, RuntimeError) as e:
            # A7-FIX: Narrow to OS/Runtime errors — actual DuckDB failures
            # A1: DuckDB is critical → fail loud immediately with full traceback
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "DuckDB", reraise=True)

        except Exception as e:
            # A7-FIX: Catch-all for unexpected errors (NameError, AttributeError, etc.)
            # These are usually programmer errors — fail loud immediately
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "DuckDB", reraise=True)

    async def _init_governor(self) -> InitResult[Any]:
        _t0 = _t.monotonic()
        _logger = _logging.getLogger(__name__)
        try:
            from hledac.universal._lazy_imports import get_M1ResourceGovernor
            M1ResourceGovernor = get_M1ResourceGovernor()
            governor = M1ResourceGovernor()
            return InitResult.success(governor, (_t.monotonic() - _t0) * 1000)

        except ImportError as e:
            # A7-FIX: Narrow to ImportError — module/dependency not found
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "Governor", reraise=True)

        except (OSError, RuntimeError) as e:
            # A7-FIX: Narrow to OS/Runtime errors — governor runtime failures
            # A1: Governor is critical → fail loud immediately with full traceback
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "Governor", reraise=True)

        except Exception as e:
            # A7-FIX: Catch-all for unexpected errors (NameError, AttributeError, etc.)
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "Governor", reraise=True)

    async def _init_hermes_engine(self, query: str) -> InitResult[Any]:
        _t0 = _t.monotonic()
        _logger = _logging.getLogger(__name__)
        try:
            from hledac.universal._lazy_imports import get_Hermes3Engine
            Hermes3Engine = get_Hermes3Engine()
            engine = Hermes3Engine()
            return InitResult.success(engine, (_t.monotonic() - _t0) * 1000)

        except ImportError as e:
            # A7-FIX: Narrow to ImportError — MLX/model dependencies not available
            # Hermes is optional (synthesis) → fail-soft
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "Hermes", reraise=False)

        except (OSError, RuntimeError) as e:
            # A7-FIX: Narrow to OS/Runtime errors — model loading failures
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "Hermes", reraise=False)

        except Exception as e:
            # A7-FIX: Catch-all for unexpected errors
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "Hermes", reraise=False)

    async def _init_evidence_log(self) -> InitResult[Any]:
        _t0 = _t.monotonic()
        _logger = _logging.getLogger(__name__)
        try:
            from hledac.universal._lazy_imports import get_EvidenceLog
            EvidenceLog = get_EvidenceLog()
            elog = EvidenceLog()
            return InitResult.success(elog, (_t.monotonic() - _t0) * 1000)

        except ImportError as e:
            # A7-FIX: Narrow to ImportError — evidence log module not available
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "EvidenceLog", reraise=False)

        except (OSError, RuntimeError) as e:
            # A7-FIX: Narrow to OS/Runtime errors — storage/initialization failures
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "EvidenceLog", reraise=False)

        except Exception as e:
            # A7-FIX: Catch-all for unexpected errors
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "EvidenceLog", reraise=False)

    async def _init_sidecar_orchestrator(self, query: str) -> InitResult[Any]:
        _t0 = _t.monotonic()
        _logger = _logging.getLogger(__name__)
        try:
            from hledac.universal._lazy_imports import get_SidecarOrchestrator
            SidecarOrchestrator = get_SidecarOrchestrator()
            orch = SidecarOrchestrator(
                result_sink=self._result,
                governor=self._governor,
                scheduler=self._scheduler,
            )
            return InitResult.success(orch, (_t.monotonic() - _t0) * 1000)

        except ImportError as e:
            # A7-FIX: Narrow to ImportError — sidecar module not available
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "SidecarOrchestrator", reraise=False)

        except (OSError, RuntimeError, TypeError) as e:
            # A7-FIX: TypeError can occur from wrong kwargs (historical bug)
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "SidecarOrchestrator", reraise=False)

        except Exception as e:
            # A7-FIX: Catch-all for unexpected errors
            return _init_failure(e, (_t.monotonic() - _t0) * 1000, _logger, "SidecarOrchestrator", reraise=False)

    async def _prewarm_hermes(self) -> None:
        try:
            _engine = self._hermes_engine.value if self._hermes_engine else None
            if _engine is not None and hasattr(_engine, "prepare"):
                await asyncio.sleep(0.1)
                await _engine.prepare()
        except (RuntimeError, OSError, AttributeError):
            # A7-FIX: Narrowed to specific exceptions; prewarm failure is non-critical
            pass


class _FlagsEmpty:
    """Neutral flags object used when flags=None."""

    __slots__ = ()

    def __getattr__(self, _name: str) -> bool:
        return False

    def __getitem__(self, _name: str) -> bool:
        return False
