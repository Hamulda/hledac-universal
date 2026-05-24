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
            SprintAdvisoryRunner as _SAR,
        )
        _SPRINT_ADVISORY_RUNNER = _SAR
    return _SPRINT_ADVISORY_RUNNER


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
        """
        # Step 1: SprintAdvisoryRunner for 4 core advisories
        if self._scheduler is not None:
            SAR = _get_sprint_advisory_runner()
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

        # Steps 5-7: F229 deep OSINT sidecars (non-blocking, env-gated)
        if self._scheduler is not None:
            bg_tasks: set | None = getattr(self._scheduler, "_bg_tasks", None)
            if bg_tasks is None:
                bg_tasks = set()
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

        MAX_MEMORY_ENTITIES = 1000
        MAX_MEMORY_EXPOSURES = 500
        MAX_MEMORY_PIVOTS = 200

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
            await self._scheduler._run_ipfs_discovery_sidecar()
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
            await self._scheduler._run_bgp_enrichment_sidecar()
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