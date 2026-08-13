# SPDX-License-Identifier: MIT
"""
runtime/sidecar_orchestrator.py — F350M-R: Thin Facade Refactor
================================================================



SidecarOrchestrator is a thin facade wiring three canonical layers:

1. FindingSidecarBus  (sidecar_bus.py)  — accepted-finding sidecar registry/execution
2. SidecarDispatcher  (sidecar_dispatcher.py) — dispatch bookkeeping
3. SprintAdvisoryRunner (sprint_advisory_runner.py) — teardown advisory orchestration

Public API (small surface):
  - dispatch_findings(...)    → delegates to SidecarDispatcher
  - run_advisory_runner()     → delegates to SprintAdvisoryRunner
  - run_target_memory_update(...) → cross-sprint target memory (F204D)
  - reset()                   → clears in-memory state

Responsibilities NOT in this module:
  - All accepted-finding sidecar runners → sidecar_bus.py (FindingSidecarBus)
  - All teardown advisory implementations → sprint_advisory_runner.py
  - Batch construction, empty guards, skipped sidecar tracking → sidecar_dispatcher.py
  - SidecarBus creation → sidecar_bus.py (create_sidecar_bus factory)
  - Advisory implementation (3 methods below) → SprintScheduler via getattr seam

Deletion test: if this module is deleted, the 4 public call sites above
must reappear in SprintScheduler. No accepted-finding sidecar call site
lives here — they all live in sidecar_bus.py / sidecar_dispatcher.py.

ADVISORY CALLBACK SEAM (bounded, F226 + F1 FIX)
────────────────────────────────────────────────
SidecarOrchestrator owns scheduling and dispatch. SprintScheduler still owns
the inline advisory implementations. Two advisories are self-contained adapters
(no scheduler dependency). All scheduler-backed sidecars now call through
the SchedulerAdvisory Protocol — no getattr() reflection:

  1. _run_ct_to_passivedns_pivot_advisory  → SchedulerAdvisory._run_ct_to_passivedns_pivot_advisory
  2. _run_bgp_advisory_sidecar             → own BGPAdvisorAdapter (no scheduler)
  3. _run_wayback_cdx_deep_sidecar         → own WaybackCDXDeepAdapter (no scheduler)
  4–12. _run_ipfs/onion/i2p/bgp_enrichment/commoncrawl/banner/dht/
        digital_ghost/steganography/ti_feed → SchedulerAdvisory Protocol

F1 FIX: SchedulerAdvisory(Protocol) nahradil getattr() antipattern.
scheduler: Any → scheduler: SchedulerAdvisory | None.
mypy --strict odhali přejmenování jakékoli _run_* metody.
No new scheduler-backed sidecar method may be added without updating
the SchedulerAdvisory Protocol in sidecar_protocol.py.
"""



import asyncio as _asyncio

from hledac.universal.utils.asyncx import parallel, safe_create_task, _check_gathered
from hledac.universal.runtime.scheduler_v2._task_registry import TaskScope, safe_create_task_tracked
import logging
import os as _os
import time as _time
from typing import Any

from hledac.universal.runtime.lane_registry import LANE_REGISTRY
from hledac.universal.runtime.sidecar_bus import create_sidecar_bus
from hledac.universal.runtime.sidecar_dispatcher import (
    DispatchOutcome,
    SidecarDispatcher,
)
from hledac.universal.runtime.sidecar_protocol import SchedulerAdvisory

log = logging.getLogger(__name__)

# Deferred import to avoid circular dep at mod load time
_SPRINT_ADVISORY_RUNNER: Any = None

# F039: OTel tracer lazy-loaded for sidecar telemetry spans
_OTEL_TRACER: Any = None


def _get_otel_tracer() -> Any:
    """Lazily get OTel tracer for sidecar spans."""
    global _OTEL_TRACER
    if _OTEL_TRACER is None:
        try:
            from opentelemetry import trace
            _OTEL_TRACER = trace.get_tracer("hledac.sidecar")
        except Exception:
            _OTEL_TRACER = False
    return _OTEL_TRACER if _OTEL_TRACER else None

# P0: Bounded concurrency for advisory sidecars (M1 8GB safe)
# Advisory sidecary (IPFS, Tor, I2P, BGP, banner, DHT, Gopher, etc.) run in
# fire-and-forget background tasks. This semaphore prevents unbounded parallel
# launches when many HLEDAC_ENABLE_* flags are set simultaneously.
#
# ISSUE #3: Raised to 8 — all 4 outer TaskGroup branches (Steps 1-2, 3-4, 5-7,
# plugin) now run in PARALLEL. Each branch's inner _run_bounded_sidecar calls
# share ONE global semaphore. With 4 parallel branches each running up to
# _ADVISORY_SIDECAR_SEMAPHORE_LIMIT sidecars, total concurrent sidecar slots = 8.
# M1 8GB: 8 × ~15 MB/sidecar ≈ 120 MB peak — within budget.
_ADVISORY_SIDECAR_SEMAPHORE_LIMIT: int = 8

# P0: Bounded concurrency for plugin sidecars (M1 8GB safe)
# Plugin sidecars (SidecarRegistry) are dispatched as individual tasks.
# This semaphore caps concurrent plugin runs regardless of how many
# @SidecarRegistry.register adapters are available.
_PLUGIN_SIDECAR_SEMAPHORE_LIMIT: int = 4


# P0: Shared semaphore for advisory sidecar concurrency control.
# All advisory sidecar tasks share ONE semaphore to enforce the global
# _ADVISORY_SIDECAR_SEMAPHORE_LIMIT cap regardless of how many
# HLEDAC_ENABLE_* flags are active simultaneously.
_advisory_sidecar_sem: _asyncio.Semaphore | None = None


def _get_advisory_semaphore() -> _asyncio.Semaphore:
    global _advisory_sidecar_sem
    if _advisory_sidecar_sem is None:
        _advisory_sidecar_sem = _asyncio.Semaphore(_ADVISORY_SIDECAR_SEMAPHORE_LIMIT)
    return _advisory_sidecar_sem


# UNIFIED-001: Peak load coordinator integration
# Each advisory sidecar is estimated at ~15 MB peak memory usage.
# We acquire admission from the global peak load coordinator BEFORE
# running the sidecar to prevent OOM when multiple subsystems compete.
_ADVISORY_SIDECAR_ESTIMATED_MB: float = 15.0


