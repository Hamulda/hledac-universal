"""STEP 4 — Phase protocols and SprintContext for SprintScheduler v2.

F350M-R / Issue #P2.

Defines the Protocol interfaces that each phase orchestrator must implement,
and the shared SprintContext passed to all phases.
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
import msgspec
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar
from utils._struct_helpers import struct_replace
if TYPE_CHECKING:
    from runtime.scheduler_config import SprintSchedulerConfig
    from runtime.scheduler_result import SprintSchedulerResult

class PhaseRunner(Protocol):
    """Protocol for the lifecycle runner (SprintLifecycleRunner).

    Defines the contract between SprintContext and the mechanical lifecycle boundary.
    Implemented by SprintLifecycleRunner — a pure mechanical seam that translates
    lifecycle state into phase transitions without owning policy.

    GHOST_INVARIANTS:
    - is_terminal() returns True when runner has reached a terminal phase
    - should_enter_windup() returns True when windup should begin (time-based)
    - windup_guard() evaluates pre_windup_barrier callback before allowing continuation
    - current_phase() returns current phase as string
    - teardown() triggers final phase transitions for windup/export/teardown
    - abort() requests immediate abort with reason
    - abort_requested / abort_reason provide abort state read access
    """

    def is_terminal(self) -> bool:
        """True if runner has reached a terminal phase."""
        ...

    def should_enter_windup(self, now_monotonic: float | None=None) -> bool:
        """True if windup should begin (time-based heuristic)."""
        ...

    def windup_guard(self, now_monotonic: float) -> bool:
        """True if windup guard allows continuation (evaluates pre_windup_barrier)."""
        ...

    @property
    def current_phase(self) -> str:
        """Current phase as string for scheduler callbacks."""
        ...

    def teardown(self) -> None:
        """Handle final phase transitions for teardown.

        If in WINDUP → EXPORT → TEARDOWN.
        If in ACTIVE/WARMUP → TEARDOWN (abort).
        """
        ...

    def abort(self, reason: str) -> None:
        """Request immediate abort with reason."""
        ...

    @property
    def abort_requested(self) -> bool:
        """True if the lifecycle has been asked to abort."""
        ...

    @property
    def abort_reason(self) -> str:
        """Reason for abort, if any."""
        ...

class Phase(Protocol):
    """Base protocol for all SprintScheduler v2 phases."""

    async def run(self, ctx: SprintContext, **kwargs: Any) -> Any:
        """Run the phase. Returns phase-specific result."""
        ...

class PreludePhase(Protocol):
    """Prelude phase: runs mandatory acquisition prelude lanes in parallel."""

    async def run(self, ctx: SprintContext, duckdb_store: Any, ct_log_client: Any) -> PreludePhaseResult:
        """Run prelude lanes (PUBLIC, CT, WAYBACK, PDNS, DOH)."""
        ...

class AcquisitionPhase(Protocol):
    """Acquisition phase: runs the main cycle loop."""

    async def run(self, ctx: SprintContext, ordered_sources: list[Any], duckdb_store: Any, now_monotonic: float | None) -> AcquisitionPhaseResult:
        """Run one acquisition cycle (feed/public/CT branches)."""
        ...

class WinddownPhase(Protocol):
    """Winddown phase: export, synthesis, teardown."""

    async def run(self, ctx: SprintContext, lifecycle: Any, query: str) -> None:
        """Run winddown (flush, export, synthesis, teardown)."""
        ...
T = TypeVar('T', default=object)

@dataclass(frozen=True, slots=True)
class InitResult(Generic[T]):
    """Result of a fail-soft init — captures success/failure with reason.

    Replaces ``try/except → return None`` antipattern across all SprintSchedulerV2
    service inits. Every init now logs (warning + elapsed_ms) on failure rather
    than silently returning None.

    Usage::

        result: InitResult[DuckDBShadowStore] = await _init_duckdb_store(query)
        if result.ok:
            store = result.value
        else:
            logger.warning("DuckDB init failed after %.1fms: %s",
                          result.elapsed_ms, result.error)
    """
    value: T | None
    'The initialized object, or None if init failed.'
    error: str | None
    'Human-readable error message. None on success.'
    elapsed_ms: float
    'How long the init took in milliseconds.'

    @property
    def ok(self) -> bool:
        """True if init succeeded (value is not None)."""
        return self.value is not None

    @classmethod
    def success(cls, value: T, elapsed_ms: float) -> 'InitResult[T]':
        """Construct a success result."""
        return cls(value=value, error=None, elapsed_ms=elapsed_ms)

    @classmethod
    def failure(cls, error: str, elapsed_ms: float) -> 'InitResult[T]':
        """Construct a failure result."""
        return cls(value=None, error=error, elapsed_ms=elapsed_ms)

class PreludePhaseResult(msgspec.Struct):
    """Result from the prelude phase."""
    lanes_attempted: list[str]
    lanes_skipped: dict[str, str]
    lanes_accepted: dict[str, int]
    prelude_duration_s: float | None = None
    error: str | None = None

class AcquisitionPhaseResult(msgspec.Struct):
    """Result from one acquisition cycle."""
    cycles_started: int = 0
    cycles_completed: int = 0
    accepted_findings: int = 0
    empty_cycles: int = 0
    windup_entered: bool = False
    exit_path: str | None = None
    error: str | None = None

class WinddownPhaseResult(msgspec.Struct):
    """Result from the winddown phase."""
    export_paths: list[str] = msgspec.field(default_factory=list)
    synthesis_success: bool = False
    teardown_duration_s: float | None = None
    error: str | None = None

class _CycleState(msgspec.Struct):
    """Per-cycle mutable state — isolated to prevent cross-cycle leakage.

    Unlike SprintContext (which is immutable/frozen), _CycleState IS mutable
    because per-cycle state changes frequently within a single cycle (e.g.,
    barrier_retry_count increments, cycle_time_ema updates).

    Lifecycle: a fresh _CycleState is created at the START of each cycle
    (acquisition, winddown) and passed via SprintContext._cycle. This prevents
    cross-cycle state leakage while keeping the phase orchestrator API clean.

    Usage::

        ctx = ctx.with_cycle(barrier_retry_count=2)
        ctx._cycle.barrier_retry_count += 1  # mutate in-place within cycle
    """
    wall_clock_start: float = 0.0
    'Monotonic timestamp when sprint started.'
    lifecycle: Any = None
    'SprintLifecycleManager instance.'
    duckdb_store: Any = None
    'DuckDBShadowStore — set once at sprint start.'
    stop_requested: bool = False
    'True when scheduler requests acquisition to stop.'
    prewindup_barrier_delayed: bool = False
    'True when prewindup barrier has been satisfied with a delay.'
    barrier_retry_count: int = 0
    'Number of barrier retries attempted.'
    cycle_time_ema: float = 1.0
    'Exponential moving average of cycle time in seconds.'
    last_cycle_start: float | None = None
    'Monotonic timestamp of last cycle start.'
    effective_max_cycles: int | None = None
    'Computed max cycles based on active window and EMA.'
    enrichment_services: Any = None
    'EnrichmentServices instance. May be None.'
    sidecar_orchestrator: Any = None
    'SidecarOrchestrator instance. May be None.'
    acquisition_plan: Any = None
    'AcquisitionPlan instance. May be None.'
    synth_windup_task: Any = None
    'Synthesis windup task. May be None during acquisition.'
    hermes_engine: Any = None
    'Hermes3Engine for synthesis. May be None during acquisition.'
    privacy_layer: Any = None
    'PrivacyLayer instance. May be None.'
    privacy_context_id: Any = None
    'Privacy context ID. May be None.'
    evidence_log: Any = None
    'EvidenceLog instance. May be None.'
    prev_chain_hash: Any = None
    'Previous chain hash for consecutive sprint dedup. May be None.'
    sprint_id: str = 'unknown'
    'Unique sprint identifier.'
    int_counter_layout: Any = None
    'Int counter layout for Rust IPC. May be None.'
    rel_discovery_engine: Any = None
    'Relationship discovery engine. May be None.'
    temporal_predictor: Any = None
    'TemporalIOCPredictor. May be None.'
    pivot_planner: Any = None
    'Pivot planner. May be None.'
    analyst_workbench: Any = None
    'Analyst workbench. May be None.'
    forensics_enricher: Any = None
    'Forensics enricher. May be None.'

class SprintContext(msgspec.Struct, frozen=True):
    """Shared immutable context passed to all phase orchestrators.

    Unlike v1's `self._*` slots, v2 passes all state explicitly via this
    context. This makes phases testable in isolation and enables the
    greenfield rewrite without 156-slot coupling.

    All mutable fields (result, bg_tasks, cancel_event) are passed as
    explicit references, not hidden state.

    Per-cycle mutable state is stored in `_cycle` field. Type checker
    prevents accidental field addition to this class — new per-cycle fields
    MUST be added to _CycleState and accessed via ctx._cycle.FIELD.
    """
    config: SprintSchedulerConfig
    'Sprint configuration (duration, windup lead, etc.).'
    query: str
    'Original sprint query string.'
    result: SprintSchedulerResult
    'Sprint result — written by all phases (ctx.result.X = Y).'
    duckdb_store: InitResult[Any] | None = None
    'DuckDBShadowStore init result. Access .value if .ok, else handle .error.'
    graph_service: Any = None
    'DuckPGQGraph instance. May be None if graph disabled.'
    hermes_engine: InitResult[Any] | None = None
    'Hermes3Engine init result. Access .value if .ok, else handle .error.'
    governor: InitResult[Any] | None = None
    'M1ResourceGovernor init result. Access .value if .ok, else handle .error.'
    evidence_log: InitResult[Any] | None = None
    'EvidenceLog init result. Access .value if .ok, else handle .error.'
    ct_log_client: Any = None
    'CT log client. May be None.'
    runner: PhaseRunner | None = None
    'SprintLifecycleRunner — phase transitions, windup guard.'
    lifecycle: Any = None
    'SprintLifecycle — lifecycle events (start, cycle, windup, teardown).'
    cancel_event: asyncio.Event = msgspec.field(default_factory=asyncio.Event)
    'Cancellation event — set to cancel the sprint.'
    bg_tasks: set[asyncio.Task[Any]] = msgspec.field(default_factory=set)
    'Set of background asyncio.Task objects tracked by the scheduler.'
    _cycle: _CycleState = msgspec.field(default_factory=_CycleState)
    'Per-cycle state — see _CycleState docstring.'

    @property
    def wall_clock_start(self) -> float:
        """Wall clock start — monotonic timestamp when sprint started."""
        return self._cycle.wall_clock_start

    @property
    def is_terminal(self) -> bool:
        """True if runner has reached a terminal phase."""
        return self.runner.is_terminal() if self.runner else True

    @classmethod
    def build(cls, config: SprintSchedulerConfig, query: str, result: SprintSchedulerResult, *, ct_log_client: Any=None, graph_service: Any=None) -> 'SprintContext':
        """Build a new SprintContext with required fields and defaults.

        Usage::

            ctx = SprintContext.build(config, query, result)
            ctx = ctx.with_services(duckdb_store=store, governor=gov)
        """
        return cls(config=config, query=query, result=result, ct_log_client=ct_log_client, graph_service=graph_service)

    def with_services(self, *, duckdb_store: InitResult[Any] | None=None, graph_service: Any=None, hermes_engine: InitResult[Any] | None=None, governor: InitResult[Any] | None=None, evidence_log: InitResult[Any] | None=None, runner: Any=None, lifecycle: Any=None) -> 'SprintContext':
        """Return a new SprintContext with services initialized (type-safe).

        Each service field accepts an InitResult[T] (from fail-soft init) or None.
        Access the live object via result.value when result.ok is True.
        """
        return struct_replace(self, duckdb_store=duckdb_store, graph_service=graph_service, hermes_engine=hermes_engine, governor=governor, evidence_log=evidence_log, runner=runner, lifecycle=lifecycle)

    def with_cycle(self, **kwargs: Any) -> 'SprintContext':
        """Return a new SprintContext with updated per-cycle state.

        Usage::

            ctx = ctx.with_cycle(wall_clock_start=ts, lifecycle=lifecycle_mgr)
            ctx = ctx.with_cycle(stop_requested=True, barrier_retry_count=2)
        """
        _new_cycle = struct_replace(self._cycle, **kwargs)
        return struct_replace(self, _cycle=_new_cycle)


def dataclass_replace(obj: Any, **changes: Any) -> Any:
    """Polyglot replace: delegates to msgspec.structs.replace for msgspec.Struct,
    otherwise falls back to the dataclass logic for real @dataclass types.

    This is the bridge for code that may work with both dataclasses and msgspec
    Structs. For new code targeting only msgspec.Struct, prefer ``struct_replace``
    directly.
    """
    import dataclasses as _dataclasses
    import msgspec as _msgspec

    if isinstance(obj, _msgspec.Struct):
        return _msgspec.structs.replace(obj, **changes)

    # Real dataclass — use standard library
    if not isinstance(obj, type) and hasattr(obj, '__dataclass_fields__'):
        return _dataclasses.replace(obj, **changes)
    raise TypeError(f"{obj!r} is neither a msgspec.Struct nor a dataclass")