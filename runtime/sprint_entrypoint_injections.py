"""
Declarative injection table for SprintSchedulerV2.

Replaces 9 hand-written try/except blocks (~70 LOC) with a single
structured loop that is:
  - Explicit: every injectable is listed in one place
  - Testable: each _Injection is independently callable
  - Auditable: gate_attr, fail_soft are visible at a glance
  - Maintainable: adding a new injection = one tuple entry

Architecture notes
-----------------
inject_* methods on SprintSchedulerV2 follow two patterns:

  1. "Ctx-updating" — also calls self._ctx.with_cycle(...) to propagate
     the service into SprintContext so lanes can access it:
       evidence_log, communication_layer, stealth_layer, ghost_layer,
       prefetch_oracle, duckdb_store, prefetch_pipeline,
       temporal_predictor, pivot_planner, analyst_workbench,
       forensics_enricher, enrichment_services, privacy_layer, ioc_graph

  2. "Private-only" — sets a _private attribute only:
       policy_manager, security_coordinator, multimodal_enricher,
       source_economics

Ordering constraints
--------------------
  1. duckdb_store MUST be injected before any oracle or graph wiring.
  2. prefetch_oracle MUST be injected before P3-1 oracle wiring block.
  3. Layers (communication/stealth/ghost) should be injected before
     security_coordinator so the coordinator can see them.
  4. EvidenceLog is special: initialised with async .initialize() and
     its create_event is also wrapped in try/except.  Handled as a
     dedicated "priority" injection at slot 0.

Usage
-----
    from runtime.sprint_entrypoint_injections import INJECTIONS, apply_injections

    await apply_injections(scheduler, flags, sprint_id=sprint_id,
                           logger=logging.getLogger(__name__))

Each _Injection.factory is called synchronously (no I/O) so this is safe
to run inside an async context without spawning threads.

F350M-R / ISSUE-008
"""

from __future__ import annotations

import logging

import msgspec
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    pass


class _Injection(msgspec.Struct, frozen=True):
    """
    One declarative injection entry.

    Attributes
    ----------
    name : str
        The suffix of the scheduler.inject_<name>() method.
    factory : Callable[[], Any]
        Zero-argument callable that returns the object to inject.
        Called exactly once per injection.  All imports are lazy
        (inside the factory) so module-level import failures are
        impossible.
    gate_attr : str | None
        If set, the injection is skipped when ``getattr(flags, gate_attr, False)``
        is truthy.  Used for --no-* opt-out flags.
    fail_soft : bool
        If True (default), exceptions from factory() or inject_*() are logged
        at DEBUG level and suppressed.  If False the exception propagates.
    order : int
       ascending, lower numbers run first.  Priority 0 = EvidenceLog
        (has async .initialize() that must run before everything else).
    """

    name: str
    factory: Callable[..., Any]
    gate_attr: str | None = None
    fail_soft: bool = True
    order: int = 10


# -------------------------------------------------------------------
# EvidenceLog — special: async .initialize() + create_event wrapped in
# try/except inside the factory so the async work runs before the
# inject call.  Must run FIRST (order=0).
# -------------------------------------------------------------------


def _evidence_log_factory(*, sprint_id: str) -> Any:
    """Build and initialize EvidenceLog; returns the live instance."""
    from hledac.universal.evidence_log import EvidenceLog

    elog = EvidenceLog(run_id=sprint_id, enable_persist=True)
    # The async init (.initialize() + WARMUP event) is handled in
    # _evidence_log_init() which is called by apply_injections().
    return elog


def _evidence_log_init(elog: Any, sprint_id: str, query: str, duration_s: float, windup_lead_s: float) -> None:
    """Call async initialize() on EvidenceLog and record WARMUP event."""
    import asyncio

    try:
        # Python 3.12+: get_running_loop() in async context, fallback to new_event_loop() for sync context.
        # This pattern avoids the deprecated get_event_loop() in Python 3.14+.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        if loop.is_running():
            # Create a task and keep strong reference so it isn't GC'd
            _task = asyncio.create_task(elog.initialize())
            object.__setattr__(elog, "_init_task", _task)
        else:
            loop.run_until_complete(elog.initialize())
    except Exception:
        pass  # fail-soft: initialize() failures never block sprint

    try:
        elog.create_event(
            event_type="observation",
            payload={
                "phase": "WARMUP",
                "sprint_id": sprint_id,
                "query": query,
                "duration_s": duration_s,
                "windup_lead_s": windup_lead_s,
            },
            confidence=1.0,
        )
    except Exception:
        pass  # fail-soft: evidence events never block sprint