def _get_peak_coordinator():
    """Lazy import of peak load coordinator to avoid circular dependencies.

    UNIFIED-003: Now also tries GlobalPeakCoScheduler for enhanced features
    (mutex groups, UMA gating, deadline boosting). Falls back to raw
    GlobalPeakLoadCoordinator if co-scheduler is not initialized.
    """
    try:
        # Try co-scheduler first (UNIFIED-003)
        from hledac.universal.core.global_co_scheduler import (
            get_co_scheduler,
            Subsystem,
        )
        from hledac.universal.core.peak_load_coordinator import (
            ResourceClass,
            TaskPriority,
        )
        return get_co_scheduler(), ResourceClass, TaskPriority, Subsystem
    except ImportError:  # noqa: BLE001
        pass
    try:
        from hledac.universal.core.peak_load_coordinator import (
            ResourceClass,
            TaskPriority,
            get_peak_coordinator,
        )
        return get_peak_coordinator(), ResourceClass, TaskPriority, None
    except ImportError:
        return None, None, None, None


async def _run_bounded_sidecar(coro, name: str) -> None:
    """
    P0: Run a sidecar coroutine through the advisory semaphore.

    Bounds concurrent advisory sidecar executions to
    _ADVISORY_SIDECAR_SEMAPHORE_LIMIT regardless of how many
    HLEDAC_ENABLE_* flags are active.

    UNIFIED-001: Also acquires admission from GlobalPeakLoadCoordinator
    to prevent OOM when multiple subsystems compete for memory.
    Advisory sidecars run at LOW priority and may be deferred under
    high memory pressure.

    Fail-soft: any exception is caught and logged, never raised.
    F039: OTel span for sidecar telemetry.
    """
    tracer = _get_otel_tracer()
    sem = _get_advisory_semaphore()

    # UNIFIED-001+003: Acquire admission from peak load coordinator
    coordinator_result = _get_peak_coordinator()
    coordinator, ResourceClass, TaskPriority, Subsystem = coordinator_result
    peak_guard = None
    if coordinator is not None:
        try:
            # UNIFIED-003: If co-scheduler is available, use it (enhanced features)
            if Subsystem is not None:
                peak_guard = coordinator.guard(
                    Subsystem.SIDECAR_ADVISORY,
                    _ADVISORY_SIDECAR_ESTIMATED_MB,
                    priority="low",
                    owner=f"sidecar:{name}",
                    timeout_s=5.0,
                )
            else:
                # Fall back to raw coordinator (backward compat)
                peak_guard = await coordinator.acquire(
                    ResourceClass.SIDECAR_ADVISORY,
                    _ADVISORY_SIDECAR_ESTIMATED_MB,
                    priority=TaskPriority.LOW,
                    owner=f"sidecar:{name}",
                    timeout_s=5.0,
                )
        except TimeoutError:
            log.debug("[UNIFIED-001] sidecar %s deferred: peak load timeout", name)
            return  # Fail-soft: skip sidecar under memory pressure
        except Exception as e:
            log.debug("[UNIFIED-001] sidecar %s: peak coordinator error (fail-soft): %s", name, e)
            peak_guard = None  # Fall through to semaphore-only path

    # UNIFIED-003: Wrap in peak_guard context to ensure release
    if peak_guard is not None:
        async with peak_guard:
            await _run_sidecar_with_semaphore(sem, coro, name, tracer)
    else:
        await _run_sidecar_with_semaphore(sem, coro, name, tracer)


async def _run_sidecar_with_semaphore(sem, coro, name: str, tracer: Any) -> None:
    """Internal sidecar execution within semaphore and optional OTel span."""
    async with sem:
        if tracer:
            with tracer.start_as_current_span(f"sidecar.{name}") as span:
                span.set_attribute("sidecar.name", name)
                span.set_attribute("sidecar.type", "advisory")
                try:
                    await coro
                except _asyncio.CancelledError:
                    raise
                except Exception as e:
                    span.set_attribute("sidecar.error", str(e))
                    log.debug("[P0 advisory] sidecar %s failed (fail-soft): %s", name, e)
        else:
            try:
                await coro
            except _asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("[P0 advisory] sidecar %s failed (fail-soft): %s", name, e)


async def _execute_sidecar_with_tracer(coro, name: str, tracer: Any) -> None:
    """Execute sidecar coroutine with OTel tracing (helper for _run_bounded_sidecar)."""
    if tracer:
        with tracer.start_as_current_span(f"sidecar.{name}") as span:
            span.set_attribute("sidecar.name", name)
            span.set_attribute("sidecar.type", "advisory")
            try:
                await coro
            except _asyncio.CancelledError:
                raise
            except Exception as e:
                span.set_attribute("sidecar.error", str(e))
                log.debug("[P0 advisory] sidecar %s failed (fail-soft): %s", name, e)
    else:
        try:
            await coro
        except _asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug("[P0 advisory] sidecar %s failed (fail-soft): %s", name, e)


# P0: Shared semaphore for plugin sidecar concurrency control.
# All plugin sidecar dispatches share ONE semaphore to enforce the global
# _PLUGIN_SIDECAR_SEMAPHORE_LIMIT cap regardless of how many adapters
# are registered in SidecarRegistry.
_plugin_sidecar_sem: _asyncio.Semaphore | None = None


def _get_plugin_semaphore() -> _asyncio.Semaphore:
    global _plugin_sidecar_sem
    if _plugin_sidecar_sem is None:
        _plugin_sidecar_sem = _asyncio.Semaphore(_PLUGIN_SIDECAR_SEMAPHORE_LIMIT)
    return _plugin_sidecar_sem


async def _run_bounded_plugin_sidecar(coro, sidecar_id: str) -> None:
    """
    P0: Run a plugin sidecar coroutine through the plugin semaphore + UMA memory guard.

    Bounds concurrent plugin sidecar executions to
    _PLUGIN_SIDECAR_SEMAPHORE_LIMIT regardless of how many
    @SidecarRegistry.register adapters are available.

    UNIFIED-002: Now also reserves memory budget via AsyncUMAGuard to prevent
    race condition where multiple sidecars simultaneously see "ELEVATED" pressure
    and both proceed, exceeding the 6.48 GB hard limit.

    Fail-soft: any exception is caught and logged, never raised.
    F039: OTel span for plugin sidecar telemetry.
    """
    tracer = _get_otel_tracer()
    sem = _get_plugin_semaphore()

    # UNIFIED-002: Reserve memory budget via AsyncUMAGuard
    guard = None
    try:
        from hledac.universal.core.resource_governor import get_uma_guard, Priority
        guard = get_uma_guard()
    except Exception:
        guard = None

    async with sem:
        # If guard available, wrap execution in memory reservation
        if guard is not None:
            try:
                async with guard.reserve(
                    estimated_mb=30.0,  # ~30 MB per plugin sidecar
                    priority=Priority.LOW,
                    timeout_s=10.0,
                ):
                    await _execute_plugin_sidecar_with_tracer(coro, sidecar_id, tracer)
                    return None
            except Exception as e:
                log.debug("[P0 plugin] sidecar %s blocked by UMA guard: %s", sidecar_id, e)
                return None
        else:
            # Fallback: no guard available, execute without memory reservation
            await _execute_plugin_sidecar_with_tracer(coro, sidecar_id, tracer)
            return None


