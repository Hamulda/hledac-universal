"""
runtime/sprint_advisory_runner.py — F206D: Extracted Advisory Runner
====================================================================



Refactored from sprint_scheduler.py F202G/F204C/F202J/F204E. Holds all
teardown advisory orchestration:

- _run_pivot_planner_advisory   (F202G)
- _run_pivot_executor_advisory   (F204C)
- _run_resource_governor_advisory (F202J)
- _run_analyst_brief_advisory   (F204E)

Canonical write path remains via existing seams (duckdb_store, governor, etc.).
No new persistent write paths introduced.

Runner order is explicit and tested:
  1. pivot_planner  → produces planned_pivots
  2. pivot_executor → consumes planned_pivots, produces executed_pivots
  3. resource_governor → produces governor_recorded
  4. analyst_brief → produces brief_generated

Each step is fail-soft; CancelledError is re-raised.

GHOST_INVARIANTS:
- asyncio.gather always with return_exceptions=True
- _check_gathered() after every gather
- CancelledError re-raised, never swallowed
- No blocking calls in async context
- Canonical write path only via existing seams (duckdb_store, governor)
- Model lifecycle via brain.model_lifecycle only
- RAM guard: skip heavy ops when RSS > high_water
- Fail-soft: advisory error never stops sprint
"""
from __future__ import annotations
import asyncio
import logging
import msgspec
from compat.msgspec_gc_compat import Struct
import msgspec.json as _json
from functools import lru_cache
from typing import Any
__all__ = ['SprintAdvisoryRunner', 'AdvisoryRunOutcome', 'build_search_documents_from_findings']
log = logging.getLogger(__name__)
try:
    from hledac.universal.utils.source_types import SourceType
except ImportError:
    SourceType = None
from hledac.universal._core.env_config import ENV
from _core import aclose
MAX_PIVOTS: int = 20
_ADVISORY_PARALLEL_SEMAPHORE_LIMIT: int = 4

def _merge_outcomes(base: AdvisoryRunOutcome, other: AdvisoryRunOutcome) -> AdvisoryRunOutcome:
    """
    Merge two AdvisoryRunOutcome objects, taking the last-seen value for each field.

    Used when parallel advisory steps return partial outcomes that need to be
    combined into a single coherent result. Non-count fields (bool/str) use
    the value from `other` if it differs from the base default.
    """
    return AdvisoryRunOutcome(planned_pivots=base.planned_pivots or other.planned_pivots, executed_pivots=base.executed_pivots or other.executed_pivots, governor_recorded=base.governor_recorded or other.governor_recorded, brief_generated=base.brief_generated or other.brief_generated, local_search_attempted=base.local_search_attempted or other.local_search_attempted, local_search_hits=base.local_search_hits or other.local_search_hits, local_search_source=base.local_search_source if base.local_search_source != 'none' else other.local_search_source, local_search_indexed=base.local_search_indexed or other.local_search_indexed, local_search_elapsed_ms=base.local_search_elapsed_ms or other.local_search_elapsed_ms, local_search_top_results=base.local_search_top_results or other.local_search_top_results, local_search_error=base.local_search_error or other.local_search_error, federated_attempted=base.federated_attempted or other.federated_attempted, federated_nodes=base.federated_nodes or other.federated_nodes, federated_findings=base.federated_findings or other.federated_findings, federated_bridge_updates=base.federated_bridge_updates or other.federated_bridge_updates, federated_bridge_persists=base.federated_bridge_persists or other.federated_bridge_persists, federated_mode=base.federated_mode if base.federated_mode != 'none' else other.federated_mode, federated_elapsed_ms=base.federated_elapsed_ms or other.federated_elapsed_ms, federated_error=base.federated_error or other.federated_error, error=base.error or other.error)

