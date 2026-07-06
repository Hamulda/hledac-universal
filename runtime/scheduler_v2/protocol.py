"""STEP 4 — Phase protocols and SprintContext for SprintScheduler v2.

F350M-R / Issue #P2.

Defines the Protocol interfaces that each phase orchestrator must implement,
and the shared SprintContext passed to all phases.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from runtime.scheduler_config import SprintSchedulerConfig
    from runtime.scheduler_result import SprintSchedulerResult


# ── Phase Protocols ────────────────────────────────────────────────────────────


class Phase(Protocol):
    """Base protocol for all SprintScheduler v2 phases."""

    async def run(self, ctx: SprintContext, **kwargs: Any) -> Any:
        """Run the phase. Returns phase-specific result."""
        ...


class PreludePhase(Protocol):
    """Prelude phase: runs mandatory acquisition prelude lanes in parallel."""

    async def run(
        self,
        ctx: SprintContext,
        duckdb_store: Any,
        ct_log_client: Any,
    ) -> PreludePhaseResult:
        """Run prelude lanes (PUBLIC, CT, WAYBACK, PDNS, DOH)."""
        ...


class AcquisitionPhase(Protocol):
    """Acquisition phase: runs the main cycle loop."""

    async def run(
        self,
        ctx: SprintContext,
        ordered_sources: list[Any],
        duckdb_store: Any,
        now_monotonic: float | None,
    ) -> AcquisitionPhaseResult:
        """Run one acquisition cycle (feed/public/CT branches)."""
        ...


class WinddownPhase(Protocol):
    """Winddown phase: export, synthesis, teardown."""

    async def run(
        self,
        ctx: SprintContext,
        lifecycle: Any,
        query: str,
    ) -> None:
        """Run winddown (flush, export, synthesis, teardown)."""
        ...


# ── Phase Result Types ────────────────────────────────────────────────────────


@dataclass
class PreludePhaseResult:
    """Result from the prelude phase."""

    lanes_attempted: list[str]
    lanes_skipped: dict[str, str]
    lanes_accepted: dict[str, int]
    prelude_duration_s: float | None = None
    error: str | None = None


@dataclass
class AcquisitionPhaseResult:
    """Result from one acquisition cycle."""

    cycles_started: int = 0
    cycles_completed: int = 0
    accepted_findings: int = 0
    empty_cycles: int = 0
    windup_entered: bool = False
    exit_path: str | None = None
    error: str | None = None


@dataclass
class WinddownPhaseResult:
    """Result from the winddown phase."""

    export_paths: list[str] = field(default_factory=list)
    synthesis_success: bool = False
    teardown_duration_s: float | None = None
    error: str | None = None


# ── SprintContext ─────────────────────────────────────────────────────────────


@dataclass
class SprintContext:
    """Shared immutable context passed to all phase orchestrators.

    Unlike v1's `self._*` slots, v2 passes all state explicitly via this
    context. This makes phases testable in isolation and enables the
    greenfield rewrite without 156-slot coupling.

    All mutable fields (result, bg_tasks, cancel_event) are passed as
    explicit references, not hidden state.
    """

    # ── Configuration (read-only) ──────────────────────────────────────────

    config: SprintSchedulerConfig
    """Sprint configuration (duration, windup lead, etc.)."""

    query: str
    """Original sprint query string."""

    # ── Mutable result accumulator (shared across all phases) ───────────────

    result: SprintSchedulerResult
    """Sprint result — written by all phases (ctx.result.X = Y)."""

    # ── Core services (set at construction, used by phases) ─────────────────

    duckdb_store: Any = None
    """DuckDBShadowStore instance. May be None in tests."""

    graph_service: Any = None
    """DuckPGQGraph instance. May be None if graph disabled."""

    hermes_engine: Any = None
    """Hermes3Engine for MLX inference. May be None."""

    governor: Any = None
    """M1ResourceGovernor for memory pressure monitoring."""

    evidence_log: Any = None
    """EvidenceLog for telemetry. May be None."""

    ct_log_client: Any = None
    """CT log client. May be None."""

    # ── Lifecycle management ────────────────────────────────────────────────

    runner: Any = None
    """SprintLifecycleManager — phase transitions, windup guard."""

    lifecycle: Any = None
    """SprintLifecycle — lifecycle events (start, cycle, windup, teardown)."""

    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    """Cancellation event — set to cancel the sprint."""

    # ── Background tasks ────────────────────────────────────────────────────

    bg_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    """Set of background asyncio.Task objects tracked by the scheduler."""

    # ── Mutable runtime state (written by phases) ──────────────────────────

    @property
    def wall_clock_start(self) -> float:
        """Wall clock start — monotonic timestamp when sprint started."""
        return self.result.scheduler_exit_elapsed_s or 0.0

    @property
    def is_terminal(self) -> bool:
        """True if runner has reached a terminal phase."""
        return self.runner.is_terminal() if self.runner else True