# -------------------------------------------------------------------
# PolicyManager — unconditional, no flag gate, no try/except
# -------------------------------------------------------------------


def _policy_manager_factory(*, rl_train_mode: bool) -> Any:
    from hledac.universal.rl.sprint_policy_manager import SprintPolicyManager

    return SprintPolicyManager(enabled=True, rl_train_mode=rl_train_mode)


# -------------------------------------------------------------------
# DuckDB store — unconditional, no try/except (store is always valid).
# Passed directly from outer scope (not created by factory).
# -------------------------------------------------------------------


def _duckdb_store_factory(*, duckdb_store: Any) -> Any:
    """Dummy factory — duckdb_store is passed in from outer scope."""
    return duckdb_store


# -------------------------------------------------------------------
# Layer injections (communication / stealth / ghost)
# -------------------------------------------------------------------


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
    from hledac.universal.coordinators.security_coordinator import UniversalSecurityCoordinator

    return UniversalSecurityCoordinator(max_concurrent=3)


# -------------------------------------------------------------------
# PrefetchOracle — unconditional, fail-soft
# -------------------------------------------------------------------


def _prefetch_oracle_factory() -> Any:
    from hledac.universal.prefetch.prefetch_oracle_integration import PrefetchOracleIntegration

    return PrefetchOracleIntegration()


# -------------------------------------------------------------------
# ContinuousPrefetchPipeline + TemporalIOCPredictor — unconditional,
# fail-soft.  Both built together so they can share duckdb_store ref.
# -------------------------------------------------------------------


def _prefetch_pipeline_factory(*, duckdb_store: Any) -> Any:
    from hledac.universal.layers import get_temporal_signal_layer
    from hledac.universal.prefetch.prefetch_pipeline import ContinuousPrefetchPipeline
    from hledac.universal.prefetch.temporal_predictor import TemporalIOCPredictor

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


# -------------------------------------------------------------------
# DuckDB store — unconditional, no try/except (store is always valid)
# -------------------------------------------------------------------


# (store is passed directly from the outer scope; no factory needed)

# -------------------------------------------------------------------
# Oracle + graph wiring — a SECOND pass after duckdb_store is injected
# -------------------------------------------------------------------



# -------------------------------------------------------------------
# Injection table — ordered by execution order
# -------------------------------------------------------------------

INJECTIONS: tuple[_Injection, ...] = (
    # 0: Priority — EvidenceLog has async .initialize() + create_event
    _Injection(
        name="evidence_log",
        factory=_evidence_log_factory,
        fail_soft=True,
        order=0,
    ),
    # 1: PolicyManager — unconditional, no gate
    _Injection(
        name="policy_manager",
        factory=_policy_manager_factory,
        fail_soft=False,  # PolicyManager is critical; raise if broken
        order=1,
    ),
    # 1.5: DuckDB store — unconditional (store passed from outer scope).
    # Must be injected before prefetch_oracle wiring (order 6).
    _Injection(
        name="duckdb_store",
        factory=_duckdb_store_factory,
        fail_soft=False,
        order=1,
    ),
    # 2: CommunicationLayer — opt-out via no_communication
    _Injection(
        name="communication_layer",
        factory=_communication_layer_factory,
        gate_attr="no_communication",
        fail_soft=True,
        order=2,
    ),
    # 3: StealthLayer — opt-out via no_stealth
    _Injection(
        name="stealth_layer",
        factory=_stealth_layer_factory,
        gate_attr="no_stealth",
        fail_soft=True,
        order=3,
    ),
    # 4: GhostLayer — opt-out via no_ghost
    _Injection(
        name="ghost_layer",
        factory=_ghost_layer_factory,
        gate_attr="no_ghost",
        fail_soft=True,
        order=4,
    ),
    # 5: SecurityCoordinator — tied to no_stealth gate
    _Injection(
        name="security_coordinator",
        factory=_security_coordinator_factory,
        gate_attr="no_stealth",
        fail_soft=True,
        order=5,
    ),
    # 6: PrefetchOracle — unconditional
    _Injection(
        name="prefetch_oracle",
        factory=_prefetch_oracle_factory,
        fail_soft=True,
        order=6,
    ),
    # 7: PrefetchPipeline + TemporalPredictor — unconditional
    _Injection(
        name="prefetch_pipeline",
        factory=_prefetch_pipeline_factory,
        fail_soft=True,
        order=7,
    ),
)


# -------------------------------------------------------------------
# Apply all injections
# -------------------------------------------------------------------