def build_search_documents_from_findings(findings: list) -> list:
    """
    F228C: Convert CanonicalFinding objects to SearchDocument records.

    Advisory-only, no canonical writes. Skips findings without payload_text.
    Deduplicates by url to avoid metadata explosion.
    Bounds result to MAX_INDEXED_FINDINGS.

    Args:
        findings: List of CanonicalFinding objects (or dict-like with
                  finding_id, source_type, payload_text attrs).

    Returns:
        list[SearchDocument] suitable for LocalSearchSeam.index().
    """
    from hledac.universal.knowledge.search_index import SearchDocument
    MAX_INDEXED_FINDINGS = 5000
    seen_urls: set[str] = set()
    docs: list = []
    for f in findings:
        if len(docs) >= MAX_INDEXED_FINDINGS:
            break
        try:
            payload = getattr(f, 'payload_text', '') or (f.get('payload_text', '') if isinstance(f, dict) else '')
            source_type = getattr(f, 'source_type', 'unknown') or (f.get('source_type', 'unknown') if isinstance(f, dict) else 'unknown')
            finding_id = getattr(f, 'finding_id', '?') or (f.get('finding_id', '?') if isinstance(f, dict) else '?')
            url = getattr(f, 'url', '') or (f.get('url', '') if isinstance(f, dict) else '')
        except Exception:
            continue
        if not payload:
            continue
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        title = payload[:80].strip()
        doc = SearchDocument(url=url or f'finding://{finding_id}', title=title, content=payload, metadata={'finding_id': finding_id, 'source_type': source_type})
        docs.append(doc)
    return docs

class AdvisoryRunOutcome(Struct, frozen=True):
    """
    Result of a full advisory run (all 6 advisory steps).

    Fields:
        planned_pivots: Number of pivots planned (0 if planner skipped/failed)
        executed_pivots: Number of pivots executed (0 if executor skipped/failed)
        governor_recorded: True if governor evaluate+apply succeeded
        brief_generated: True if analyst brief was generated
        local_search_attempted: True if local search seam was queried
        local_search_hits: Number of top results returned
        local_search_source: "search_index" or "none"
        local_search_indexed: Number of findings indexed
        local_search_elapsed_ms: Wall time of index+search
        local_search_top_results: list[dict] with url/title/score/source_type/finding_id
        local_search_error: Error string if failed, else None
        federated_attempted: True if federated bridge was queried
        federated_nodes: Virtual nodes used in distributed run (0 if skipped)
        federated_findings: Findings emitted from federated distribute_research
        federated_bridge_updates: Bridge.update() calls during this advisory
        federated_bridge_persists: Bridge.persist_if_due() writes during this advisory
        federated_mode: Bridge mode (lightweight_only/lazy_hybrid/cross_sprint_persist)
        federated_elapsed_ms: Wall time of the federated advisory
        federated_error: Error string if failed, else None
        error: Error message if any step failed, else None
    """
    planned_pivots: int = 0
    executed_pivots: int = 0
    governor_recorded: bool = False
    brief_generated: bool = False
    local_search_attempted: bool = False
    local_search_hits: int = 0
    local_search_source: str = 'none'
    local_search_indexed: int = 0
    local_search_elapsed_ms: float = 0.0
    local_search_top_results: list[str] = []
    local_search_error: str | None = None
    federated_attempted: bool = False
    federated_nodes: int = 0
    federated_findings: int = 0
    federated_bridge_updates: int = 0
    federated_bridge_persists: int = 0
    federated_mode: str = 'none'
    federated_elapsed_ms: float = 0.0
    federated_error: str | None = None
    error: str | None = None

