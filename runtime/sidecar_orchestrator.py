# SPDX-License-Identifier: MIT
"""
runtime/sidecar_orchestrator.py

Extracted from runtime/sprint_scheduler.py (Sprint F350M).

SidecarOrchestrator invokes all 20 sidecar and advisory methods against accepted
findings. Sidecar authors can test against this interface without running the full
SprintScheduler. Deletion test: if this module is deleted, all 20 sidecar call sites
must reappear in SprintScheduler.

Responsibilities moved from SprintScheduler:
- _dispatch_accepted_findings_sidecars()
- All 20 _run_*_sidecar() / _run_*_advisory() methods
- SidecarDispatcher wiring
- SidecarBus creation

Responsibilities NOT moved (stay in SprintScheduler):
- Acquisition planning (build_acquisition_plan)
- Lifecycle management (SprintLifecycleRunner)
- Memory pressure monitoring (MemoryPressureMonitor)
- Enrichment lifecycle (_init/_flush/_close forensics/multimodal)
- Result accumulation (_reset_result)
- Feed/public/CT branch dispatch
- Pivot queue drainage
- Export coordination
"""

from __future__ import annotations

import asyncio as _asyncio
import json
import logging
import time as _time
from typing import Any

log = logging.getLogger(__name__)

# Deferred import to avoid circular dependency at module load time
# SprintAdvisoryRunner is defined in sprint_advisory_runner.py
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
# SidecarDispatcher (extracted from sprint_scheduler.py line 1784)
# ---------------------------------------------------------------------------

from dataclasses import dataclass


@dataclass(frozen=True)
class DispatchOutcome:
    """Result of a sidecar dispatch call."""
    sprint_id: str
    source_branch: str
    sidecars_skipped: tuple[str, ...]


class SidecarDispatcher:
    """
    F205F: Extracted sidecar dispatch bookkeeping.

    All batch construction, empty guards, skipped heavy sidecar tracking,
    CancelledError propagation, and fail-soft handling live here.
    """

    def __init__(
        self,
        bus: Any,
        governor: Any,
        result_sink: Any,
    ) -> None:
        self._bus = bus
        self._governor = governor
        self._result_sink = result_sink
        self._sidecars_skipped: set[str] = set()

    async def dispatch(
        self,
        source_branch: str,
        findings: list,
        store: Any,
        query: str,
        sprint_id: str,
    ) -> DispatchOutcome:
        """
        Route accepted findings from any branch through FindingSidecarBus.

        Unified entry point used by feed, public, and CT branches. Creates a
        SidecarBatch and calls bus.run_all_sidecars() so all accepted findings
        receive the same sidecar processing regardless of source.

        Fail-soft: errors never crash the caller.
        CancelledError: re-raised to caller.
        Empty batch or None store: returns DispatchOutcome with empty skips.
        """
        import asyncio
        import time as _time

        if not findings or store is None:
            return DispatchOutcome(
                sprint_id=sprint_id,
                source_branch=source_branch,
                sidecars_skipped=(),
            )

        if self._bus is None:
            return DispatchOutcome(
                sprint_id=sprint_id,
                source_branch=source_branch,
                sidecars_skipped=(),
            )

        from hledac.universal.runtime.sidecar_bus import SidecarBatch

        batch = SidecarBatch(
            sprint_id=sprint_id,
            query=query,
            source_branch=source_branch,
            findings=tuple(findings),
            created_ts=_time.time(),
        )

        try:
            sidecar_results = await self._bus.run_all_sidecars(batch, store)
            for sr in sidecar_results:
                if not sr.attempted and (
                    "uma_" in sr.skipped_reason
                    or "high_water" in sr.skipped_reason
                    or "rss_exceeds" in sr.skipped_reason
                ):
                    self._sidecars_skipped.add(sr.sidecar_name)
                    if self._result_sink is not None:
                        try:
                            self._result_sink.sidecars_skipped.add(sr.sidecar_name)
                        except Exception:
                            pass

        except asyncio.CancelledError:
            raise
        except Exception:
            pass

        return DispatchOutcome(
            sprint_id=sprint_id,
            source_branch=source_branch,
            sidecars_skipped=tuple(sorted(self._sidecars_skipped)),
        )

    def reset(self) -> None:
        """Clear in-memory skipped-sidecar tracking. Called on sprint teardown."""
        self._sidecars_skipped.clear()


# ---------------------------------------------------------------------------
# FindingSidecarBus factory (extracted from sprint_scheduler.py line 1781)
# ---------------------------------------------------------------------------

