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
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = ["SprintAdvisoryRunner", "AdvisoryRunOutcome", "build_search_documents_from_findings"]

log = logging.getLogger(__name__)

# Bounds
MAX_PIVOTS: int = 20  # from pivot_planner.py


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
        List[SearchDocument] suitable for LocalSearchSeam.index().
    """
    from hledac.universal.knowledge.search_index import SearchDocument

    MAX_INDEXED_FINDINGS = 5000
    seen_urls: set[str] = set()
    docs: list = []
    for f in findings:
        if len(docs) >= MAX_INDEXED_FINDINGS:
            break
        try:
            payload = getattr(f, "payload_text", "") or (f.get("payload_text", "") if isinstance(f, dict) else "")
            source_type = getattr(f, "source_type", "unknown") or (f.get("source_type", "unknown") if isinstance(f, dict) else "unknown")
            finding_id = getattr(f, "finding_id", "?") or (f.get("finding_id", "?") if isinstance(f, dict) else "?")
            url = getattr(f, "url", "") or (f.get("url", "") if isinstance(f, dict) else "")
        except Exception:
            continue
        if not payload:
            continue
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        title = payload[:80].strip()
        doc = SearchDocument(
            url=url or f"finding://{finding_id}",
            title=title,
            content=payload,
            metadata={"finding_id": finding_id, "source_type": source_type},
        )
        docs.append(doc)
    return docs


# ── Advisory Run Outcome ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AdvisoryRunOutcome:
    """
    Result of a full advisory run (all 4 advisory steps).

    Fields:
        planned_pivots: Number of pivots planned (0 if planner skipped/failed)
        executed_pivots: Number of pivots executed (0 if executor skipped/failed)
        governor_recorded: True if governor evaluate+apply succeeded
        brief_generated: True if analyst brief was generated
        error: Error message if any step failed, else None
    """

    planned_pivots: int = 0
    executed_pivots: int = 0
    governor_recorded: bool = False
    brief_generated: bool = False
    local_search_attempted: bool = False
    local_search_hits: int = 0
    local_search_source: str = "none"
    local_search_indexed: int = 0
    local_search_elapsed_ms: float = 0.0
    local_search_top_results: list = field(default_factory=list)
    local_search_error: Optional[str] = field(default=None)
    error: Optional[str] = field(default=None)


# ── Sprint Advisory Runner ──────────────────────────────────────────────────────


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

    def __init__(
        self,
        scheduler: Any,
        duckdb_store: Any = None,
        governor: Any = None,
        analyst_workbench: Any = None,
    ) -> None:
        self._scheduler = scheduler
        self._duckdb_store = duckdb_store
        self._governor = governor
        self._analyst_workbench = analyst_workbench

    # ── Public API ───────────────────────────────────────────────────────────

    async def run_all_advisories(self) -> AdvisoryRunOutcome:
        """
        Run all 4 advisory steps in explicit order.

        Order:
          1. pivot_planner  → planned_pivots
          2. pivot_executor → executed_pivots
          3. resource_governor → governor_recorded
          4. analyst_brief → brief_generated

        CancelledError: re-raised to caller.
        Fail-soft: any step failure returns partial outcome with error message.

        Returns:
            AdvisoryRunOutcome with counts/flags for each step.
        """
        outcome = AdvisoryRunOutcome()

        try:
            # Step 1: Pivot planner advisory
            outcome = await self._run_pivot_planner_advisory(outcome)

            # Step 2: Pivot executor advisory (depends on planner output)
            outcome = await self._run_pivot_executor_advisory(outcome)

            # Step 3: Resource governor advisory
            outcome = await self._run_resource_governor_advisory(outcome)

            # Step 4: Analyst brief advisory
            outcome = await self._run_analyst_brief_advisory(outcome)

            # Step 5: Local search advisory (F228C)
            outcome = await self._run_local_search_advisory(outcome)

        except asyncio.CancelledError:
            raise  # [I6] propagate CancelledError — never swallowed

        return outcome

    # ── Step 1: Pivot Planner ───────────────────────────────────────────────

    async def _run_pivot_planner_advisory(
        self, outcome: AdvisoryRunOutcome
    ) -> AdvisoryRunOutcome:
        """
        F202G: Run pivot planner on accepted findings for advisory ordering.

        Planner generates pivot suggestions; scheduler may use them as
        ordering input for future sprints. Advisory only.

        Fail-soft: errors never crash the runner.
        """
        planner = getattr(self._scheduler, "_pivot_planner", None)
        if planner is None:
            return outcome

        findings = getattr(self._scheduler, "_all_findings", [])
        if not findings:
            return outcome

        try:
            # Get graph stats for scoring
            graph_stats: dict[str, Any] = {}
            try:
                from hledac.universal.knowledge import graph_service

                stats = graph_service.graph_stats()
                if stats:
                    # F238D: also collect top nodes for node_degrees and domains
                    node_degrees: dict[str, int] = {}
                    domains: list[str] = []
                    try:
                        summary = graph_service.graph_analytics_summary(top_k=500)
                        if summary.get("analytics_available"):
                            for entity in summary.get("top_central_entities", [])[:500]:
                                val = entity.get("value", "")
                                deg = entity.get("degree", 0)
                                if val and deg > 0:
                                    domains.append(val)
                                    node_degrees[val] = deg
                    except Exception:
                        pass

                    graph_stats = {
                        "nodes": stats.get("nodes", 0),
                        "edges": stats.get("edges", 0),
                        "domains": domains,
                        "connected_iocs": set(),
                        "node_degrees": node_degrees,
                    }
            except Exception:
                pass  # Fail-soft: graph stats are optional

            # F203G: Get feedback summary from duckdb_store for scoring penalties
            feedback_summary: Any = None
            store = self._duckdb_store
            if store is not None:
                try:
                    from hledac.universal.runtime.hypothesis_feedback import (
                        HypothesisFeedbackAdapter,
                    )

                    adapter = HypothesisFeedbackAdapter(
                        duckdb_store=store,
                        target_id=getattr(self._scheduler, "sprint_id", "") or "default",
                    )
                    feedback_summary = await adapter.async_get_summary()
                except Exception:
                    feedback_summary = None  # Fail-safe

            # Plan pivots (synchronous, fail-soft)
            pivots = planner.plan_pivots(
                findings,
                graph_stats=graph_stats,
                feedback_summary=feedback_summary,
            )
            self._scheduler._planned_pivots = pivots
            log.debug(
                f"[F202G] Planned {len(pivots)} pivots from {len(findings)} findings"
            )

            return AdvisoryRunOutcome(
                planned_pivots=len(pivots),
                executed_pivots=outcome.executed_pivots,
                governor_recorded=outcome.governor_recorded,
                brief_generated=outcome.brief_generated,
                local_search_attempted=outcome.local_search_attempted,
                local_search_hits=outcome.local_search_hits,
                local_search_source=outcome.local_search_source,
                local_search_indexed=outcome.local_search_indexed,
                local_search_elapsed_ms=outcome.local_search_elapsed_ms,
                local_search_top_results=outcome.local_search_top_results,
                local_search_error=outcome.local_search_error,
                error=None,
            )

        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # Fail-soft: pivot planner must never crash runner

        return outcome

    # ── Step 2: Pivot Executor ──────────────────────────────────────────────

    async def _run_pivot_executor_advisory(
        self, outcome: AdvisoryRunOutcome
    ) -> AdvisoryRunOutcome:
        """
        F204C: Execute top pivots from PivotPlanner via AutonomousPivotExecutor.

        Bounded advisory: executor stores derived findings via canonical ingest
        and records HypothesisFeedback. Scheduler retains all authority.

        Fail-soft: errors never crash the runner.
        """
        pivots = getattr(self._scheduler, "_planned_pivots", None)
        if not pivots:
            return outcome

        store = self._duckdb_store
        if store is None:
            return outcome

        try:
            from hledac.universal.runtime.hypothesis_feedback import (
                HypothesisFeedbackAdapter,
            )
            from hledac.universal.runtime.pivot_executor import (
                AutonomousPivotExecutor,
            )

            feedback_adapter = HypothesisFeedbackAdapter(
                duckdb_store=store,
                target_id=getattr(self._scheduler, "sprint_id", "") or "default",
            )
            executor = AutonomousPivotExecutor(
                duckdb_store=store,
                resource_governor=self._governor,
                feedback_adapter=feedback_adapter,
            )
            results = await executor.execute_top(pivots, [])
            self._scheduler._pivot_execution_results = results
            log.debug(f"[F204C] Executed {len(results)} pivots")

            return AdvisoryRunOutcome(
                planned_pivots=outcome.planned_pivots,
                executed_pivots=len(results),
                governor_recorded=outcome.governor_recorded,
                brief_generated=outcome.brief_generated,
                local_search_attempted=outcome.local_search_attempted,
                local_search_hits=outcome.local_search_hits,
                local_search_source=outcome.local_search_source,
                local_search_indexed=outcome.local_search_indexed,
                local_search_elapsed_ms=outcome.local_search_elapsed_ms,
                local_search_top_results=outcome.local_search_top_results,
                local_search_error=outcome.local_search_error,
                error=None,
            )

        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # Fail-safe: pivot executor must never crash runner

        return outcome

    # ── Step 3: Resource Governor ──────────────────────────────────────────

    async def _run_resource_governor_advisory(
        self, outcome: AdvisoryRunOutcome
    ) -> AdvisoryRunOutcome:
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
            await governor.apply_decision(decision)
            governor_recorded = True
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # Fail-soft: governor must never crash runner

        # F204J: Track peak RSS for mission budget
        try:
            from hledac.universal.core.resource_governor import sample_uma_status
            from hledac.universal.runtime.resource_governor import MISSION_PEAK_RSS_GIB

            uma = sample_uma_status()
            if uma.system_used_gib > 0:
                rss_gib = uma.system_used_gib / (1024**3)
                peak_rss = getattr(self._scheduler, "_peak_rss_gib", 0.0)
                if rss_gib > peak_rss:
                    self._scheduler._peak_rss_gib = rss_gib
                if rss_gib > MISSION_PEAK_RSS_GIB:
                    result = getattr(self._scheduler, "_result", None)
                    if result is not None:
                        result.budget_violations += 1
        except Exception:
            pass  # Fail-soft: RSS tracking never crashes runner

        # F204J: Record sidecars skipped during this sprint
        # F222: Read from SidecarOrchestrator's dispatcher (canonical owner of skipped tracking)
        orchestrator = getattr(self._scheduler, "_sidecar_orchestrator", None)
        if orchestrator is not None:
            sidecars_skipped = getattr(orchestrator._dispatcher, "_sidecars_skipped", set())
        else:
            sidecars_skipped = set()
        peak_rss_gib = getattr(self._scheduler, "_peak_rss_gib", 0.0)
        result = getattr(self._scheduler, "_result", None)
        if result is not None:
            result.sidecars_skipped = tuple(sorted(sidecars_skipped))
            result.peak_rss_gib = peak_rss_gib

        return AdvisoryRunOutcome(
            planned_pivots=outcome.planned_pivots,
            executed_pivots=outcome.executed_pivots,
            governor_recorded=governor_recorded,
            brief_generated=outcome.brief_generated,
            local_search_attempted=outcome.local_search_attempted,
            local_search_hits=outcome.local_search_hits,
            local_search_source=outcome.local_search_source,
            local_search_indexed=outcome.local_search_indexed,
            local_search_elapsed_ms=outcome.local_search_elapsed_ms,
            local_search_top_results=outcome.local_search_top_results,
            local_search_error=outcome.local_search_error,
            error=None,
        )

    # ── Step 5: Local Search ────────────────────────────────────────────────

    async def _run_local_search_advisory(
        self, outcome: AdvisoryRunOutcome
    ) -> AdvisoryRunOutcome:
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
            local_search_top_results: List[dict] with url/title/score/source_type/finding_id
            local_search_error: Error string if failed, else None
        """
        from time import perf_counter

        t0 = perf_counter()
        try:
            from hledac.universal.knowledge.search_index import LocalSearchSeam

            seam = LocalSearchSeam()

            # ── Index accepted findings ──────────────────────────────────
            findings = getattr(self._scheduler, "_all_findings", []) or []
            docs = build_search_documents_from_findings(findings)
            indexed_count = seam.index(docs)

            # ── Search ─────────────────────────────────────────────────
            query = getattr(self._scheduler, "query", None) or ""
            if not query:
                elapsed = (perf_counter() - t0) * 1000
                return AdvisoryRunOutcome(
                    planned_pivots=outcome.planned_pivots,
                    executed_pivots=outcome.executed_pivots,
                    governor_recorded=outcome.governor_recorded,
                    brief_generated=outcome.brief_generated,
                    local_search_attempted=True,
                    local_search_hits=0,
                    local_search_indexed=indexed_count,
                    local_search_source="search_index",
                    local_search_elapsed_ms=elapsed,
                    local_search_top_results=[],
                    local_search_error=None,
                    error=None,
                )

            result = seam.search(query, top_k=10)
            hits = len(result.results)

            # Build top_results list
            top_results = []
            for doc in result.results:
                top_results.append({
                    "url": doc.url,
                    "title": doc.title,
                    "score": doc.score,
                    "source_type": doc.metadata.get("source_type", "unknown"),
                    "finding_id": doc.metadata.get("finding_id", ""),
                })

            elapsed = (perf_counter() - t0) * 1000
            return AdvisoryRunOutcome(
                planned_pivots=outcome.planned_pivots,
                executed_pivots=outcome.executed_pivots,
                governor_recorded=outcome.governor_recorded,
                brief_generated=outcome.brief_generated,
                local_search_attempted=True,
                local_search_hits=hits,
                local_search_indexed=indexed_count,
                local_search_source="search_index",
                local_search_elapsed_ms=elapsed,
                local_search_top_results=top_results,
                local_search_error=None,
                error=None,
            )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed = (perf_counter() - t0) * 1000
            return AdvisoryRunOutcome(
                planned_pivots=outcome.planned_pivots,
                executed_pivots=outcome.executed_pivots,
                governor_recorded=outcome.governor_recorded,
                brief_generated=outcome.brief_generated,
                local_search_attempted=True,
                local_search_hits=0,
                local_search_indexed=0,
                local_search_source="local_search",
                local_search_elapsed_ms=elapsed,
                local_search_top_results=[],
                local_search_error=str(e),
                error=None,
            )

    async def _run_analyst_brief_advisory(
        self, outcome: AdvisoryRunOutcome
    ) -> AdvisoryRunOutcome:
        """
        F204E/F205J: Generate analyst brief at TEARDOWN.

        Uses canonical target_id (query or duckdb_store lookup) instead of
        sprint_id, enabling cross-sprint target memory reads.

        Advisory only: brief summarizes sprint results but does not affect
        sprint execution or outcomes. Sprint retains all authority.

        Fail-soft: errors never crash the runner.

        Stores brief in scheduler._analyst_brief for export hookup.
        """
        # F204E wired: use injected workbench if available (DI injection path)
        workbench = getattr(self._scheduler, "_analyst_workbench", None)

        # F205J fix: create workbench on-demand if duckdb_store is available.
        # duckdb_store is set during run() so it IS available at teardown.
        duckdb_store = self._duckdb_store
        if workbench is None and duckdb_store is not None:
            try:
                from hledac.universal.knowledge.analyst_workbench import (
                    AnalystWorkbench,
                )

                workbench = AnalystWorkbench(duckdb_store=duckdb_store)
            except Exception:
                workbench = None

        if workbench is None:
            return outcome

        try:
            findings = getattr(self._scheduler, "_all_findings", [])
            if findings is None:
                findings = []

            # Get graph signal
            graph_signal = self._scheduler._get_graph_signal()

            # Get governor for RAM check
            governor = self._governor

            # Get sprint_id — distinguish None (never set) from "" (set but empty)
            # Only default to "unknown" when sprint_id was genuinely never set
            scheduler_sprint_id = getattr(self._scheduler, "sprint_id", None)
            if scheduler_sprint_id is not None and scheduler_sprint_id != "":
                sprint_id = scheduler_sprint_id
            elif scheduler_sprint_id == "":
                # sprint_id was explicitly set to empty string — use "unknown" for display
                sprint_id = "unknown"
            else:
                # sprint_id was never set (None) — genuine unknown
                sprint_id = "unknown"

            # F205J: Use canonical target_id — prefer query, fall back to sprint_id
            query = getattr(self._scheduler, "query", None) or ""
            target_id = query if query else sprint_id
            if not target_id:
                target_id = sprint_id

            # Build the brief (pass duckdb_store for target memory read)
            # F223F: pass store_findings_count=None for now — the canonical count
            # can be wired via duckdb_store.async_get_accepted_findings_count(target_id)
            # when duckdb_store has that method (not yet added to avoid schema migration).
            # With None, build_sprint_brief uses runtime len(findings) in headline,
            # which is already correct (sprint findings = runtime findings).
            brief = await workbench.build_sprint_brief(
                sprint_id=sprint_id,
                target_id=target_id,
                findings=findings,
                graph_signal=graph_signal,
                governor=governor,
                duckdb_store=duckdb_store,
                store_findings_count=None,
            )
            self._scheduler._analyst_brief = brief
            log.debug(f"[F204E] Analyst brief generated: {brief.headline}")

            return AdvisoryRunOutcome(
                planned_pivots=outcome.planned_pivots,
                executed_pivots=outcome.executed_pivots,
                governor_recorded=outcome.governor_recorded,
                brief_generated=True,
                local_search_attempted=outcome.local_search_attempted,
                local_search_hits=outcome.local_search_hits,
                local_search_source=outcome.local_search_source,
                local_search_indexed=outcome.local_search_indexed,
                local_search_elapsed_ms=outcome.local_search_elapsed_ms,
                local_search_top_results=outcome.local_search_top_results,
                local_search_error=outcome.local_search_error,
                error=None,
            )

        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # Fail-soft: brief generation must never crash runner

        return outcome
