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

ADVISORY CALLBACK SEAM (bounded, F226)
──────────────────────────────────────
SidecarOrchestrator owns scheduling and dispatch. SprintScheduler still owns
the inline advisory implementations. One advisory crosses the scheduler facade
via getattr; two are self-contained adapters. This is an explicit bounded seam:

  1. _run_ct_to_passivedns_pivot_advisory  → getattr(scheduler, "_run_ct_to_passivedns_pivot_advisory")
  2. _run_bgp_advisory_sidecar             → own BGPAdvisorAdapter (no getattr)
  3. _run_wayback_cdx_deep_sidecar         → own WaybackCDXDeepAdapter (no getattr)

These three callback names are the ONLY permitted scheduler advisory callbacks.
No new `getattr(self._scheduler, "_run_*")` calls may be added without updating
the seal test in tests/test_sidecar_orchestrator.py.

Extraction trigger: if advisory logic exceeds ~50 lines OR gains external callers
beyond these three methods, extract to a dedicated adapter class — do not grow
the getattr seam.
"""

from __future__ import annotations

import asyncio as _asyncio
import logging
import os as _os
import time as _time
from typing import Any

from hledac.universal.runtime.sidecar_bus import create_sidecar_bus
from hledac.universal.runtime.sidecar_dispatcher import (
    DispatchOutcome,
    SidecarDispatcher,
)

log = logging.getLogger(__name__)

# Deferred import to avoid circular dep at mod load time
_SPRINT_ADVISORY_RUNNER: Any = None


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
    scheduler:       SprintScheduler reference for deferred advisory access.
    """

    __slots__ = (
        "_result",
        "_governor",
        "_scheduler",
        "_bus",
        "_dispatcher",
        "_target_memory_service",
    )

    def __init__(
        self,
        result_sink: Any,
        governor: Any = None,
        scheduler: Any = None,
    ) -> None:
        self._result = result_sink
        self._governor = governor
        self._scheduler = scheduler
        _profile = getattr(getattr(scheduler, "_config", None), "acquisition_profile", None) if scheduler else None
        self._bus = create_sidecar_bus(governor=governor, acquisition_profile=_profile)
        self._dispatcher = SidecarDispatcher(
            bus=self._bus,
            governor=governor,
        )

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
        F206D: Run all teardown advisory steps via SprintAdvisoryRunner.

        Canonical teardown entry point. Each step is fail-soft;
        CancelledError propagates to caller.

        Steps:
          1. run_all_advisories  (pivot_planner, pivot_executor,
                                  resource_governor, analyst_brief)
          2. run_ct_to_passivedns_pivot_advisory  (R5)
          3. run_bgp_advisory_sidecar             (F234, non-blocking)
          4. run_wayback_cdx_deep_sidecar         (F234, non-blocking)
          5. run_ipfs_discovery_sidecar          (F229, gated by HLEDAC_ENABLE_IPFS)
          6. run_bgp_enrichment_sidecar          (F229, gated by HLEDAC_ENABLE_BGP)
          7. run_banner_grab_sidecar              (F229, gated by HLEDAC_ENABLE_BANNER_GRAB)
          8. run_plugin_sidecars                  (F350M-FED, iterates SidecarRegistry
                                                    for any @SidecarRegistry.register'd
                                                    adapter — federated_research is
                                                    the first such plugin)
        """
        # Step 1: SprintAdvisoryRunner for 4 core advisories
        if self._scheduler is not None:
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

        # Step 2: CT -> PassiveDNS one-hop pivot
        await self._run_ct_to_passivedns_pivot_advisory()

        # Steps 3-4: Non-blocking advisory sidecars
        if self._scheduler is not None:
            bg_tasks: set | None = getattr(self._scheduler, "_bg_tasks", None)
            if bg_tasks is None:
                bg_tasks = set()
            _bgp_task = _asyncio.create_task(
                self._run_bgp_advisory_sidecar(), name="sprint:bgp_advisory_sidecar"
            )
            bg_tasks.add(_bgp_task)
            _bgp_task.add_done_callback(bg_tasks.discard)
            _wayback_task = _asyncio.create_task(
                self._run_wayback_cdx_deep_sidecar(), name="sprint:wayback_cdx_sidecar"
            )
            bg_tasks.add(_wayback_task)
            _wayback_task.add_done_callback(bg_tasks.discard)
            # F250F: CommonCrawl CDX sidecar (non-blocking, HLEDAC_ENABLE_COMMONCRAWL=1)
            _cc_env = _os.environ.get("HLEDAC_ENABLE_COMMONCRAWL", "").strip()
            if _cc_env in ("1", "true"):
                _cc_task = _asyncio.create_task(
                    self._run_commoncrawl_sidecar(), name="sprint:commoncrawl_sidecar"
                )
                bg_tasks.add(_cc_task)
                _cc_task.add_done_callback(bg_tasks.discard)

        # Steps 5-7: F229 deep OSINT sidecars (non-blocking, env-gated)
        if self._scheduler is not None:
            bg_tasks: set | None = getattr(self._scheduler, "_bg_tasks", None)
            if bg_tasks is None:
                bg_tasks = set()
            _ipfs_env = _os.environ.get("HLEDAC_ENABLE_IPFS", "").strip()
            _ipfs_enabled = _ipfs_env in ("1", "true", "True")
            if _ipfs_enabled:
                _gateway = _os.environ.get("HLEDAC_IPFS_GATEWAY_URL", "https://ipfs.io")
                log.info("IPFS sidecar: ENABLED — gateway=%s", _gateway)
            else:
                log.info("IPFS sidecar: DISABLED (set HLEDAC_ENABLE_IPFS=1 to enable)")
            if _ipfs_enabled:
                _ipfs_task = _asyncio.create_task(
                    self._run_ipfs_discovery_sidecar(), name="sprint:ipfs_discovery_sidecar"
                )
                bg_tasks.add(_ipfs_task)
                _ipfs_task.add_done_callback(bg_tasks.discard)
            # F251: Onion discovery sidecar (Tor .onion crawling)
            _onion_task = _asyncio.create_task(
                self._run_onion_discovery_sidecar(), name="sprint:onion_discovery_sidecar"
            )
            bg_tasks.add(_onion_task)
            _onion_task.add_done_callback(bg_tasks.discard)
            # F2P: I2P discovery sidecar
            _i2p_task = _asyncio.create_task(
                self._run_i2p_discovery_sidecar(), name="sprint:i2p_discovery_sidecar"
            )
            bg_tasks.add(_i2p_task)
            _i2p_task.add_done_callback(bg_tasks.discard)
            _bgp_enr_task = _asyncio.create_task(
                self._run_bgp_enrichment_sidecar(), name="sprint:bgp_enrichment_sidecar"
            )
            bg_tasks.add(_bgp_enr_task)
            _bgp_enr_task.add_done_callback(bg_tasks.discard)
            _banner_task = _asyncio.create_task(
                self._run_banner_grab_sidecar(), name="sprint:banner_grab_sidecar"
            )
            bg_tasks.add(_banner_task)
            _banner_task.add_done_callback(bg_tasks.discard)
            # F214Q: DHT discovery sidecar
            _dht_task = _asyncio.create_task(
                self._run_dht_sidecar(), name="sprint:dht_sidecar"
            )
            bg_tasks.add(_dht_task)
            _dht_task.add_done_callback(bg_tasks.discard)
            # F214R: Gopher discovery sidecar
            _gopher_task = _asyncio.create_task(
                self._run_gopher_sidecar(), name="sprint:gopher_sidecar"
            )
            bg_tasks.add(_gopher_task)
            _gopher_task.add_done_callback(bg_tasks.discard)

            # F3FORENSICS: File forensics sidecars (non-blocking, env-gated)
            _dg_env = _os.environ.get("HLEDAC_ENABLE_DIGITAL_GHOST", "0")
            if _dg_env == "1":
                _dg_task = _asyncio.create_task(
                    self._run_digital_ghost_sidecar(), name="sprint:digital_ghost_sidecar"
                )
                bg_tasks.add(_dg_task)
                _dg_task.add_done_callback(bg_tasks.discard)

            _stego_env = _os.environ.get("HLEDAC_ENABLE_STEGANOGRAPHY", "0")
            if _stego_env == "1":
                _stego_task = _asyncio.create_task(
                    self._run_steganography_sidecar(), name="sprint:stego_sidecar"
                )
                bg_tasks.add(_stego_task)
                _stego_task.add_done_callback(bg_tasks.discard)

            # F252: TI feed advisory sidecar (NVD + CISA KEV)
            _ti_env = _os.environ.get("HLEDAC_ENABLE_TI_FEEDS", "1")
            if _ti_env == "1":
                _ti_task = _asyncio.create_task(
                    self._run_ti_feed_sidecar(), name="sprint:ti_feed_sidecar"
                )
                bg_tasks.add(_ti_task)
                _ti_task.add_done_callback(bg_tasks.discard)

        # Step 8 (F350M-FED): Plugin sidecars from SidecarRegistry.
        # Non-blocking, fail-soft. Each registered adapter is dispatched as
        # its own asyncio task. The federated_research sidecar is the first
        # user of this seam; future plugins can register via
        # @SidecarRegistry.register("my_id") and will be auto-discovered.
        _plugin_ctx = self._build_plugin_sidecar_context()
        if _plugin_ctx is not None:
            _plugin_task = _asyncio.create_task(
                self.run_plugin_sidecars(_plugin_ctx),
                name="sprint:plugin_sidecars",
            )
            if self._scheduler is not None:
                _bg_tasks: set | None = getattr(self._scheduler, "_bg_tasks", None)
                if _bg_tasks is not None:
                    _bg_tasks.add(_plugin_task)
                    _plugin_task.add_done_callback(_bg_tasks.discard)

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
            from runtime.sidecar_protocol import (
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
            except Exception:
                pass  # fall back to default

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

            # Dispatch each plugin sidecar as its own non-blocking task.
            # Each inner task is tracked in _bg_tasks so teardown can await them.
            for adapter in available:
                try:
                    _inner_task = _asyncio.create_task(
                        self._dispatch_plugin_sidecar(adapter, sidecar_ctx),
                        name=f"sprint:plugin_sidecar:{adapter.sidecar_id}",
                    )
                    if self._scheduler is not None:
                        _bg: set | None = getattr(self._scheduler, "_bg_tasks", None)
                        if _bg is not None:
                            _bg.add(_inner_task)
                            _inner_task.add_done_callback(_bg.discard)
                except Exception as e:
                    log.warning(
                        "[F350M-FED] failed to launch plugin sidecar %s: %s",
                        adapter.sidecar_id, e,
                    )
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
                    for finding in result[:50]:  # cap per sidecar
                        await self._dispatcher.dispatch(
                            source_branch=f"federated:{adapter.sidecar_id}",
                            finding=finding,
                            query=getattr(ctx, "query", "") or "",
                        )
                except Exception:
                    pass  # dispatcher may not be available
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
            except Exception:
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

    async def run_target_memory_update(
        self,
        findings: list[Any],
        store: Any,
        query: str,
    ) -> None:
        """
        F204D: Update cross-sprint target memory after findings are accepted.

        Extracts entity/exposure/pivot facets from findings and merges into
        target memory via duckdb_store.async_upsert_target_memory().

        RAM guard: skip if RSS > high_water (85% threshold).
        Fail-soft: errors never crash the sprint.
        """
        import json as _json

        try:
            import psutil

            process = psutil.Process()
            mem_info = process.memory_info()
            rss_mb = mem_info.rss / 1024**2
            vm = psutil.virtual_memory()
            high_water = vm.percent * 0.85
            if rss_mb > high_water:
                return
        except Exception:
            pass

        entity_facets: dict[str, Any] = {}
        exposure_facets: dict[str, Any] = {}
        pivot_facets: dict[str, Any] = {}

        MAX_MEMORY_ENTITIES = 1000  # noqa: N806
        MAX_MEMORY_EXPOSURES = 500  # noqa: N806
        MAX_MEMORY_PIVOTS = 200  # noqa: N806

        for finding in findings:
            target_id = getattr(finding, "target_id", None) or getattr(finding, "entity_id", None)
            if not target_id:
                continue
            if hasattr(finding, "entity_type"):
                if target_id not in entity_facets:
                    entity_facets[target_id] = {"types": set(), "count": 0}
                entity_facets[target_id]["types"].add(getattr(finding, "entity_type", "unknown"))
                entity_facets[target_id]["count"] += 1
            if hasattr(finding, "src_type") and getattr(finding, "src_type", None) == "exposure":
                if target_id not in exposure_facets:
                    exposure_facets[target_id] = {"signals": [], "count": 0}
                exposure_facets[target_id]["signals"].append(getattr(finding, "signal_type", "unknown"))
                exposure_facets[target_id]["count"] += 1
            if hasattr(finding, "suggested_pivots"):
                pivots = getattr(finding, "suggested_pivots", [])
                for pivot in pivots[:5]:
                    if target_id not in pivot_facets:
                        pivot_facets[target_id] = {"pivots": [], "count": 0}
                    pivot_facets[target_id]["pivots"].append(pivot)
                    pivot_facets[target_id]["count"] += 1
            if hasattr(finding, "src_type") and getattr(finding, "src_type", None) == "rir_correlation":
                payload_text = getattr(finding, "payload_text", None) or ""
                try:
                    rir_data = _json.loads(payload_text) if isinstance(payload_text, str) else {}
                except Exception:
                    rir_data = {}
                asn = rir_data.get("asn", "") or ""
                org = rir_data.get("org", "") or ""
                netblock = rir_data.get("netblock", "") or ""
                country = rir_data.get("country", "") or ""
                ioc_type = rir_data.get("ioc_type", "") or ""
                ioc_val_from_payload = rir_data.get("ioc_val", "") or getattr(finding, "ioc_val", "") or ""
                if target_id not in exposure_facets:
                    exposure_facets[target_id] = {"signals": [], "rir_asns": {}, "count": 0}
                rir_asns = exposure_facets[target_id].setdefault("rir_asns", {})
                if asn:
                    rir_asns[asn] = {
                        "org": org,
                        "netblock": netblock,
                        "country": country,
                        "ioc_type": ioc_type,
                        "ioc_val": ioc_val_from_payload,
                    }
                exposure_facets[target_id]["count"] += 1

        for tid in entity_facets:
            entity_facets[tid]["types"] = list(entity_facets[tid]["types"])[:MAX_MEMORY_ENTITIES]
        for tid in list(exposure_facets.keys()):
            exposure_facets[tid]["signals"] = exposure_facets[tid]["signals"][:MAX_MEMORY_EXPOSURES]
            if "rir_asns" in exposure_facets[tid]:
                rir_asns = exposure_facets[tid]["rir_asns"]
                if len(rir_asns) > 100:
                    exposure_facets[tid]["rir_asns"] = dict(list(rir_asns.items())[:100])
        for tid in list(pivot_facets.keys()):
            pivot_facets[tid]["pivots"] = pivot_facets[tid]["pivots"][:MAX_MEMORY_PIVOTS]

        sprint_id = getattr(self._result, "sprint_id", "") or ""
        now = _time.time()

        for target_id in (
            set(entity_facets.keys())
            | set(exposure_facets.keys())
            | set(pivot_facets.keys())
        ):
            try:
                from hledac.universal.intelligence.target_memory_service import (
                    TargetMemoryService,
                    TargetMemoryUpdate,
                )
                update = TargetMemoryUpdate(
                    target_id=target_id,
                    sprint_id=sprint_id,
                    finding_count=len(findings),
                    entity_facets=entity_facets.get(target_id, {}),
                    exposure_facets=exposure_facets.get(target_id, {}),
                    pivot_facets=pivot_facets.get(target_id, {}),
                    observed_ts=now,
                )
                service = getattr(self, "_target_memory_service", None) or TargetMemoryService()
                if not hasattr(self, "_target_memory_service") or self._target_memory_service is None:
                    self._target_memory_service = service
                merged = service.mrg_update(update)
                await store.async_upsert_target_memory(merged)
            except (ImportError, ModuleNotFoundError):
                pass  # fail-safe: target_memory_service unavailable
            except Exception:
                pass  # Fail-soft

    def reset(self) -> None:
        """Clear in-memory state. Called on sprint teardown."""
        if hasattr(self, "_dispatcher") and self._dispatcher is not None:
            self._dispatcher.reset()
        if hasattr(self, "_target_memory_service"):
            self._target_memory_service = None

    # ── Private advisory helpers ─────────────────────────────────────────────

    async def _run_ct_to_passivedns_pivot_advisory(self) -> None:
        """R5: CT -> PassiveDNS one-hop pivot advisory.

        Delegates to SprintScheduler._run_ct_to_passivedns_pivot_advisory().
        Fail-soft: errors never crash the sprint.
        """
        if self._scheduler is None:
            return
        try:
            method = getattr(self._scheduler, "_run_ct_to_passivedns_pivot_advisory", None)
            if method is not None:
                await method()
        except Exception:
            pass  # Fail-soft

    async def _run_bgp_advisory_sidecar(self) -> None:
        """F234: BGP advisory sidecar for ASN/path analysis. Fail-soft."""
        try:
            from hledac.universal.intelligence.bgp_advisor_adapter import (
                create_bgp_advisor_adapter,
            )
            adapter = create_bgp_advisor_adapter()
            _ = adapter.analyze(self._result)
        except (ImportError, ModuleNotFoundError, AttributeError):
            pass  # fail-safe: intelligence module unavailable

    async def _run_wayback_cdx_deep_sidecar(self) -> None:
        """F234: Deep Wayback CDX analysis for URL history. Fail-soft."""
        try:
            from hledac.universal.intelligence.wayback_cdx_deep_adapter import (
                create_wayback_cdx_deep_adapter,
            )
            adapter = create_wayback_cdx_deep_adapter()
            _ = await adapter.analyze(self._result)
        except (ImportError, ModuleNotFoundError, AttributeError):
            pass  # fail-safe: intelligence module unavailable

    # ── F229: IPFS Discovery Sidecar ─────────────────────────────────────────

    async def _run_ipfs_discovery_sidecar(self) -> None:
        """F229: IPFS discovery — fetch unindexed content from IPFS network. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_ipfs_enrichment_sidecar()
        except Exception:
            pass  # Fail-soft

    # ── F251: Onion Discovery Sidecar ───────────────────────────────────────

    async def _run_onion_discovery_sidecar(self) -> None:
        """F251: Dark web .onion discovery via Tor. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_onion_discovery_sidecar()
        except Exception:
            pass  # Fail-soft

    # ── F2P: I2P Discovery Sidecar ─────────────────────────────────────────

    async def _run_i2p_discovery_sidecar(self) -> None:
        """F2P: I2P .i2p discovery via I2P transport. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_i2p_discovery_sidecar()
        except Exception:
            pass  # Fail-soft

    async def _run_bgp_enrichment_sidecar(self) -> None:
        """F229: BGP enrichment — AS path analysis for IP/ASN in query. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_bgp_advisory_sidecar()
        except Exception:
            pass  # Fail-soft

    # F250F: CommonCrawl CDX sidecar
    async def _run_commoncrawl_sidecar(self) -> None:
        """F250F: CommonCrawl CDX domain discovery. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_commoncrawl_sidecar()
        except Exception:
            pass  # Fail-soft

    async def _run_banner_grab_sidecar(self) -> None:
        """F229: Banner grab — active TCP probing for service fingerprinting. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_banner_grab_sidecar()
        except Exception:
            pass  # Fail-soft

    # F214Q: DHT discovery sidecar
    async def _run_dht_sidecar(self) -> None:
        """F214Q: DHT torrent discovery via BitTorrent DHT network. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_dht_sidecar()
        except Exception:
            pass  # Fail-soft

    # F214R: Gopher discovery sidecar (placeholder — gopher transport available but no sidecar adapter yet)
    async def _run_gopher_sidecar(self) -> None:
        """F214R: Gopher URL discovery. Fail-soft placeholder."""
        # TODO: implement gopher sidecar adapter when gopherlane is available
        pass

    # F3FORENSICS: Digital ghost forensics sidecar
    async def _run_digital_ghost_sidecar(self) -> None:
        """F3FORENSICS: Digital ghost detection on file artifacts. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_digital_ghost_sidecar([])
        except Exception:
            pass  # Fail-soft

    # F3FORENSICS: Steganography forensics sidecar
    async def _run_steganography_sidecar(self) -> None:
        """F3FORENSICS: Steganography detection on image artifacts. Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_steganography_sidecar([])
        except Exception:
            pass  # Fail-soft

    # F252: TI feed advisory sidecar (NVD + CISA KEV)
    async def _run_ti_feed_sidecar(self) -> None:
        """F252: TI feed advisory sidecar (NVD + CISA KEV). Fail-soft."""
        if self._scheduler is None:
            return
        try:
            await self._scheduler._run_ti_feed_sidecar()
        except Exception:
            pass  # Fail-soft