class SprintAdvisoryRunner:
    """
    F206D: Extracted advisory orchestration for sprint teardown.

    Runs the 4 advisory steps in explicit order:
      1. pivot_planner  → planned_pivots
      2. pivot_executor → executed_pivots (consumes planner output)
      3. resource_governor → governor_recorded
      4. analyst_brief → brief_generated

    Each step is fail-soft. CancelledError propagates to caller.
    Scheduler retains all authority; runner is purely orchestration.

    Args:
        scheduler: SprintScheduler instance providing access to:
            - _pivot_planner
            - _duckdb_store
            - _governor
            - _analyst_workbench
            - _all_findings
            - sprint_id
            - query
            - _sidecars_skipped
            - _peak_rss_gib
            - _result
        duckdb_store: DuckDBShadowStore (passed explicitly for clarity)
        governor: M1ResourceGovernor instance
        analyst_workbench: AnalystWorkbench instance (may be None)
    """
    __slots__ = tuple(('_analyst_workbench', '_duckdb_store', '_governor', '_scheduler'))

    def __init__(self, scheduler: Any, duckdb_store: Any=None, governor: Any=None, analyst_workbench: Any=None) -> None:
        self._scheduler = scheduler
        self._duckdb_store = duckdb_store
        self._governor = governor
        self._analyst_workbench = analyst_workbench

    async def run_all_advisories(self) -> AdvisoryRunOutcome:
        """
        Run all 6 advisory steps with parallelization where safe.

        Order (mandatory sequential):
          1. pivot_planner  → planned_pivots
          2. pivot_executor → executed_pivots  [depends on 1]

        Steps 3-6 run in PARALLEL (bounded semaphore, M1 8GB safe):
          3. resource_governor → governor_recorded
          4. analyst_brief → brief_generated
          5. local_search → local_search_*
          6. federated_research → federated_* (F350M-FED-P3-FOLLOWUP)

        Parallel execution via parallel_ok with _ADVISORY_PARALLEL_SEMAPHORE_LIMIT=4.
        Each step is fail-soft; exceptions are collected and merged into outcome.error.

        CancelledError: re-raised to caller.
        Fail-soft: any step failure returns partial outcome with error message.

        Returns:
            AdvisoryRunOutcome with counts/flags for each step.
        """
        try:
            from hledac.universal.utils.asyncx import parallel_ok
        except ImportError:
            parallel_ok = None
        outcome = AdvisoryRunOutcome()
        try:
            outcome = await self._run_pivot_planner_advisory(outcome)
            outcome = await self._run_pivot_executor_advisory(outcome)
            if parallel_ok is not None:
                from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
                sem = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)

                async def bounded_step(coro, step_name: str):
                    """Run a step with semaphore-bounded concurrency."""
                    async with sem:
                        try:
                            return await coro
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            log.debug('[P2-4] advisory step %s failed (fail-soft): %s', step_name, e)
                            return AdvisoryRunOutcome(error=str(e))
                parallel_results = await parallel_ok(bounded_step(self._run_resource_governor_advisory(outcome), 'resource_governor'), bounded_step(self._run_analyst_brief_advisory(outcome), 'analyst_brief'), bounded_step(self._run_local_search_advisory(outcome), 'local_search'), bounded_step(self._run_federated_research_advisory(outcome), 'federated_research'), label='advisory_parallel:3-6')
                for r in parallel_results:
                    if isinstance(r, AdvisoryRunOutcome):
                        outcome = _merge_outcomes(outcome, r)
            else:
                outcome = await self._run_resource_governor_advisory(outcome)
                outcome = await self._run_analyst_brief_advisory(outcome)
                outcome = await self._run_local_search_advisory(outcome)
                outcome = await self._run_federated_research_advisory(outcome)
        except asyncio.CancelledError:
            raise
        return outcome

    async def _run_pivot_planner_advisory(self, outcome: AdvisoryRunOutcome) -> AdvisoryRunOutcome:
        """
        F202G: Run pivot planner on accepted findings for advisory ordering.

        Planner generates pivot suggestions; scheduler may use them as
        ordering input for future sprints. Advisory only.

        Fail-soft: errors never crash the runner.
        """
        planner = getattr(self._scheduler, '_pivot_planner', None)
        if planner is None:
            return outcome
        findings = getattr(self._scheduler, '_all_findings', [])
        if not findings:
            return outcome
        try:
            graph_stats = await self._collect_graph_stats()
            feedback_summary = await self._collect_feedback_summary()
            hermes_outputs = await self._collect_hermes_outputs()
            pivots = self._compute_pivots(planner, findings, hermes_outputs, graph_stats, feedback_summary)
            self._scheduler._planned_pivots = pivots
            log.debug(f'[F202G] Planned {len(pivots)} pivots from {len(findings)} findings')
            return AdvisoryRunOutcome(planned_pivots=len(pivots), executed_pivots=outcome.executed_pivots, governor_recorded=outcome.governor_recorded, brief_generated=outcome.brief_generated, local_search_attempted=outcome.local_search_attempted, local_search_hits=outcome.local_search_hits, local_search_source=outcome.local_search_source, local_search_indexed=outcome.local_search_indexed, local_search_elapsed_ms=outcome.local_search_elapsed_ms, local_search_top_results=outcome.local_search_top_results, local_search_error=outcome.local_search_error, error=None)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass
        return outcome

    async def _collect_graph_stats(self) -> dict[str, Any]:
        """Collect graph statistics for pivot planning."""
        try:
            from hledac.universal.knowledge import graph_service
            stats = graph_service.graph_stats()
            if not stats:
                return {}
            node_degrees, domains = {}, []
            if ENV.get_bool('HLEDAC_ENABLE_GRAPH_ANALYSIS'):
                try:
                    summary = graph_service.graph_analytics_summary(top_k=500)
                    if summary.get('analytics_available'):
                        for entity in summary.get('top_central_entities', [])[:500]:
                            val, deg = entity.get('value', ''), entity.get('degree', 0)
                            if val and deg > 0:
                                domains.append(val)
                                node_degrees[val] = deg
                except Exception:  # noqa: BLE001
                    pass
            return {'nodes': stats.get('nodes', 0), 'edges': stats.get('edges', 0), 'domains': domains, 'connected_iocs': set(), 'node_degrees': node_degrees}
        except Exception:  # noqa: BLE001
            return {}

    async def _collect_feedback_summary(self) -> Any:
        """Collect hypothesis feedback summary."""
        store = self._duckdb_store
        if store is None:
            return None
        try:
            from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackAdapter
            adapter = HypothesisFeedbackAdapter(duckdb_store=store, target_id=getattr(self._scheduler, 'sprint_id', '') or 'default')
            return await adapter.async_get_summary()
        except Exception:
            return None

    async def _collect_hermes_outputs(self) -> list:
        """Collect Hermes inference outputs from findings."""
        store = self._duckdb_store
        if store is None:
            return []
        try:
            from hledac.universal.runtime.hermes_pivot_contract import HermesInferenceOutput
            rows = await store._conn.execute('SELECT payload_text FROM findings WHERE source_type = ? AND query = ? LIMIT 50', [SourceType.HERMES_INFERENCE.value, getattr(self._scheduler, '_query', '') or ''])
            outputs = []
            for row in rows:
                try:
                    import orjson
                    payload = orjson.loads(row[0])
                    outputs.append(HermesInferenceOutput.from_dict(payload))
                except Exception:  # noqa: BLE001
                    pass
            return outputs
        except Exception:
            return []

    def _compute_pivots(self, planner: Any, findings: list, hermes_outputs: list, graph_stats: dict, feedback_summary: Any) -> list:
        """Compute pivots using planner with Hermes outputs or fallback."""
        if hermes_outputs:
            return planner.score_with_hermes_output(findings, hermes_outputs, graph_stats=graph_stats)
        return planner.plan_pivots(findings, graph_stats=graph_stats, feedback_summary=feedback_summary)

    async def _run_pivot_executor_advisory(self, outcome: AdvisoryRunOutcome) -> AdvisoryRunOutcome:
        """
        F204C: Execute top pivots from PivotPlanner via AutonomousPivotExecutor.

        Bounded advisory: executor stores derived findings via canonical ingest
        and records HypothesisFeedback. Scheduler retains all authority.

        Fail-soft: errors never crash the runner.
        """
        pivots = getattr(self._scheduler, '_planned_pivots', None)
        if not pivots:
            return outcome
        store = self._duckdb_store
        if store is None:
            return outcome
        try:
            from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackAdapter
            from hledac.universal.runtime.pivot_executor import AutonomousPivotExecutor
            feedback_adapter = HypothesisFeedbackAdapter(duckdb_store=store, target_id=getattr(self._scheduler, 'sprint_id', '') or 'default')
            executor = AutonomousPivotExecutor(duckdb_store=store, resource_governor=self._governor, feedback_adapter=feedback_adapter)
            results = await executor.execute_top(pivots, [])
            self._scheduler._pivot_execution_results = results
            log.debug(f'[F204C] Executed {len(results)} pivots')
            return AdvisoryRunOutcome(planned_pivots=outcome.planned_pivots, executed_pivots=len(results), governor_recorded=outcome.governor_recorded, brief_generated=outcome.brief_generated, local_search_attempted=outcome.local_search_attempted, local_search_hits=outcome.local_search_hits, local_search_source=outcome.local_search_source, local_search_indexed=outcome.local_search_indexed, local_search_elapsed_ms=outcome.local_search_elapsed_ms, local_search_top_results=outcome.local_search_top_results, local_search_error=outcome.local_search_error, error=None)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass
        return outcome

    async def _run_resource_governor_advisory(self, outcome: AdvisoryRunOutcome) -> AdvisoryRunOutcome:
        """
        F202J: Apply resource governor decision at TEARDOWN.

        Advisory only: governor evaluates and applies concurrency hints.
        Sprint retains all authority.

        F204J: Also tracks peak RSS and sidecars skipped for budget scorecard.

        Fail-soft: errors never crash the runner.
        """
        governor = self._governor
        if governor is None:
            return outcome
        governor_recorded = False
        try:
            decision = await governor.evaluate()
            governor_recorded = True
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass
        try:
            from hledac.universal._core.resource_governor import sample_uma_status
            from hledac.universal.runtime.resource_governor import MISSION_PEAK_RSS_GIB
            uma = sample_uma_status()
            if uma.system_used_gib > 0:
                rss_gib = uma.system_used_gib / 1024 ** 3
                peak_rss = getattr(self._scheduler, '_peak_rss_gib', 0.0)
                if rss_gib > peak_rss:
                    self._scheduler._peak_rss_gib = rss_gib
                if rss_gib > MISSION_PEAK_RSS_GIB:
                    result = getattr(self._scheduler, '_result', None)
                    if result is not None:
                        result.budget_violations += 1
        except Exception:  # noqa: BLE001
            pass
        orchestrator = getattr(self._scheduler, '_sidecar_orchestrator', None)
        if orchestrator is not None:
            sidecars_skipped = getattr(orchestrator._dispatcher, '_sidecars_skipped', set())
        else:
            sidecars_skipped = set()
        peak_rss_gib = getattr(self._scheduler, '_peak_rss_gib', 0.0)
        result = getattr(self._scheduler, '_result', None)
        if result is not None:
            result.sidecars_skipped = tuple(sorted(sidecars_skipped))
            result.peak_rss_gib = peak_rss_gib
        if decision is not None and result is not None:
            result.governor_uma_state = getattr(decision, 'uma_state', '')
            result.governor_system_used_gib = getattr(decision, 'system_used_gib', 0.0)
            result.governor_swap_detected = getattr(decision, 'swap_detected', False)
            result.governor_io_only = getattr(decision, 'io_only', False)
        return AdvisoryRunOutcome(planned_pivots=outcome.planned_pivots, executed_pivots=outcome.executed_pivots, governor_recorded=governor_recorded, brief_generated=outcome.brief_generated, local_search_attempted=outcome.local_search_attempted, local_search_hits=outcome.local_search_hits, local_search_source=outcome.local_search_source, local_search_indexed=outcome.local_search_indexed, local_search_elapsed_ms=outcome.local_search_elapsed_ms, local_search_top_results=outcome.local_search_top_results, local_search_error=outcome.local_search_error, error=None)

    async def _run_local_search_advisory(self, outcome: AdvisoryRunOutcome) -> AdvisoryRunOutcome:
        """
        F228C: Local search advisory at teardown.

        Indexes accepted findings into LocalSearchSeam (advisory-only, no
        canonical writes, no persistent DB). Then searches them with the
        sprint query to surface relevant evidence for research context.

        Bounded, fail-soft, no network, no model load.

        Telemetry fields in AdvisoryRunOutcome:
            local_search_attempted: True if seam was queried
            local_search_hits: Number of top results returned
            local_search_indexed: Number of findings indexed
            local_search_source: "search_index" or "none"
            local_search_elapsed_ms: Wall time of index+search
            local_search_top_results: list[dict] with url/title/score/source_type/finding_id
            local_search_error: Error string if failed, else None
        """
        from time import perf_counter
        t0 = perf_counter()
        try:
            from hledac.universal.knowledge.search_index import LocalSearchSeam
            seam = LocalSearchSeam()
            findings = getattr(self._scheduler, '_all_findings', []) or []
            docs = build_search_documents_from_findings(findings)
            indexed_count = seam.index(docs)
            query = getattr(self._scheduler, 'query', None) or ''
            if not query:
                elapsed = (perf_counter() - t0) * 1000
                return AdvisoryRunOutcome(planned_pivots=outcome.planned_pivots, executed_pivots=outcome.executed_pivots, governor_recorded=outcome.governor_recorded, brief_generated=outcome.brief_generated, local_search_attempted=True, local_search_hits=0, local_search_indexed=indexed_count, local_search_source='search_index', local_search_elapsed_ms=elapsed, local_search_top_results=[], local_search_error=None, error=None)
            result = seam.search(query, top_k=10)
            hits = len(result.results)
            top_results = []
            for doc in result.results:
                top_results.append({'url': doc.url, 'title': doc.title, 'score': doc.score, 'source_type': doc.metadata.get('source_type', 'unknown'), 'finding_id': doc.metadata.get('finding_id', '')})
            elapsed = (perf_counter() - t0) * 1000
            return AdvisoryRunOutcome(planned_pivots=outcome.planned_pivots, executed_pivots=outcome.executed_pivots, governor_recorded=outcome.governor_recorded, brief_generated=outcome.brief_generated, local_search_attempted=True, local_search_hits=hits, local_search_indexed=indexed_count, local_search_source='search_index', local_search_elapsed_ms=elapsed, local_search_top_results=top_results, local_search_error=None, error=None)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed = (perf_counter() - t0) * 1000
            return AdvisoryRunOutcome(planned_pivots=outcome.planned_pivots, executed_pivots=outcome.executed_pivots, governor_recorded=outcome.governor_recorded, brief_generated=outcome.brief_generated, local_search_attempted=True, local_search_hits=0, local_search_indexed=0, local_search_source='local_search', local_search_elapsed_ms=elapsed, local_search_top_results=[], local_search_error=str(e), error=None)

    async def _run_analyst_brief_advisory(self, outcome: AdvisoryRunOutcome) -> AdvisoryRunOutcome:
        """
        F204E/F205J: Generate analyst brief at TEARDOWN.

        Uses canonical target_id (query or duckdb_store lookup) instead of
        sprint_id, enabling cross-sprint target memory reads.

        Advisory only: brief summarizes sprint results but does not affect
        sprint execution or outcomes. Sprint retains all authority.

        Fail-soft: errors never crash the runner.

        Stores brief in scheduler._analyst_brief for export hookup.
        """
        workbench = getattr(self._scheduler, '_analyst_workbench', None)
        duckdb_store = self._duckdb_store
        if workbench is None and duckdb_store is not None:
            try:
                from hledac.universal.knowledge.analyst_workbench import AnalystWorkbench
                workbench = AnalystWorkbench(duckdb_store=duckdb_store)
            except Exception:
                workbench = None
        if workbench is None:
            return outcome
        try:
            findings = getattr(self._scheduler, '_all_findings', [])
            if findings is None:
                findings = []
            graph_signal = self._scheduler._get_graph_signal()
            governor = self._governor
            scheduler_sprint_id = getattr(self._scheduler, 'sprint_id', None)
            if scheduler_sprint_id is not None and scheduler_sprint_id != '':
                sprint_id = scheduler_sprint_id
            elif scheduler_sprint_id == '':
                sprint_id = 'unknown'
            else:
                sprint_id = 'unknown'
            query = getattr(self._scheduler, 'query', None) or ''
            target_id = query if query else sprint_id
            if not target_id:
                target_id = sprint_id
            brief = await workbench.build_sprint_brief(sprint_id=sprint_id, target_id=target_id, findings=findings, graph_signal=graph_signal, governor=governor, duckdb_store=duckdb_store, store_findings_count=None)
            self._scheduler._analyst_brief = brief
            log.debug(f'[F204E] Analyst brief generated: {brief.headline}')
            return AdvisoryRunOutcome(planned_pivots=outcome.planned_pivots, executed_pivots=outcome.executed_pivots, governor_recorded=outcome.governor_recorded, brief_generated=True, local_search_attempted=outcome.local_search_attempted, local_search_hits=outcome.local_search_hits, local_search_source=outcome.local_search_source, local_search_indexed=outcome.local_search_indexed, local_search_elapsed_ms=outcome.local_search_elapsed_ms, local_search_top_results=outcome.local_search_top_results, local_search_error=outcome.local_search_error, error=None)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass
        return outcome

    async def _run_federated_research_advisory(self, outcome: AdvisoryRunOutcome) -> AdvisoryRunOutcome:
        """
        F350M-FED-P3-FOLLOWUP: Federated research advisory at teardown.

        The canonical seam for the federated research capability at sprint
        teardown. Performs four bounded, fail-soft actions:

        1. **Lazy bridge creation** — `scheduler._ensure_federated_bridge()`
           returns a long-lived `FederatedBridge` (singleton on scheduler).
           Off by default (gated on HLEDAC_ENABLE_FEDERATED=1).
        2. **M1 safety** — skip entirely if memory_pressure > 0.85.
        3. **Bridge updates** — for each accepted finding in
           `scheduler._all_findings`, emit `bridge.update(lane, state, action, reward, next_state)`.
           Reward = clamp01(confidence). State = (lane, len(findings)).
           Bounded by len(findings) — typically < 100.
        4. **LMDB persistence** — call `bridge.persist_if_due()` (debounced,
           `asyncio.to_thread`, fail-soft). Honors env-var
           `HLEDAC_FEDERATED_LMDB_PATH` for cross-sprint state.

        Complements (does NOT replace) the Phase 2 plugin sidecar:
        - Plugin sidecar: fire-and-forget, runs FederatedResearchCoordinator,
          produces CanonicalFinding objects → SidecarDispatcher.
        - This advisory: bounded bridge updates + LMDB persistence +
          telemetry → analytics/export.

        Side effects (all fail-soft):
        - Sets `scheduler._federated_bridge` to the long-lived instance.
        - Updates `SprintSchedulerResult.federated_*` telemetry fields
          (populated by `sprint_scheduler._apply_federated_outcome`).

        CancelledError: re-raised to caller.
        All other exceptions: caught, logged at debug, outcome returned.
        """
        from time import perf_counter
        t0 = perf_counter()
        try:
            bridge_factory = getattr(self._scheduler, '_ensure_federated_bridge', None)
            if bridge_factory is None:
                return outcome
            bridge = bridge_factory()
            if bridge is None:
                return outcome
            memory_pressure = 0.0
            try:
                if self._governor is not None:
                    snap = getattr(self._governor, 'snapshot', None)
                    if snap is not None:
                        memory_pressure = float(getattr(snap, 'memory_pressure', 0.0) or 0.0)
            except Exception:
                memory_pressure = 0.0
            if memory_pressure > FEDERATED_ADVISORY_MEMORY_SKIP_THRESHOLD:
                log.debug('[F350M-FED-P3-FOLLOWUP] skipping: memory_pressure=%.2f > %.2f', memory_pressure, FEDERATED_ADVISORY_MEMORY_SKIP_THRESHOLD)
                elapsed = (perf_counter() - t0) * 1000
                return _with_federated_outcome(outcome, attempted=False, nodes=0, findings=0, updates=0, persists=0, mode=str(getattr(bridge, 'mode', 'none')), elapsed_ms=elapsed, error=None)
            findings = getattr(self._scheduler, '_all_findings', []) or []
            max_updates = min(len(findings), FEDERATED_ADVISORY_MAX_UPDATES)
            updates_emitted = 0
            for finding in findings[:max_updates]:
                try:
                    lane = _derive_federated_lane(finding)
                    conf = float(getattr(finding, 'confidence', 0.0) or 0.0)
                    conf = max(0.0, min(1.0, conf))
                    state = (lane, len(findings))
                    bridge.update(lane=lane, state=state, action=lane, reward=conf, next_state=state)
                    updates_emitted += 1
                except Exception as ue:
                    log.debug('[F350M-FED-P3-FOLLOWUP] bridge update skipped: %s', ue)
            persists_emitted = 0
            try:
                persisted = await bridge.persist_if_due()
                if persisted:
                    persists_emitted = 1
            except Exception as pe:
                log.debug('[F350M-FED-P3-FOLLOWUP] persist_if_due skipped: %s', pe)
            if memory_pressure > FEDERATED_ADVISORY_MEMORY_REDUCED_THRESHOLD:
                nodes = 1
            else:
                nodes = FEDERATED_ADVISORY_MAX_NODES
            elapsed = (perf_counter() - t0) * 1000
            log.debug('[F350M-FED-P3-FOLLOWUP] advisory done: updates=%d persists=%d mode=%s dur=%.2fms', updates_emitted, persists_emitted, getattr(bridge, 'mode', 'none'), elapsed)
            try:
                self._scheduler._federated_advisory_outcome = {'federated_attempted': True, 'federated_nodes': nodes, 'federated_findings': len(findings), 'federated_bridge_updates': updates_emitted, 'federated_bridge_persists': persists_emitted, 'federated_mode': str(getattr(bridge, 'mode', 'none')), 'federated_elapsed_ms': elapsed, 'federated_error': None}
            except Exception:  # noqa: BLE001
                pass
            return _with_federated_outcome(outcome, attempted=True, nodes=nodes, findings=len(findings), updates=updates_emitted, persists=persists_emitted, mode=str(getattr(bridge, 'mode', 'none')), elapsed_ms=elapsed, error=None)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed = (perf_counter() - t0) * 1000
            log.debug('[F350M-FED-P3-FOLLOWUP] advisory fail-soft: %s: %s', type(e).__name__, e)
            return _with_federated_outcome(outcome, attempted=True, nodes=0, findings=0, updates=0, persists=0, mode='none', elapsed_ms=elapsed, error=str(e))