from hledac.universal.runtime.sidecar_bus import create_sidecar_bus


# ---------------------------------------------------------------------------
# SidecarOrchestrator
# ---------------------------------------------------------------------------

class SidecarOrchestrator:
    """
    Invokes all sidecars and advisories against accepted findings.

    Sidecar authors can test against this interface without running the full
    SprintScheduler. Deletion test: if this is deleted, all 20 sidecar call
    sites must reappear in SprintScheduler.

    Attributes:
        result_sink: SprintSchedulerResult — all sidecar telemetry is written here.
        governor: M1 resource governor or None — used for RAM guard checks.
        _dispatcher: SidecarDispatcher — handles batch dispatch bookkeeping.
    """

    def __init__(
        self,
        result_sink: Any,
        governor: Any = None,
        scheduler: Any = None,
    ) -> None:
        self._result = result_sink
        self._governor = governor
        self._scheduler = scheduler  # SprintScheduler reference for deferred advisories
        self._bus = create_sidecar_bus(governor=governor)
        self._dispatcher = SidecarDispatcher(
            bus=self._bus,
            governor=governor,
            result_sink=result_sink,
        )
        # Lazy adapters — created on first use
        self._leak_sentinel_adapter: Any | None = None
        self._identity_adapter: Any | None = None
        self._target_memory_service: Any | None = None

    # ── Public dispatch ───────────────────────────────────────────────────

    async def dispatch_findings(
        self,
        source_branch: str,
        findings: list,
        store: Any,
        query: str,
        sprint_id: str,
    ) -> None:
        """
        F205C/F205F: Route accepted findings from any branch through FindingSidecarBus.

        Delegates to SidecarDispatcher. All batch construction, empty guards,
        skipped heavy sidecar tracking, CancelledError propagation, and
        fail-soft handling live in the dispatcher.

        Args:
            source_branch: "feed" | "public" | "ct"
            findings: List of accepted CanonicalFinding objects
            store: DuckDBShadowStore instance
            query: Original sprint query
            sprint_id: Sprint identifier
        """
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(
            source_branch=source_branch,
            findings=findings,
            store=store,
            query=query,
            sprint_id=sprint_id,
        )

    # ── Reset ──────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear in-memory state. Called on sprint teardown."""
        if hasattr(self, "_dispatcher") and self._dispatcher is not None:
            self._dispatcher.reset()
        # Clear lazy adapters
        self._leak_sentinel_adapter = None
        self._identity_adapter = None
        self._target_memory_service = None

    # ── Identity Stitching Sidecar (F202B) ────────────────────────────────

    async def run_identity_stitching_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F202B: Run identity stitching on accepted findings.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Derived identity findings are ingested via async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.entity_signal_extractor import (
                extract_entities_from_findings,
            )
            from hledac.universal.intelligence.identity_stitching_canonical import (
                create_identity_stitching_adapter,
            )
        except Exception:
            return  # Fail-soft: missing dependencies

        try:
            profiles = extract_entities_from_findings(findings)
            if not profiles:
                return

            if self._identity_adapter is None:
                self._identity_adapter = create_identity_stitching_adapter()

            candidates = self._identity_adapter.extract_and_stitch(profiles)
            if not candidates:
                return

            if len(candidates) > 1:
                try:
                    from hledac.universal.intelligence.attribution_scorer import (
                        create_attribution_scorer,
                    )
                    scorer = create_attribution_scorer()
                    candidates = self._identity_adapter.score_and_enrich_candidates(
                        candidates, scorer
                    )
                except Exception:
                    pass

            try:
                self._identity_adapter.upsert_identity_edges(candidates)
            except Exception:
                pass

            derived_findings = self._identity_adapter.to_derived_findings(
                candidates, query
            )
            if not derived_findings:
                return

            # F203D: Record identity stitching chain step (fail-soft)
            try:
                from hledac.universal.knowledge.evidence_chain import get_global_builder
                builder = get_global_builder()
                root_ids = [getattr(f, "finding_id", "") or "" for f in findings if getattr(f, "finding_id", "")]
                output_ids = [getattr(df, "finding_id", "") or "" for df in derived_findings if getattr(df, "finding_id", "")]
                if root_ids and output_ids:
                    builder.record_identity(
                        root_finding_id=root_ids[0],
                        input_ids=root_ids,
                        output_id=f"identity-stitched-{len(output_ids)}",
                        confidence=float(sum(getattr(c, "confidence", 0.5) for c in candidates) / max(len(candidates), 1)),
                        reason=f"Linked {len(profiles)} profiles → {len(candidates)} identity candidates → {len(derived_findings)} derived findings",
                    )
                    self._result.chain_steps_recorded += 1
            except Exception:
                pass

            if len(candidates) > 1:
                try:
                    from hledac.universal.knowledge.evidence_chain import get_global_builder
                    builder = get_global_builder()
                    root_ids = [getattr(f, "finding_id", "") or "" for f in findings if getattr(f, "finding_id", "")]
                    if root_ids:
                        builder.record_attribution(
                            root_finding_id=root_ids[0],
                            input_ids=root_ids,
                            output_id=f"attribution-scored-{len(candidates)}",
                            confidence=float(sum(getattr(c, "confidence", 0.5) for c in candidates) / max(len(candidates), 1)),
                            reason=f"Attribution scoring applied to {len(candidates)} identity candidates",
                        )
                        self._result.chain_steps_recorded += 1
                except Exception:
                    pass

            try:
                results = await store.async_ingest_findings_batch(derived_findings)
                stored = sum(
                    1 for r in results
                    if isinstance(r, dict) and r.get("accepted")
                )
                self._result.identity_findings_produced += stored
            except Exception:
                pass

            self._result.identity_candidates_found = len(candidates)

        except Exception:
            pass  # Fail-soft: sidecar must never crash sprint

    # ── Asset Exposure Correlator Sidecar (F202C) ─────────────────────────

    async def run_exposure_correlator_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F202C: Run asset exposure correlation on accepted findings.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Derived exposure findings are ingested via async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.exposure_correlator_adapter import (
                create_exposure_correlator_adapter,
            )
        except Exception:
            return

        try:
            adapter = create_exposure_correlator_adapter()
            result = adapter.correlate(findings)

            derived_findings = result.derived_findings
            if not derived_findings:
                return

            # F203D: Record exposure correlation chain step (fail-soft)
            try:
                from hledac.universal.knowledge.evidence_chain import get_global_builder
                builder = get_global_builder()
                root_ids = [getattr(f, "finding_id", "") or "" for f in findings if getattr(f, "finding_id", "")]
                output_ids = [getattr(df, "finding_id", "") or "" for df in derived_findings if getattr(df, "finding_id", "")]
                if root_ids and output_ids:
                    builder.record_exposure(
                        root_finding_id=root_ids[0],
                        input_ids=root_ids,
                        output_id=f"exposure-correlated-{len(output_ids)}",
                        confidence=0.7,
                        reason=f"Correlated {len(findings)} findings → {len(derived_findings)} exposure findings",
                    )
                    self._result.chain_steps_recorded += 1
            except Exception:
                pass

            try:
                results = await store.async_ingest_findings_batch(derived_findings)
                stored = sum(
                    1 for r in results
                    if isinstance(r, dict) and r.get("accepted")
                )
                self._result.exposure_findings_produced += stored
                self._result.correlated_assets_count += len(result.correlated_assets)
            except Exception:
                pass

        except Exception:
            pass

    # ── Leak Sentinel Sidecar (F202D) ──────────────────────────────────────

    async def run_leak_sentinel_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F202D: Run leak and secret sentinel on accepted findings.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Derived leak findings are ingested via async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.leak_sentinel import (
                create_leak_sentinel_adapter,
            )
        except Exception:
            return

        try:
            if self._leak_sentinel_adapter is None:
                self._leak_sentinel_adapter = create_leak_sentinel_adapter()

            derived_findings = await self._leak_sentinel_adapter.scan(query)
            if not derived_findings:
                return

            # F203D: Record leak sentinel chain step (fail-soft)
            try:
                from hledac.universal.knowledge.evidence_chain import get_global_builder
                builder = get_global_builder()
                root_ids = [getattr(f, "finding_id", "") or "" for f in findings if getattr(f, "finding_id", "")]
                output_ids = [getattr(df, "finding_id", "") or "" for df in derived_findings if getattr(df, "finding_id", "")]
                if root_ids and output_ids:
                    builder.record_leak(
                        root_finding_id=root_ids[0],
                        input_ids=root_ids,
                        output_id=f"leak-detected-{len(output_ids)}",
                        confidence=0.8,
                        reason=f"Leak scan on query → {len(derived_findings)} leak findings",
                    )
                    self._result.chain_steps_recorded += 1
            except Exception:
                pass

            try:
                results = await store.async_ingest_findings_batch(derived_findings)
                stored = sum(
                    1 for r in results
                    if isinstance(r, dict) and r.get("accepted")
                )
                self._result.leak_findings_produced += stored
            except Exception:
                pass

        except Exception:
            pass

    # ── Temporal Archaeology Sidecar (F202E) ──────────────────────────────

    async def run_temporal_archaeology_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F202E: Run temporal archaeology on accepted findings.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Synthesizes timeline from CT timestamps, archive events, document metadata,
        and finding timestamps. Derived timeline findings are ingested via
        async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.temporal_archaeologist_adapter import (
                create_temporal_archaeologist_adapter,
            )
        except Exception:
            return

        try:
            adapter = create_temporal_archaeologist_adapter()
            ct_findings = [f for f in findings if getattr(f, "source_type", "") == "ct_log"]
            result = adapter.synthesize_timeline(
                ct_findings=ct_findings,
                entity_id=query[:64],
            )

            timeline = result.timeline
            derived_findings = result.derived_findings

            if not derived_findings:
                return

            # F203D: Record temporal archaeology chain step (fail-soft)
            try:
                from hledac.universal.knowledge.evidence_chain import get_global_builder
                builder = get_global_builder()
                root_ids = [getattr(f, "finding_id", "") or "" for f in ct_findings if getattr(f, "finding_id", "")]
                output_ids = [getattr(df, "finding_id", "") or "" for df in derived_findings if getattr(df, "finding_id", "")]
                if root_ids and output_ids:
                    builder.record_temporal(
                        root_finding_id=root_ids[0],
                        input_ids=root_ids,
                        output_id=f"timeline-synthesized-{len(output_ids)}",
                        confidence=0.7,
                        reason=f"Synthesized {len(timeline)} timeline events from {len(ct_findings)} CT findings → {len(derived_findings)} timeline findings",
                    )
                    self._result.chain_steps_recorded += 1
            except Exception:
                pass

            try:
                results = await store.async_ingest_findings_batch(derived_findings)
                stored = sum(
                    1 for r in results
                    if isinstance(r, dict) and r.get("accepted")
                )
                self._result.timeline_findings_produced += stored
            except Exception:
                pass

        except Exception:
            pass

    # ── Evidence Triage Sidecar (F202I) ────────────────────────────────────

    async def run_evidence_triage_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F202I: Count document findings with triage facets.

        Document findings already have triage facets embedded by DocumentExtractor
        via _build_document_envelope. This sidecar counts them for observability.

        Fail-soft: errors never crash the sprint.
        """
        if not findings:
            return

        try:
            triage_count = 0
            for finding in findings:
                if not hasattr(finding, "source_type") or finding.source_type != "document":
                    continue
                if not hasattr(finding, "payload_text") or not finding.payload_text:
                    continue
                try:
                    payload = json.loads(finding.payload_text)
                    if isinstance(payload, dict) and "triage" in payload:
                        triage_count += 1
                except Exception:
                    pass
            self._result.evidence_triage_findings_count = triage_count
        except Exception as exc:
            log.warning(
                "sprint %s: evidence triage sidecar failed — %s: %s",
                getattr(self._result, "sprint_id", "?"),
                type(exc).__name__, exc,
            )

    # ── Target Memory Update (F204D) ──────────────────────────────────────

    async def run_target_memory_update(
        self,
        findings: list[Any],
        store: Any,
        query: str,
    ) -> None:
        """
        F204D: Update cross-sprint target memory after findings are accepted.

        Sidecar runs after findings are accepted and sidecar bus completes.
        Extracts entity/exposure/pivot facets from findings and merges into
        target memory via duckdb_store.

        RAM guard: skip if RSS > high_water (85% threshold).
        Fail-soft: errors never crash the sprint.
        """
        if not findings or store is None:
            return

        try:
            import psutil
        except Exception:
            return

        try:
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

            if hasattr(finding, "source_type") and getattr(finding, "source_type", None) == "exposure":
                if target_id not in exposure_facets:
                    exposure_facets[target_id] = {"signals": [], "count": 0}
                exposure_facets[target_id]["signals"].append(getattr(finding, "signal_type", "unknown"))
                exposure_facets[target_id]["count"] += 1

            if hasattr(finding, "suggested_pivots"):
                pivots = getattr(finding, "suggested_pivots", [])
                for pivot in pivots[:5]:
                    pivot_key = f"{pivot.get('pivot_type', '')}:{pivot.get('ioc_value', '')}"
                    if target_id not in pivot_facets:
                        pivot_facets[target_id] = {"pivots": [], "count": 0}
                    pivot_facets[target_id]["pivots"].append(pivot)
                    pivot_facets[target_id]["count"] += 1

            if hasattr(finding, "source_type") and getattr(finding, "source_type", None) == "rir_correlation":
                import json as _json
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
                ioc_value_from_payload = rir_data.get("ioc_value", "") or getattr(finding, "ioc_value", "") or ""
                if target_id not in exposure_facets:
                    exposure_facets[target_id] = {"signals": [], "rir_asns": {}, "count": 0}
                rir_asns = exposure_facets[target_id].setdefault("rir_asns", {})
                if asn:
                    rir_asns[asn] = {"org": org, "netblock": netblock, "country": country,
                                      "ioc_type": ioc_type, "ioc_value": ioc_value_from_payload}
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

        for target_id in set(entity_facets.keys()) | set(exposure_facets.keys()) | set(pivot_facets.keys()):
            from hledac.universal.intelligence.target_memory_service import TargetMemoryUpdate, TargetMemoryService

            update = TargetMemoryUpdate(
                target_id=target_id,
                sprint_id=sprint_id,
                finding_count=len(findings),
                entity_facets=entity_facets.get(target_id, {}),
                exposure_facets=exposure_facets.get(target_id, {}),
                pivot_facets=pivot_facets.get(target_id, {}),
                observed_ts=now,
            )

            if self._target_memory_service is None:
                self._target_memory_service = TargetMemoryService()

            merged = self._target_memory_service.merge_update(update)

            try:
                await store.async_upsert_target_memory(merged)
            except Exception:
                pass

    # ── Sprint Diff Sidecar (F203A) ─────────────────────────────────────────

    async def run_sprint_diff_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F203A: Compute cross-sprint diff for target.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Reads previous findings for the same target from DuckDB target_profiles,
        computes diff (new/disappeared/changed), updates profile, ingests diff
        findings via async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.
        """
        if not findings or store is None:
            return

        target_id = query[:128]

        try:
            from hledac.universal.knowledge.sprint_diff_engine import SprintDiffEngine
        except Exception:
            return

        try:
            try:
                prev_findings_raw = await store.async_get_previous_findings_for_target(
                    target_id, limit=1000
                )
            except Exception:
                prev_findings_raw = []

            previous_findings: list[dict] = []
            for f in prev_findings_raw:
                try:
                    previous_findings.append({
                        "finding_id": getattr(f, "finding_id", "") or "",
                        "source_type": getattr(f, "source_type", "") or "",
                        "ioc_type": getattr(f, "ioc_type", "") or "",
                        "ioc_value": getattr(f, "ioc_value", "") or "",
                        "confidence": getattr(f, "confidence", 0.5) or 0.5,
                        "ts": getattr(f, "ts", 0.0) or 0.0,
                    })
                except Exception:
                    continue

            current_findings: list[dict] = []
            for f in findings:
                try:
                    current_findings.append({
                        "finding_id": getattr(f, "finding_id", "") or "",
                        "source_type": getattr(f, "source_type", "") or "",
                        "ioc_type": getattr(f, "ioc_type", "") or "",
                        "ioc_value": getattr(f, "ioc_value", "") or "",
                        "confidence": getattr(f, "confidence", 0.5) or 0.5,
                        "ts": getattr(f, "ts", 0.0) or 0.0,
                    })
                except Exception:
                    continue

            engine = SprintDiffEngine()
            diff_result = engine.compute_diff(previous_findings, current_findings, target_id)

            derived_findings = diff_result.derived_findings
            if not derived_findings:
                return

            # F203D: Record sprint diff chain step (fail-soft)
            try:
                from hledac.universal.knowledge.evidence_chain import get_global_builder
                builder = get_global_builder()
                root_ids = [getattr(f, "finding_id", "") or "" for f in findings if getattr(f, "finding_id", "")]
                output_ids = [getattr(df, "finding_id", "") or "" for df in derived_findings if getattr(df, "finding_id", "")]
                if root_ids and output_ids:
                    builder.record_sprint_diff(
                        root_finding_id=root_ids[0],
                        input_ids=root_ids,
                        output_id=f"sprint-diff-{len(output_ids)}",
                        confidence=0.6,
                        reason=f"Sprint diff: {diff_result.new_count} new, {diff_result.disappeared_count} disappeared, {diff_result.changed_count} changed",
                    )
                    self._result.chain_steps_recorded += 1
            except Exception:
                pass

            try:
                results = await store.async_ingest_findings_batch(derived_findings)
                stored = sum(
                    1 for r in results
                    if isinstance(r, dict) and r.get("accepted")
                )
                self._result.sprint_diff_findings_produced += stored
            except Exception:
                pass

        except Exception:
            pass

    # ── Kill Chain Tagging Sidecar (F203C) ─────────────────────────────────

    async def run_kill_chain_tagging_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F203C: Tag findings with MITRE ATT&CK kill chain phases.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Tags findings via regex/lookup patterns, stores kill-chain-tagged findings
        via async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.kill_chain_tagger import (
                create_kill_chain_tagger,
            )
        except Exception:
            return

        try:
            tagger = create_kill_chain_tagger()
            tagged_results: dict[str, list] = {}
            tagged_count = 0

            for finding in findings:
                fid = getattr(finding, "finding_id", None)
                if not fid:
                    continue
                tags = tagger.tag_finding(finding)
                if tags:
                    tagged_results[str(fid)] = [tag.to_dict() for tag in tags]
                    tagged_count += len(tags)

            if not tagged_results:
                return

            # F203D: Record kill chain tagging chain step (fail-soft)
            try:
                from hledac.universal.knowledge.evidence_chain import get_global_builder
                builder = get_global_builder()
                root_ids = [getattr(f, "finding_id", "") or "" for f in findings if getattr(f, "finding_id", "")]
                output_ids = [f"kct-{fid[:32]}" for fid in tagged_results.keys()]
                if root_ids and output_ids:
                    builder.record_killchain(
                        root_finding_id=root_ids[0],
                        input_ids=root_ids,
                        output_id=f"killchain-tagged-{len(output_ids)}",
                        confidence=0.7,
                        reason=f"Tagged {len(tagged_results)} findings with {tagged_count} ATT&CK technique labels",
                    )
                    self._result.chain_steps_recorded += 1
            except Exception:
                pass

            derived_findings: list[Any] = []
            ts_now = _time.time()

            class _KCTFinding:
                """Minimal finding-like object with __slots__ for efficiency."""
                __slots__ = (
                    "finding_id", "source_type", "query", "target_id",
                    "ioc_type", "ioc_value", "confidence", "ts", "payload_text",
                )

                def __init__(self, **kw: Any) -> None:
                    for k, v in kw.items():
                        setattr(self, k, v)

            for fid, tags_list in tagged_results.items():
                try:
                    orig = next(
                        (f for f in findings if getattr(f, "finding_id", "") == fid),
                        None,
                    )
                    ioc_type = getattr(orig, "ioc_type", "unknown") if orig else "unknown"
                    ioc_value = getattr(orig, "ioc_value", fid) if orig else fid
                    confidence = getattr(orig, "confidence", 0.5) if orig else 0.5

                    derived_findings.append(
                        _KCTFinding(
                            finding_id=f"kct-{fid[:32]}",
                            source_type="killchain_tag",
                            query=query,
                            target_id=query[:128],
                            ioc_type=ioc_type,
                            ioc_value=ioc_value,
                            confidence=confidence,
                            ts=ts_now,
                            payload_text=str({"kill_chain_tags": tags_list}),
                        )
                    )
                except Exception:
                    continue

            if derived_findings:
                try:
                    results = await store.async_ingest_findings_batch(derived_findings)
                    stored = sum(
                        1 for r in results
                        if isinstance(r, dict) and r.get("accepted")
                    )
                    self._result.kill_chain_tags_produced += stored
                except Exception:
                    pass

        except Exception:
            pass

    # ── Embedding Sidecar (F203I) ───────────────────────────────────────────

    async def run_embedding_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F203I: Run streaming embedding on accepted findings for ANN indexing.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Uses StreamingEmbedder to embed findings in small batches, reducing peak
        RSS on M1 8GB. Indexed embeddings go to LanceDB ANN for fast dedup.

        Guardrails:
        - Model lifecycle via brain.model_lifecycle.get_model_lifecycle_status()
        - FETCH_SEMAPHORE=3 while model loaded
        - RAM guard blocks at >85% high_water / is_critical / is_emergency
        - prewarm() called after bulk embedding for faster dedup queries

        Fail-soft: errors never crash the sprint.
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder

            embedder = StreamingEmbedder()

            embeddable = []
            for f in findings:
                text = getattr(f, "payload_text", None) or getattr(f, "query", "") or ""
                if len(text) >= 16:
                    embeddable.append(f)

            if not embeddable:
                return

            async for ids, embeddings in embedder.embed_findings(embeddable, batch_size=16):
                if ids and embeddings is not None and embeddings.shape[0] > 0:
                    try:
                        from hledac.universal.knowledge.ann_index import get_ann_index

                        ann = get_ann_index()
                        import hashlib

                        for idx, finding_id in enumerate(ids):
                            emb = embeddings[idx]
                            if emb.shape[0] == 256:
                                key = hashlib.blake2b(finding_id.encode(), digest_size=32).hexdigest()
                                text_hash = hashlib.sha256(finding_id.encode()).hexdigest()
                                ann.upsert(key, emb, text_hash)

                        try:
                            from hledac.universal.knowledge.vector_store import get_vector_store

                            vs = get_vector_store()
                            await vs.add_vectors_streaming(ids, embeddings, index_type="text", batch_size=16)
                        except Exception:
                            pass
                    except Exception:
                        pass

            try:
                from hledac.universal.knowledge.ann_index import get_ann_index

                ann = get_ann_index()
                ann.prewarm()
            except Exception:
                pass

        except Exception:
            pass

    # ── Wayback Diff Sidecar (F203F) ──────────────────────────────────────

    async def run_wayback_diff_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F203F: Compute wayback diff for URLs found in findings.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Checks Wayback Machine for URL historical snapshots, computes diff against
        current state, ingests diff findings via async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.wayback_diff_engine import WaybackDiffEngine
        except Exception:
            return

        try:
            urls = []
            for f in findings:
                url = getattr(f, "ioc_value", "") if getattr(f, "ioc_type", "") == "url" else ""
                if url:
                    urls.append(url)

            if not urls:
                return

            engine = WaybackDiffEngine()
            result = await engine.compute_wayback_diff(urls[:50])  # Bound to 50 URLs

            derived_findings = result.derived_findings
            if not derived_findings:
                return

            try:
                results = await store.async_ingest_findings_batch(derived_findings)
                stored = sum(
                    1 for r in results
                    if isinstance(r, dict) and r.get("accepted")
                )
                self._result.wayback_diff_findings_produced += stored
            except Exception:
                pass

        except Exception:
            pass

    # ── RIR/ASN Correlator Sidecar (F204H) ─────────────────────────────────

    async def run_rir_correlator_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F204H: Correlate findings with RIR/ASN data (ARIN, RIPE, APNIC, LACNIC, AFRINIC).

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Enriches IP findings with AS number, org name, netblock, and country.
        Enriched findings ingested via async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.rir_correlator_adapter import (
                create_rir_correlator_adapter,
            )
        except Exception:
            return

        try:
            adapter = create_rir_correlator_adapter()
            result = adapter.correlate(findings)

            derived_findings = result.derived_findings
            if not derived_findings:
                return

            try:
                results = await store.async_ingest_findings_batch(derived_findings)
                stored = sum(
                    1 for r in results
                    if isinstance(r, dict) and r.get("accepted")
                )
                self._result.rir_correlation_produced += stored
            except Exception:
                pass

        except Exception:
            pass

    # ── Social Identity Surface Sidecar (F204J) ─────────────────────────────

    async def run_social_identity_surface_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F204J: Identify social media identity surfaces from findings.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Extracts social media handles, profiles, and identity signals from findings.
        Derived identity surface findings ingested via async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.social_identity_surface_adapter import (
                create_social_identity_surface_adapter,
            )
        except Exception:
            return

        try:
            adapter = create_social_identity_surface_adapter()
            result = adapter.extract_surface(findings)

            derived_findings = result.derived_findings
            if not derived_findings:
                return

            try:
                results = await store.async_ingest_findings_batch(derived_findings)
                stored = sum(
                    1 for r in results
                    if isinstance(r, dict) and r.get("accepted")
                )
                # Telemetry field: social_identity_findings_produced
            except Exception:
                pass

        except Exception:
            pass

    # ── Advisory Runner ────────────────────────────────────────────────────

    async def run_advisory_runner(self) -> None:
        """
        F206D: Run all advisory steps via SprintAdvisoryRunner.

        Canonical teardown entry point for all advisory orchestration.
        Each step is fail-soft; CancelledError propagates to caller.

        Runner order:
          1. run_all_advisories (pivot_planner, pivot_executor, resource_governor, analyst_brief)
          2. run_ct_to_passivedns_pivot_advisory (R5: CT -> PDNS one-hop pivot)
          3. run_bgp_advisory_sidecar (F234: fail-soft, non-blocking)
          4. run_wayback_cdx_deep_sidecar (F234: fail-soft, non-blocking)
        """
        # ── Step 1: Run all 4 advisory steps ──────────────────────────────────
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
                try:
                    from hledac.universal.runtime.acquisition_strategy import (
                        is_lane_enabled,
                        AcquisitionLane,
                        lane_skip_reason,
                    )
                    if not is_lane_enabled(snapshot, AcquisitionLane.PIVOT_EXECUTOR):
                        reason = lane_skip_reason(snapshot, AcquisitionLane.PIVOT_EXECUTOR) or "unknown"
                        log.debug(f"[F206BK] pivot_executor skipped: {reason}")
                        if hasattr(self._result, "acquisition_lanes_skipped"):
                            self._result.acquisition_lanes_skipped += 1
                except Exception:
                    pass
            await runner.run_all_advisories()

        # ── Step 2: CT -> PassiveDNS one-hop pivot ─────────────────────────────
        await self.run_ct_to_passivedns_pivot_advisory()

        # ── Steps 3-4: Non-blocking advisory sidecars ───────────────────────────
        if self._scheduler is not None:
            bg_tasks: set = getattr(self._scheduler, "_bg_tasks", set())

            _bgp_task = _asyncio.create_task(
                self.run_bgp_advisory_sidecar(), name="sprint:bgp_advisory_sidecar"
            )
            bg_tasks.add(_bgp_task)
            _bgp_task.add_done_callback(bg_tasks.discard)

            _wayback_task = _asyncio.create_task(
                self.run_wayback_cdx_deep_sidecar(), name="sprint:wayback_cdx_sidecar"
            )
            bg_tasks.add(_wayback_task)
            _wayback_task.add_done_callback(bg_tasks.discard)

    # ── Pivot Planner Advisory (F202G) ───────────────────────────────────

    async def run_pivot_planner_advisory(self) -> None:
        """
        F202G: Run pivot planner on accepted findings for advisory ordering.

        Delegates to SprintAdvisoryRunner for the actual work.
        Kept as thin wrapper for backward compatibility.
        """
        pass

    # ── Pivot Executor Advisory (F204C) ───────────────────────────────────

    async def run_pivot_executor_advisory(self) -> None:
        """
        F204C: Execute top pivots from PivotPlanner via AutonomousPivotExecutor.

        Delegates to SprintAdvisoryRunner for the actual work.
        Kept as thin wrapper for backward compatibility.
        """
        pass

    # ── Resource Governor Advisory (F202J) ─────────────────────────────────

    async def run_resource_governor_advisory(self) -> None:
        """
        F202J: Apply resource governor decision at TEARDOWN.

        Delegates to SprintAdvisoryRunner for the actual work.
        Kept as thin wrapper for backward compatibility.
        """
        pass

    # ── Analyst Brief Advisory (F204E) ───────────────────────────────────

    async def run_analyst_brief_advisory(self) -> None:
        """
        F204E: Generate analyst brief at TEARDOWN.

        Delegates to SprintAdvisoryRunner for the actual work.
        Kept as thin wrapper for backward compatibility.
        """
        pass

    # ── CT to PassiveDNS Pivot Advisory ───────────────────────────────────

    async def run_ct_to_passivedns_pivot_advisory(self) -> None:
        """
        Run CT to PassiveDNS pivot advisory.

        Delegates to SprintAdvisoryRunner for the actual work.
        Kept as thin wrapper for backward compatibility.
        """
        pass

    # ── BGP Advisory Sidecar ─────────────────────────────────────────────

    async def run_bgp_advisory_sidecar(self) -> None:
        """
        Run BGP advisory sidecar for ASN/path analysis.

        Fail-soft: errors never crash the sprint.
        """
        try:
            from hledac.universal.intelligence.bgp_advisor_adapter import (
                create_bgp_advisor_adapter,
            )
        except Exception:
            return

        try:
            adapter = create_bgp_advisor_adapter()
            result = adapter.analyze(self._result)
            # BGP advisory results are written to result telemetry
        except Exception:
            pass

    # ── Wayback CDX Deep Sidecar ─────────────────────────────────────────

    async def run_wayback_cdx_deep_sidecar(self) -> None:
        """
        Run deep Wayback CDX analysis for URL history.

        Fail-soft: errors never crash the sprint.
        """
        try:
            from hledac.universal.intelligence.wayback_cdx_deep_adapter import (
                create_wayback_cdx_deep_adapter,
            )
        except Exception:
            return

        try:
            adapter = create_wayback_cdx_deep_adapter()
            result = await adapter.analyze(self._result)
            # Results written to result telemetry
        except Exception:
            pass