async def apply_injections(
    scheduler: Any,
    flags: Any,
    *,
    query: str,
    sprint_id: str,
    sprint_duration_s: float,
    windup_lead_s: float,
    duckdb_store: Any,
    rl_train_mode: bool,
    logger: logging.Logger,
) -> None:
    """
    Apply all declarative injections to *scheduler*.

    Execution order
    ---------------
    1. EvidenceLog (order=0): factory → async .initialize() → inject →
       create_event
    2. PolicyManager (order=1): factory → inject (no try/except)
    3. Layers (order=2-4): factory → inject with gate check
    4. SecurityCoordinator (order=5): factory → inject with gate check
    5. PrefetchOracle (order=6): factory → inject
    6. PrefetchPipeline (order=7): factory → inject both pipeline + predictor
    7. Oracle wiring: inject duckdb_store + ioc_graph into oracle

    Parameters
    ----------
    scheduler : SprintSchedulerV2
        Target object with inject_<name>() methods.
    flags : SprintFlags | None
        Runtime flags (no_communication, no_stealth, no_ghost, …).
        If None, all gate checks are treated as False (injections run).
    sprint_id : str
        Passed to EvidenceLog factory.
    sprint_duration_s : float
        Passed to EvidenceLog WARMUP event.
    windup_lead_s : float
        Passed to EvidenceLog WARMUP event.
    duckdb_store : Any
        Pre-initialised DuckDBShadowStore instance (from outer scope).
    rl_train_mode : bool
        Passed to SprintPolicyManager factory.
    logger : Logger
        Used for debug/warning messages.
    """
    # Normalise flags
    if flags is None:
        _flags_empty = _FlagsEmpty()
        flags = _flags_empty

    # Sort injections by order
    sorted_injections = sorted(INJECTIONS, key=lambda i: i.order)

    # Phase 1: standard injections
    for inj in sorted_injections:
        # Gate check
        if inj.gate_attr is not None and getattr(flags, inj.gate_attr, False):
            logger.debug("ISSUE-008: %s skipped (gate: %s)", inj.name, inj.gate_attr)
            continue

        # Build kwargs for factory
        factory_kwargs: dict[str, Any] = {}
        if inj.name == "evidence_log":
            factory_kwargs["sprint_id"] = sprint_id
        elif inj.name == "policy_manager":
            factory_kwargs["rl_train_mode"] = rl_train_mode
        elif inj.name in ("duckdb_store", "prefetch_pipeline"):
            factory_kwargs["duckdb_store"] = duckdb_store

        try:
            obj = inj.factory(**factory_kwargs)

            # Special-case EvidenceLog: async initialize() + WARMUP event
            if inj.name == "evidence_log" and obj is not None:
                _evidence_log_init(obj, sprint_id, query, sprint_duration_s, windup_lead_s)

            # For pipeline: inject both pipeline and predictor
            if inj.name == "prefetch_pipeline" and obj is not None:
                prefetch_pipeline, temporal_predictor = obj
                inject_method = getattr(scheduler, f"inject_{inj.name}", None)
                if inject_method:
                    inject_method(prefetch_pipeline)
                # Also inject temporal_predictor
                tp_inject = getattr(scheduler, "inject_temporal_predictor", None)
                if tp_inject and temporal_predictor is not None:
                    tp_inject(temporal_predictor)
            else:
                inject_method = getattr(scheduler, f"inject_{inj.name}", None)
                if inject_method and obj is not None:
                    inject_method(obj)

        except Exception as e:
            if inj.fail_soft:
                logger.debug("ISSUE-008: %s injection failed (fail-soft): %s", inj.name, e)
            else:
                raise

    # Phase 2: oracle wiring (after duckdb_store is injected)
    _oracle = getattr(scheduler, "_prefetch_oracle", None)
    if _oracle is not None and duckdb_store is not None:
        try:
            _oracle.inject_duckdb_store(duckdb_store)
        except Exception as e:
            logger.debug("ISSUE-008: oracle duckdb_store wiring failed (fail-soft): %s", e)

        try:
            from hledac.universal.knowledge.graph_service import _get_graph

            _ioc_graph = _get_graph()
            if _ioc_graph is not None:
                _oracle.inject_ioc_graph(_ioc_graph)
        except Exception as e:
            logger.debug("ISSUE-008: IOC graph injection failed (fail-soft): %s", e)


class _FlagsEmpty:
    """Neutral flags object used when flags=None."""

    def __getattr__(self, _name: str) -> bool:
        return False

    def __getitem__(self, _name: str) -> bool:
        return False