FEDERATED_ADVISORY_MAX_NODES: int = 2
'Max virtual nodes in advisory mode (matches sidecar_adapter).'
FEDERATED_ADVISORY_MEMORY_SKIP_THRESHOLD: float = 0.85
'Skip advisory entirely if memory_pressure > this ratio.'
FEDERATED_ADVISORY_MEMORY_REDUCED_THRESHOLD: float = 0.7
'Reduce to 1 node if memory_pressure > this ratio.'
FEDERATED_ADVISORY_MAX_UPDATES: int = 500
'Hard cap on bridge.update() calls per advisory (bounded by len(findings)).'

def _derive_federated_lane(finding: Any) -> str:
    """
    Map an accepted finding to a federated lane (surface/dark/archive).

    Uses finding.source_lane attr if available, otherwise classifies by
    source_type heuristic. Returns "surface" as the safe default.
    """
    lane = getattr(finding, 'source_lane', None)
    if lane:
        return str(lane)
    src = str(getattr(finding, 'source_type', '') or '').lower()
    if any((k in src for k in ('onion', 'i2p', 'tor', 'dark', 'ipfs'))):
        return 'dark'
    if any((k in src for k in ('wayback', 'commoncrawl', 'archive'))):
        return 'archive'
    return 'surface'

def _with_federated_outcome(outcome: AdvisoryRunOutcome, *, attempted: bool, nodes: int, findings: int, updates: int, persists: int, mode: str, elapsed_ms: float, error: str | None) -> AdvisoryRunOutcome:
    """
    Build a new AdvisoryRunOutcome with federated fields populated.

    Frozen dataclass requires rebuilding the whole object. This helper
    keeps the call sites DRY.
    """
    return AdvisoryRunOutcome(planned_pivots=outcome.planned_pivots, executed_pivots=outcome.executed_pivots, governor_recorded=outcome.governor_recorded, brief_generated=outcome.brief_generated, local_search_attempted=outcome.local_search_attempted, local_search_hits=outcome.local_search_hits, local_search_source=outcome.local_search_source, local_search_indexed=outcome.local_search_indexed, local_search_elapsed_ms=outcome.local_search_elapsed_ms, local_search_top_results=outcome.local_search_top_results, local_search_error=outcome.local_search_error, federated_attempted=attempted, federated_nodes=nodes, federated_findings=findings, federated_bridge_updates=updates, federated_bridge_persists=persists, federated_mode=mode, federated_elapsed_ms=elapsed_ms, federated_error=error, error=outcome.error)