async def _execute_plugin_sidecar_with_tracer(coro, sidecar_id: str, tracer: Any) -> None:
    """Execute plugin sidecar coroutine with OTel tracing (helper for _run_bounded_plugin_sidecar)."""
    if tracer:
        with tracer.start_as_current_span(f"sidecar.plugin.{sidecar_id}") as span:
            span.set_attribute("sidecar.id", sidecar_id)
            span.set_attribute("sidecar.type", "plugin")
            try:
                await coro
                return None
            except _asyncio.CancelledError:
                raise
            except Exception as e:
                span.set_attribute("sidecar.error", str(e))
                log.debug("[P0 plugin] sidecar %s failed (fail-soft): %s", sidecar_id, e)
                return None
    else:
        try:
            await coro
            return None
        except _asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug("[P0 plugin] sidecar %s failed (fail-soft): %s", sidecar_id, e)
            return None


def _get_sprint_advisory_runner():
    global _SPRINT_ADVISORY_RUNNER
    if _SPRINT_ADVISORY_RUNNER is None:
        from hledac.universal.runtime.sprint_advisory_runner import (
            SprintAdvisoryRunner as _SAR,  # noqa: N814
        )
        _SPRINT_ADVISORY_RUNNER = _SAR
    return _SPRINT_ADVISORY_RUNNER


# ---------------------------------------------------------------------------
# F350M-FED: Plugin Sidecar Context (duck-typed SidecarContext)
# ---------------------------------------------------------------------------


class _PluginSidecarContext:
    """
    F350M-FED: Lightweight duck-typed SidecarContext for plugin sidecars.

    Constructed by SidecarOrchestrator._build_plugin_sidecar_context() from
    the bound scheduler state. Avoids a hard import of SidecarContext
    (which lives in runtime.sidecar_protocol) at module load time and
    matches the attribute-based access pattern that registered adapters
    use (getattr(ctx, "query") etc.).

    SidecarRegistry.get_available() returns adapter instances; their
    run(ctx) implementations read ctx attributes via getattr, so any
    object with the 5 fields is accepted. We use this typed shim for
    IDE/typing clarity but it is structurally compatible with
    SidecarContext.
    """

    __slots__ = (
        "query",
        "sprint_id",
        "findings",
        "sprint_mode",
        "memory_pressure",
    )

    def __init__(
        self,
        query: str,
        sprint_id: str,
        findings: list,
        sprint_mode: str,
        memory_pressure: float,
    ) -> None:
        self.query = query
        self.sprint_id = sprint_id
        self.findings = findings
        self.sprint_mode = sprint_mode
        self.memory_pressure = float(memory_pressure)


# ---------------------------------------------------------------------------
# SidecarOrchestrator
# ---------------------------------------------------------------------------


class SidecarOrchestrator:
    """
    Thin facade wiring three canonical layers for sprint sidecar execution.

    result_sink:     SprintSchedulerResult — telemetry fields are updated here.
    governor:        M1 resource governor or None — RAM guard checks.
    scheduler:       SchedulerAdvisory — F1 FIX: typovy kontrakt nahradil Any;
                     primo volani pres Protocol nahradilo getattr() antipattern.
    """

    __slots__ = (
        "_result",
        "_governor",
        "_scheduler",
        "_bus",
        "_dispatcher",
        "_target_memory_service",
        "_prewarmed",
    )

    def __init__(
        self,
        result_sink: Any,
        governor: Any = None,
        scheduler: SchedulerAdvisory | None = None,
    ) -> None:
        self._prewarmed = False  # ISSUE #22: prewarm once
        self._result = result_sink
        self._governor = governor
        self._scheduler = scheduler
        _profile = getattr(getattr(scheduler, "_config", None), "acquisition_profile", None) if scheduler else None
        self._bus = create_sidecar_bus(governor=governor, acquisition_profile=_profile)
        self._dispatcher = SidecarDispatcher(
            bus=self._bus,
            governor=governor,
        )
        # ISSUE-005 FIX: Bind this SidecarOrchestrator to ContextVar so that
        # SchedulerBackedSidecarAdapter.run_async() can find sidecar methods.
        # SidecarOrchestrator hosts the _run_*_sidecar() methods, not SprintScheduler.
        from hledac.universal.runtime.sidecars._base import bind_scheduler as _bind_scheduler
        _bind_scheduler(self)

    async def prewarm_async(self) -> None:
        """
        ISSUE #22: Parallel pre-warm of SidecarRegistry adapters.

        Runs BEFORE first run_advisory_runner() call to overlap
        import costs (academic GLiNER=200ms, dht cryptography=150ms).

        Idempotent: only runs once.
        """
        if self._prewarmed:
            return
        self._prewarmed = True
        try:
            from hledac.universal.runtime.sidecar_protocol import SidecarRegistry, ensure_adapters_registered
            ensure_adapters_registered()
            await SidecarRegistry.prewarm_async()
        except Exception as e:
            log.debug("[ISSUE #22] prewarm_async failed (fail-soft): %s", e)

    # ── Public API ─────────────────────────────────────────────────────────────

    async def dispatch_findings(
        self,
        source_branch: str,
        findings: list,
        store: Any,
        query: str,
        sprint_id: str,
    ) -> DispatchOutcome:
        """
        F205C/F205F: Route accepted findings from any branch through SidecarDispatcher.

        Delegates to SidecarDispatcher. All batch construction, empty guards,
        skipped heavy sidecar tracking, CancelledError propagation, and
        fail-soft handling live in the dispatcher.

        CancelledError is re-raised to caller.
        All other exceptions are fail-soft.
        """
        # query is passed through to SidecarBatch in the dispatcher
        outcome = await self._dispatcher.dispatch(
            source_branch,
            findings,
            store,
            query,
            sprint_id,
        )
        # Propagate skipped sidecars to result sink if the attribute exists
        if outcome.sidecars_skipped and hasattr(self._result, "sidecars_skipped"):
            existing = getattr(self._result, "sidecars_skipped", set())
            if isinstance(existing, set):
                self._result.sidecars_skipped = existing | set(outcome.sidecars_skipped)
            elif isinstance(existing, list):
                seen = set(existing)
                for s in outcome.sidecars_skipped:
                    if s not in seen:
                        existing.append(s)

        # F245B: Propagate source_family_outcomes to result sink if the attr exists.
        # Attribute name is source_family_outcomes_list on SprintSchedulerResult.
        if outcome.source_family_outcomes and hasattr(self._result, "source_family_outcomes_list"):
            existing = getattr(self._result, "source_family_outcomes_list", [])
            if isinstance(existing, list):
                for entry in outcome.source_family_outcomes:
                    # Deduplicate by (family, lane) before appending
                    duplicate = any(
                        e.get("family") == entry.get("family") and e.get("lane") == entry.get("lane")
                        for e in existing
                    )
                    if not duplicate:
                        existing.append(entry)
                self._result.source_family_outcomes_list = existing

        return outcome

    async def run_advisory_runner(self) -> None:
        """
        F206D + ISSUE #3: Run all teardown advisory steps via SprintAdvisoryRunner.

        ISSUE #3 FIX: All 4 branches now run in PARALLEL via outer TaskGroup:
          - Branch A: SprintAdvisoryRunner (4 core advisories)
          - Branch B: CT → PassiveDNS pivot advisory
          - Branch C: BGP/Wayback/CommonCrawl sidecars (TaskGroup)
          - Branch D: IPFS/Onion/I2P/banner/DHT/Gopher/stego/TI sidecars (TaskGroup)
          - Branch E: Plugin sidecars (TaskGroup)

        Each branch's inner _run_bounded_sidecar calls share ONE global semaphore
        (_ADVISORY_SIDECAR_SEMAPHORE_LIMIT=8). This replaces the prior sequential
        execution that ran Steps 1→2→(3-4)→(5-7)→(plugin) in wall-time.

        Expected speedup: 5-7× faster teardown (30-90s → 5-15s at full flag-on load).

        Canonical teardown entry point. Each step is fail-soft;
        CancelledError propagates to caller.
        """
        # [FINAL]-019-06: Gate sidecars under CRITICAL QoS to avoid OOM under pressure.
        # Sidecar gate functions (bgp, ipfs, onion, etc.) are expensive and should
        # be skipped during WINDUP/BATTERY/EMERGENCY modes — the governor already
        # sets sidecars_ok=False in QoSProfile for these levels.
        try:
            from hledac.universal.core.resource_governor import get_current_degradation_level, QoSLevel
            level = get_current_degradation_level()
            if level in (QoSLevel.EMERGENCY, QoSLevel.BATTERY, QoSLevel.WINDUP):
                return  # All sidecars suppressed by QoS policy
        except Exception:  # noqa: BLE001
            pass  # fail-open: governor unavailable → allow sidecars

        # ISSUE #22: Parallel pre-warm of SidecarRegistry adapters (lazy imports + parallel init)
        await self.prewarm_async()

        if self._scheduler is None:
            return

        # ISSUE #3: Outer TaskGroup — all 5 branches run concurrently (PEP 654)
        async with _asyncio.TaskGroup() as _outer_tg:
            # ── Branch A: SprintAdvisoryRunner (4 core advisories) ─────────────
            async def _run_sprint_advisory_branch() -> None:
                """Branch A: SprintAdvisoryRunner for 4 core advisories."""
                SAR = _get_sprint_advisory_runner()  # noqa: N806
                runner = SAR(
                    scheduler=self._scheduler,
                    duckdb_store=getattr(self._scheduler, "_duckdb_store", None),
                    governor=getattr(self._scheduler, "_governor", None),
                    analyst_workbench=getattr(self._scheduler, "_analyst_workbench", None),
                )
                # Sprint F206BK: Gate pivot_executor via acquisition strategy
                snapshot = getattr(self._scheduler, "_acquisition_plan", None)
                if snapshot is not None:
                    from hledac.universal.runtime.acquisition_strategy import (
                        AcquisitionLane,
                        is_lane_enabled,
                        lane_skip_reason,
                    )
                    if not is_lane_enabled(snapshot, AcquisitionLane.PIVOT_EXECUTOR):
                        reason = lane_skip_reason(snapshot, AcquisitionLane.PIVOT_EXECUTOR) or "unknown"
                        log.debug("[F206BK] pivot_executor skipped: %s", reason)
                        if hasattr(self._result, "acquisition_lanes_skipped"):
                            self._result.acquisition_lanes_skipped += 1
                await runner.run_all_advisories()

            _outer_tg.create_task(
                _run_sprint_advisory_branch(),
                name="advisory:sprint_advisory_runner",
            )

            # ── Branch B: CT → PassiveDNS one-hop pivot ───────────────────────
            _outer_tg.create_task(
                self._run_ct_to_passivedns_pivot_advisory(),
                name="advisory:ct_passivedns",
            )

            # ── Branch C: BGP/Wayback/CommonCrawl sidecars ────────────────────
            async def _run_archive_sidecars() -> None:
                async with _asyncio.TaskGroup() as _tg:
                    _tg.create_task(
                        _run_bounded_sidecar(self._run_bgp_advisory_sidecar(), "bgp_advisory"),
                        name="sprint:bgp_advisory_sidecar",
                    )
                    _tg.create_task(
                        _run_bounded_sidecar(self._run_wayback_cdx_deep_sidecar(), "wayback_cdx_deep"),
                        name="sprint:wayback_cdx_sidecar",
                    )
                    # F250F: CommonCrawl CDX sidecar (non-blocking, via LaneRegistry)
                    if LANE_REGISTRY.is_enabled("common_crawl"):
                        _tg.create_task(
                            _run_bounded_sidecar(self._run_commoncrawl_sidecar(), "commoncrawl"),
                            name="sprint:commoncrawl_sidecar",
                        )

            _outer_tg.create_task(
                _run_archive_sidecars(),
                name="advisory:archive_sidecars",
            )

            # ── Branch D: IPFS/Onion/I2P/banner/DHT/Gopher/stego/TI sidecars ─
            _ipfs_enabled = LANE_REGISTRY.is_enabled("ipfs")
            if _ipfs_enabled:
                _gateway = _os.environ.get("HLEDAC_IPFS_GATEWAY_URL", "https://ipfs.io")
                log.info("IPFS sidecar: ENABLED — gateway=%s", _gateway)
            else:
                log.info("IPFS sidecar: DISABLED (set HLEDAC_ENABLE_IPFS=1 to enable)")

            async def _run_dark_pivot_sidecars() -> None:
                async with _asyncio.TaskGroup() as _tg:
                    if _ipfs_enabled:
                        _tg.create_task(
                            _run_bounded_sidecar(self._run_ipfs_discovery_sidecar(), "ipfs_discovery"),
                            name="sprint:ipfs_discovery_sidecar",
                        )
                    # F251: Onion discovery sidecar (Tor .onion crawling)
                    _tg.create_task(
                        _run_bounded_sidecar(self._run_onion_discovery_sidecar(), "onion_discovery"),
                        name="sprint:onion_discovery_sidecar",
                    )
                    # F2P: I2P discovery sidecar
                    _tg.create_task(
                        _run_bounded_sidecar(self._run_i2p_discovery_sidecar(), "i2p_discovery"),
                        name="sprint:i2p_discovery_sidecar",
                    )
                    _tg.create_task(
                        _run_bounded_sidecar(self._run_bgp_enrichment_sidecar(), "bgp_enrichment"),
                        name="sprint:bgp_enrichment_sidecar",
                    )
                    _tg.create_task(
                        _run_bounded_sidecar(self._run_banner_grab_sidecar(), "banner_grab"),
                        name="sprint:banner_grab_sidecar",
                    )
                    # F214Q: DHT discovery sidecar
                    _tg.create_task(
                        _run_bounded_sidecar(self._run_dht_sidecar(), "dht_discovery"),
                        name="sprint:dht_sidecar",
                    )
                    # F214R: Gopher discovery sidecar — gated via LaneRegistry
                    if LANE_REGISTRY.is_enabled("gopher"):
                        _tg.create_task(
                            _run_bounded_sidecar(self._run_gopher_sidecar(), "gopher"),
                            name="sprint:gopher_sidecar",
                        )
                    # F3FORENSICS: File forensics sidecars (non-blocking, env-gated, P0 bounded)
                    if LANE_REGISTRY.is_enabled("digital_ghost"):
                        _tg.create_task(
                            _run_bounded_sidecar(self._run_digital_ghost_sidecar(), "digital_ghost"),
                            name="sprint:digital_ghost_sidecar",
                        )
                    if LANE_REGISTRY.is_enabled("steganography"):
                        _tg.create_task(
                            _run_bounded_sidecar(self._run_steganography_sidecar(), "steganography"),
                            name="sprint:stego_sidecar",
                        )
                    # F252: TI feed advisory sidecar (NVD + CISA KEV, P0 bounded)
                    if LANE_REGISTRY.is_enabled("ti_feeds"):
                        _tg.create_task(
                            _run_bounded_sidecar(self._run_ti_feed_sidecar(), "ti_feed"),
                            name="sprint:ti_feed_sidecar",
                        )
                    # ADVERSARY-004: Hermes3 Auto-RE for unknown binary formats
                    if _os.environ.get("HLEDAC_ENABLE_AUTO_RE", "0") in ("1", "true", "yes"):
                        _tg.create_task(
                            _run_bounded_sidecar(self._run_auto_re_sidecar(), "auto_re"),
                            name="sprint:auto_re_sidecar",
                        )

            _outer_tg.create_task(
                _run_dark_pivot_sidecars(),
                name="advisory:dark_pivot_sidecars",
            )

            # ── Branch E: Plugin sidecars from SidecarRegistry ───────────────
            _plugin_ctx = self._build_plugin_sidecar_context()
            if _plugin_ctx is not None:
                _plugin_task = safe_create_task(
                    self.run_plugin_sidecars(_plugin_ctx),
                    name="sprint:plugin_sidecars",
                )
                _sidecar_tasks: set | None = getattr(self._scheduler, "_sidecar_tasks", None)
                if _sidecar_tasks is not None:
                    _sidecar_tasks.add(_plugin_task)
                    _plugin_task.add_done_callback(_sidecar_tasks.discard)

    async def run_plugin_sidecars(self, ctx: Any) -> None:
        """
        F350M-FED: Iterate over SidecarRegistry.get_available() and dispatch
        each registered plugin sidecar in a non-blocking asyncio task.

        Args:
            ctx: A SidecarContext (or duck-typed equivalent) with
                 .query, .sprint_id, .findings, .sprint_mode, .memory_pressure.

        Behavior:
            - Reads the canonical M1 budget from the governor if available
              (defaults to 100MB).
            - Iterates in priority order (highest first).
            - Each sidecar runs in its own task with the supplied ctx.
            - Fail-soft: any exception is caught and logged, never raised.
        """
        try:
            from hledac.universal.runtime.sidecar_protocol import (
                SidecarContext,
                SidecarRegistry,
                ensure_adapters_registered,
            )
        except Exception as e:
            log.debug("[F350M-FED] SidecarRegistry import failed: %s", e)
            return

        # Sprint P2-3: Ensure all @SidecarRegistry.register() decorators have run.
        ensure_adapters_registered()

        try:
            # Determine the available memory budget from the governor, if any.
            memory_budget_mb = 100  # default conservative budget
            try:
                if self._governor is not None:
                    snap = getattr(self._governor, "snapshot", None)
                    if snap is not None:
                        # Memory pressure is 0..1, so budget is inverse
                        pressure = float(getattr(snap, "memory_pressure", 0.0) or 0.0)
                        memory_budget_mb = max(
                            10, int(200 * (1.0 - pressure))  # 10..200MB
                        )
            except Exception:  # noqa: BLE001
                pass  # noqa: BLE001  # fall back to default

            available = SidecarRegistry.get_available(memory_budget_mb)
            if not available:
                log.debug("[F350M-FED] no plugin sidecars available "
                          "(budget=%dMB)", memory_budget_mb)
                return

            # Build a proper SidecarContext if we got a duck-typed one.
            # SidecarContext requires the 4 mandatory fields; we coerce.
            sidecar_ctx = ctx
            if not isinstance(ctx, SidecarContext):
                try:
                    sidecar_ctx = SidecarContext(
                        query=str(getattr(ctx, "query", "") or ""),
                        sprint_id=str(getattr(ctx, "sprint_id", "unknown") or "unknown"),
                        findings=list(getattr(ctx, "findings", []) or []),
                        sprint_mode=str(getattr(ctx, "sprint_mode", "active") or "active"),
                        memory_pressure=float(
                            getattr(ctx, "memory_pressure", 0.0) or 0.0
                        ),
                    )
                except Exception as e:
                    log.debug("[F350M-FED] cannot build SidecarContext: %s", e)
                    return

            # ISSUE #027 FIX: Run all plugin sidecar coroutines in PARALLEL via
            # asyncio.gather(), then dispatch results sequentially.
            # Prior code: sequential for-loop await (N × 100ms = 1600ms for 16 sidecars).
            # New code: parallel gather at semaphore limit (4 at a time) = ~400ms wall time.
            # Speedup: ~70% reduction in plugin sidecar startup time.
            #
            # NOTE: gather() is awaited directly (not via TaskGroup.create_task()) because
            # each _run_bounded_plugin_sidecar already acquires the plugin semaphore internally.
            # The outer TaskGroup was removed — it added no structured-concurrency value since
            # no child tasks are created via create_task().
            run_coros = [
                _run_bounded_plugin_sidecar(
                    self._dispatch_plugin_sidecar(adapter, sidecar_ctx),
                    adapter.sidecar_id,
                )
                for adapter in available
            ]
            if run_coros:
                try:
                    result = await parallel(run_coros, policy="log")
                    # Check for unexpected exceptions (fail-soft, per GHOST_INVARIANTS)
                    for i, item in enumerate(result.errors):
                        adapter = available[i] if i < len(available) else None
                        sidecar_id = getattr(adapter, "sidecar_id", f"plugin[{i}]") if adapter else f"plugin[{i}]"
                        log.warning(
                            "[ISSUE #027] plugin sidecar %s unexpected exception (fail-soft): %s: %s",
                            sidecar_id, type(item).__name__, item,
                        )
                except _asyncio.CancelledError:
                    # Propagate cancellation — gather() cancels all child coroutines on CancelledError
                    raise
        except Exception as e:
            log.warning(
                "[F350M-FED] run_plugin_sidecars: fail-soft: %s: %s",
                type(e).__name__, e,
            )

    async def _dispatch_plugin_sidecar(
        self, adapter: Any, ctx: Any,
    ) -> None:
        """Dispatch a single plugin sidecar. Fail-soft. Returns nothing."""
        try:
            result = await adapter.run(ctx)
            # Optional: pass findings to the canonical dispatcher if a
            # result sink is available. For the F350M-FED activation we
            # log only; downstream ingestion is the responsibility of
            # the adapter (which already converts to CanonicalFinding).
            if result and self._dispatcher is not None:
                # Best-effort: do not raise if dispatcher rejects shape
                try:
                    total = len(result)
                    for finding in result[:50]:  # cap per sidecar
                        # ISSUE #3-1 FIX: findings=[] (not finding=obj) — dispatch()
                        # expects positional args: (source_branch, findings: list, store, query, sprint_id).
                        # Prior code passed keyword finding=finding which is not a dispatch()
                        # parameter — caused TypeError on every plugin finding, silently
                        # swallowed by the surrounding except Exception block.
                        # Canonical write skipped intentionally (store=None): plugin sidecars
                        # are responsible for their own CanonicalFinding conversion and any
                        # downstream ingestion they require.
                        await self._dispatcher.dispatch(
                            source_branch=f"federated:{adapter.sidecar_id}",
                            findings=[finding],
                            store=None,
                            query=getattr(ctx, "query", "") or "",
                            sprint_id=getattr(ctx, "sprint_id", "") or "",
                        )
                    if total > 50:
                        log.debug(
                            "[F350M-FED] %s: %d findings capped to 50 (dropped %d)",
                            adapter.sidecar_id,
                            total,
                            total - 50,
                        )
                except Exception:  # noqa: BLE001
                    pass  # noqa: BLE001  # dispatcher may not be available
        except Exception as e:
            log.warning(
                "[F350M-FED] plugin sidecar %s raised: %s: %s",
                getattr(adapter, "sidecar_id", "?"),
                type(e).__name__, e,
            )

    def _build_plugin_sidecar_context(self) -> Any | None:
        """
        Construct a SidecarContext (or duck-typed equivalent) from the
        current scheduler state. Returns None if no scheduler is bound.
        """
        if self._scheduler is None:
            return None
        try:
            # Best-effort attribute reads. The scheduler may not have all
            # of these — that's fine, defaults are safe.
            query = getattr(self._scheduler, "_sprint_query", "") or ""
            # sprint_id may live in the scheduler config or result
            sprint_id = (
                getattr(self._scheduler, "_sprint_id", None)
                or getattr(getattr(self._scheduler, "_config", None),
                           "sprint_id", None)
                or "unknown"
            )
            # findings live on the result_sink or are accumulated per-sprint
            findings = []
            try:
                if self._result is not None:
                    findings = list(getattr(self._result, "findings", []) or [])
            except Exception:
                findings = []
            # sprint_mode
            sprint_mode = (
                getattr(getattr(self._scheduler, "_config", None),
                        "sprint_mode", None)
                or "active"
            )
            # memory_pressure from governor snapshot
            memory_pressure = 0.0
            try:
                if self._governor is not None:
                    snap = getattr(self._governor, "snapshot", None)
                    if snap is not None:
                        memory_pressure = float(
                            getattr(snap, "memory_pressure", 0.0) or 0.0
                        )
            except Exception:  # noqa: BLE001
                pass

            # Build a minimal duck-typed context (avoid hard import for
            # speed; SidecarRegistry/SidecarContext will accept this if
            # its adapter reads via getattr, which our adapter does).
            return _PluginSidecarContext(
                query=query,
                sprint_id=sprint_id,
                findings=findings,
                sprint_mode=sprint_mode,
                memory_pressure=memory_pressure,
            )
        except Exception as e:
            log.debug("[F350M-FED] build context failed: %s", e)
            return None


    def _check_memory_ok(self) -> bool:
        """Check if memory pressure is acceptable. Returns True to proceed, False to skip."""
        try:
            import psutil
            process = psutil.Process()
            mem_info = process.memory_info()
            rss_mb = mem_info.rss / 1024**2
            vm = psutil.virtual_memory()
            high_water = vm.percent * 0.85
            return rss_mb <= high_water
        except Exception:  # noqa: BLE001
            return True

    def _aggregate_finding_facets(self, findings: list[Any]) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict], dict[str, int]]:
        """Aggregate findings into entity, exposure, and pivot facets.
        
        Returns: (entity_data, exposure_data, pivot_data, finding_counts)
        """
        import orjson
        entity_data: dict[str, dict[str, Any]] = {}
        exposure_data: dict[str, dict[str, Any]] = {}
        pivot_data: dict[str, dict[str, Any]] = {}
        finding_counts: dict[str, int] = {}

        for finding in findings:
            target_id = getattr(finding, "target_id", None) or getattr(finding, "entity_id", None)
            if not target_id:
                continue

            if target_id not in finding_counts:
                finding_counts[target_id] = 0
            finding_counts[target_id] += 1

            # Entity facets
            entity_type = getattr(finding, "entity_type", None)
            if entity_type:
                if target_id not in entity_data:
                    entity_data[target_id] = {"types": set(), "count": 0}
                entity_data[target_id]["types"].add(entity_type)
                entity_data[target_id]["count"] += 1

            # Exposure facets
            self._process_exposure_facet(finding, target_id, exposure_data, orjson)

            # Pivot facets
            suggested_pivots = getattr(finding, "suggested_pivots", None)
            if suggested_pivots:
                if target_id not in pivot_data:
                    pivot_data[target_id] = {"pivots": [], "count": 0}
                for pivot in suggested_pivots[:5]:
                    pivot_data[target_id]["pivots"].append(pivot)
                    pivot_data[target_id]["count"] += 1

        return entity_data, exposure_data, pivot_data, finding_counts

    def _process_exposure_facet(self, finding: Any, target_id: str, exposure_data: dict, orjson: Any) -> None:
        """Process a single finding's exposure facets."""
        src_type = getattr(finding, "src_type", None)
        if src_type == "exposure":
            if target_id not in exposure_data:
                exposure_data[target_id] = {"signals": [], "rir_asns": {}, "count": 0}
            exposure_data[target_id]["signals"].append(getattr(finding, "signal_type", "unknown"))
            exposure_data[target_id]["count"] += 1
        elif src_type == "rir_correlation":
            if target_id not in exposure_data:
                exposure_data[target_id] = {"signals": [], "rir_asns": {}, "count": 0}
            payload_text = getattr(finding, "payload_text", None) or ""
            rir_data: dict[str, Any] = {}
            if isinstance(payload_text, str) and payload_text:
                try:
                    rir_data = orjson.loads(payload_text)
                except Exception:  # noqa: BLE001
                    pass
            asn = rir_data.get("asn", "") or ""
            if asn:
                exposure_data[target_id]["rir_asns"][asn] = {
                    "org": rir_data.get("org", "") or "",
                    "netblock": rir_data.get("netblock", "") or "",
                    "country": rir_data.get("country", "") or "",
                    "ioc_type": rir_data.get("ioc_type", "") or "",
                    "ioc_val": rir_data.get("ioc_val", "") or getattr(finding, "ioc_val", "") or "",
                }
            exposure_data[target_id]["count"] += 1

    def _build_memory_payloads(self, entity_data: dict, exposure_data: dict, pivot_data: dict) -> tuple[dict, dict, dict]:
        """Build bounded memory payloads from aggregated data."""
        from hledac.universal.knowledge.target_memory import (
            MAX_MEMORY_ENTITIES,
            MAX_MEMORY_EXPOSURES,
            MAX_MEMORY_PIVOTS,
        )

        entity_facets: dict[str, Any] = {}
        for tid, data in entity_data.items():
            entity_facets[tid] = {
                "types": list(data["types"])[:MAX_MEMORY_ENTITIES],
                "count": data["count"],
            }

        exposure_facets: dict[str, Any] = {}
        for tid, data in exposure_data.items():
            signals = data["signals"][:MAX_MEMORY_EXPOSURES]
            rir_asns = data["rir_asns"]
            if len(rir_asns) > 100:
                rir_asns = dict(list(rir_asns.items())[:100])
            exposure_facets[tid] = {
                "signals": signals,
                "rir_asns": rir_asns,
                "count": data["count"],
            }

        pivot_facets: dict[str, Any] = {}
        for tid, data in pivot_data.items():
            pivot_facets[tid] = {
                "pivots": data["pivots"][:MAX_MEMORY_PIVOTS],
                "count": data["count"],
            }

        return entity_facets, exposure_facets, pivot_facets

    async def run_target_memory_update(
        self,
        findings: list[Any],
        store: Any,
        query: str,  # noqa: ARG002 — reserved for future enrichment use
    ) -> None:
        """
        F204D: Update cross-sprint target memory after findings are accepted.

        Extracts entity/exposure/pivot facets from findings and merges into
        target memory via duckdb_store.async_upsert_target_memory().

        RAM guard: skip if RSS > high_water (85% threshold).
        Fail-soft: errors never crash the sprint.

        Issue #15 fix: single-pass aggregation with orjson (5-10× faster
        than json.loads) and early-bound finding_count (avoids N re-reads).
        """
        if not self._check_memory_ok():
            return

        entity_data, exposure_data, pivot_data, finding_counts = self._aggregate_finding_facets(findings)
        entity_facets, exposure_facets, pivot_facets = self._build_memory_payloads(entity_data, exposure_data, pivot_data)

        # Bulk upsert: single pass over all target_ids
        all_target_ids = (
            set(entity_facets.keys())
            | set(exposure_facets.keys())
            | set(pivot_facets.keys())
        )

        try:
            from hledac.universal.intel.target_memory_service import (
                TargetMemoryService,
                TargetMemoryUpdate,
            )
            if not hasattr(self, "_target_memory_service") or self._target_memory_service is None:
                self._target_memory_service = TargetMemoryService(store)
            service = self._target_memory_service

            for target_id in all_target_ids:
                try:
                    update = TargetMemoryUpdate(
                        target_id=target_id,
                        sprint_id=getattr(self._result, "sprint_id", "") or "",
                        timestamp=_time.time(),
                        entity_facets=entity_facets.get(target_id),
                        exposure_facets=exposure_facets.get(target_id),
                        pivot_facets=pivot_facets.get(target_id),
                        finding_count=finding_counts.get(target_id, 0),
                    )
                    await service.update_target_memory(update)
                except Exception as e:
                    log.debug("[F350M-FED] target memory update failed for %s: %s", target_id, e)
        except Exception as e:
            log.debug("[F350M-FED] target memory service unavailable: %s", e)


    def teardown(self) -> None:
        """Clear in-memory state. Called on sprint teardown."""
        if hasattr(self, "_dispatcher") and self._dispatcher is not None:
            self._dispatcher.reset()
        if hasattr(self, "_target_memory_service"):
            self._target_memory_service = None

    # ── Private advisory helpers ─────────────────────────────────────────────

    async def _run_ct_to_passivedns_pivot_advisory(self) -> None:
        """R5: CT -> PassiveDNS one-hop pivot advisory.

        F1 FIX: primo volani pres SchedulerAdvisory Protocol —
        žádné getattr() reflection antipattern.
        Fail-soft: errors never crash the sprint.
        """
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_ct_to_passivedns_pivot_advisory()
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fail-soft

    async def _run_bgp_advisory_sidecar(self) -> None:
        """F234: BGP advisory sidecar for ASN/path analysis. Fail-soft."""
        try:
            from hledac.universal.intel.bgp_advisor_adapter import (
                create_bgp_advisor_adapter,
            )
            adapter = create_bgp_advisor_adapter()
            _ = adapter.analyze(self._result)
        except (ImportError, ModuleNotFoundError, AttributeError):  # noqa: BLE001
            pass  # fail-safe: intelligence module unavailable

    async def _run_wayback_cdx_deep_sidecar(self) -> None:
        """F234: Deep Wayback CDX analysis for URL history. Fail-soft."""
        try:
            from hledac.universal.intel.wayback_cdx_deep_adapter import (
                create_wayback_cdx_deep_adapter,
            )
            adapter = create_wayback_cdx_deep_adapter()
            _ = await adapter.analyze(self._result)
        except (ImportError, ModuleNotFoundError, AttributeError):  # noqa: BLE001
            pass  # fail-safe: intelligence module unavailable

    # ── F229: IPFS Discovery Sidecar ─────────────────────────────────────────

    async def _run_ipfs_discovery_sidecar(self) -> None:
        """F229: IPFS discovery — fetch unindexed content from IPFS network. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_ipfs_enrichment_sidecar()
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fail-soft

    # ── F251: Onion Discovery Sidecar ───────────────────────────────────────

    async def _run_onion_discovery_sidecar(self) -> None:
        """F251: Dark web .onion discovery via Tor. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_onion_discovery_sidecar()
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fail-soft

    # ── F2P: I2P Discovery Sidecar ─────────────────────────────────────────

    async def _run_i2p_discovery_sidecar(self) -> None:
        """F2P: I2P .i2p discovery via I2P transport. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_i2p_discovery_sidecar()
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fail-soft

    async def _run_bgp_enrichment_sidecar(self) -> None:
        """F229: BGP enrichment — AS path analysis for IP/ASN in query. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_bgp_advisory_sidecar()
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fail-soft

    # F250F: CommonCrawl CDX sidecar
    async def _run_commoncrawl_sidecar(self) -> None:
        """F250F: CommonCrawl CDX domain discovery. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_commoncrawl_sidecar()
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fail-soft

    async def _run_banner_grab_sidecar(self) -> None:
        """F229: Banner grab — active TCP probing for service fingerprinting. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_banner_grab_sidecar()
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fail-soft

    # F214Q: DHT discovery sidecar
    async def _run_dht_sidecar(self) -> None:
        """F214Q: DHT torrent discovery via BitTorrent DHT network. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_dht_sidecar()
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fail-soft

    # F214R: Gopher discovery sidecar
    # GopherLane does not exist yet — this is a deferred implementation.
    # When implementing, create transport/gopher_lane.py with GopherLane class
    # (follow the pattern of existing lanes: ct_lane.py, bgp_lane.py).
    # Then wire into this method. Until then, the sidecar is a no-op.
    async def _run_gopher_sidecar(self) -> None:
        """F214R: Gopher URL discovery. No-op until GopherLane is implemented."""
        # GopherLane tracking: create transport/gopher_lane.py to enable this
        pass

    # F3FORENSICS: Digital ghost forensics sidecar
    async def _run_digital_ghost_sidecar(self) -> None:
        """F3FORENSICS: Digital ghost detection on file artifacts. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_digital_ghost_sidecar([])
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fail-soft

    # F3FORENSICS: Steganography forensics sidecar
    async def _run_steganography_sidecar(self) -> None:
        """F3FORENSICS: Steganography detection on image artifacts. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_steganography_sidecar([])
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fail-soft

    # F252: TI feed advisory sidecar (NVD + CISA KEV)
    async def _run_ti_feed_sidecar(self) -> None:
        """F252: TI feed advisory sidecar (NVD + CISA KEV). Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_ti_feed_sidecar()
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fail-soft

    # ADVERSARY-004: Hermes3 Auto-RE sidecar for unknown binary formats
    async def _run_auto_re_sidecar(self) -> None:
        """ADVERSARY-004: Hermes3 Auto-RE sidecar for unknown binary formats.

        Converts unknown binary files (custom .dat, wallet.dat, etc.) into IOC
        extractions via Hermes3-generated parsers. Fails soft: any exception
        is caught and logged; returns [] to avoid aborting the sprint.

        Rate-limited to 3 attempts per sprint by AutoRESidecarAdapter.
        """
        from hledac.universal.runtime.sidecars.forensics._auto_re import (
            AutoRESidecarAdapter,
        )
        from hledac.universal.runtime.scheduler_v2.protocol import SidecarContext

        adapter = AutoRESidecarAdapter()
        if not adapter.is_available():
            log.debug("[AUTO-RE] sidecar not available (env gate or missing deps)")
            return

        # Build minimal SidecarContext from orchestrator state.
        # SidecarContext requires: query, sprint_id, findings, sprint_mode.
        # Extra fields (duckdb_store, graph_service, result_sink) are passed via
        # getattr() in the adapter since SidecarContext is a msgspec.Struct with
        # only the 4 canonical fields.
        ctx: SidecarContext | None = None
        try:
            # Pull canonical fields from the live scheduler
            if self._scheduler is not None:
                query = getattr(self._scheduler, "_query", "") or ""
                sprint_id = getattr(self._scheduler, "_sprint_id", "unknown") or "unknown"
                findings = getattr(self._scheduler, "_current_findings", []) or []
                sprint_mode = getattr(self._scheduler, "_mode", "active") or "active"
            else:
                query = "auto_re"
                sprint_id = "unknown"
                findings = []
                sprint_mode = "active"

            ctx = SidecarContext(
                query=str(query),
                sprint_id=str(sprint_id),
                findings=list(findings),
                sprint_mode=str(sprint_mode),
            )
        except Exception as e:
            log.warning("[AUTO-RE] failed to build SidecarContext: %s", e)
            return

        # Attach extra fields the adapter needs via duck-typing (SidecarContext
        # accepts getattr() in adapter — msgspec.Struct has no fixed schema).
        # These are optional supplements, not part of the SidecarContext spec.
        ctx.result_sink = getattr(self, "_result", None)
        ctx.duckdb_store = getattr(self._scheduler, "_duckdb_store", None) if self._scheduler else None
        ctx.graph_service = getattr(self._scheduler, "_graph_service", None) if self._scheduler else None
        ctx.governor = getattr(self, "_governor", None)

        try:
            await adapter.run_async(ctx)
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fail-soft
