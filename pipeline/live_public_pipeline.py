"""Sprint 8AE: Live public OSINT pipeline — Pure Adapter.

F360-REFACTOR: This file is now a thin adapter (≤200 LOC) that:
1. Wraps the Phase-based architecture from pipeline/public/_phases.py
2. Exports types for backward compatibility
3. Handles legacy CLI parameter mapping

All internal logic has been extracted to pipeline/public/_phases.py.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

# Import from phases module (re-exports everything needed)
from hledac.universal.pipeline.public._phases import (
    PipelineContext,
    PipelinePageResult,
    PipelineRunResult,
    DiscoveryPhaseResult,
    DiscoveryEngine,
    Phase1_Initialization,
    Phase2_ResourceGovernance,
    Phase3_DiscoveryRunner,
    Phase4_FetchOrchestrator,
    Phase5_TelemetryAggregator,
    Phase6_ReportGenerator,
    Phase7_SynthesisRunner,
    Phase8_ExportManager,
    Phase9_TemporalPersistence,
    _build_emergency_result,
    _build_pipeline_run_result,
    _ensure_discovery_patched,
    _ensure_ct_scanner_patched,
    _ASYNC_DISCOVERY_SEARCH,
    _CT_SCANNER_GET_SUBDOMAINS,
)

# Import generators from public module
from hledac.universal.pipeline.public._generators import (
    generate_bootstrap_urls,
    generate_rescue_urls,
    generate_seed_context_bootstrap_urls,
    generate_keyword_bootstrap_urls,
    _is_threat_query,
    _filter_public_noise,
)


# ----------------------------------------------------------------------
# Legacy Compatibility Functions
# ----------------------------------------------------------------------


def _patch_fetcher_and_matcher(fetch_fn: Any, match_fn: Any) -> None:
    """Legacy compatibility: delegate to public_fetch module."""
    from . import public_fetch as _pf

    _pf._patch_fetcher_and_matcher(fetch_fn, match_fn)


def _patch_ct_scanner(get_subdomains_fn: Any) -> None:
    """Patch in a CT scanner function."""
    global _CT_SCANNER_GET_SUBDOMAINS
    _CT_SCANNER_GET_SUBDOMAINS = get_subdomains_fn


def _make_finding_id(query: str, url: str, label: str, pattern: str, value: str) -> str:
    """Deterministic finding ID via SHA-256 hash."""
    from hledac.universal.pipeline.public_patterns import _make_finding_id as _id

    return _id(query, url, label, pattern, value)


# ----------------------------------------------------------------------
# Main Pipeline Entry Point
# ----------------------------------------------------------------------


async def async_run_live_public_pipeline(
    query: str,
    store: "DuckDBShadowStore | None" = None,
    *,
    max_results: int = 10,
    fetch_timeout_s: float = 35.0,
    fetch_max_bytes: int = 2000000,
    fetch_concurrency: int = 8,
    hermes_engine: Any | None = None,
    graph: Any | None = None,
    memory_manager: Any | None = None,
    session_id: str | None = None,
    vector_store: Any | None = None,
    run_loop: bool = False,
    rl_steps: int = 0,
    enqueue_hypothesis_pivot: Any | None = None,
    public_bootstrap_enabled: bool = False,
    seed_context: Any | None = None,
    fetch_fn: Any | None = None,
    match_fn: Any | None = None,
    discovery_fn: Any | None = None,
    ct_subdomains_fn: Any | None = None,
    clear_query_cache_fn: Any | None = None,
    export_dir: str | None = None,
    _sprint_id: str = "",
) -> PipelineRunResult:
    """F360-REFACTOR: Pure adapter (~90 LOC) delegating to Phase architecture.

    This function is a thin wrapper that:
    1. Handles legacy CLI parameter overrides
    2. Creates PipelineContext
    3. Runs Phase classes in sequence
    4. Builds PipelineRunResult

    All internal logic extracted to pipeline/public/_phases.py.
    """
    global _ASYNC_DISCOVERY_SEARCH, _CT_SCANNER_GET_SUBDOMAINS

    # Handle legacy parameter overrides
    if fetch_fn is not None:
        from . import public_fetch as _pf

        _pf._ASYNC_FETCH_PUBLIC_TEXT = fetch_fn
    if match_fn is not None:
        from . import public_fetch as _pf

        _pf._SYNC_MATCH_TEXT = match_fn
    if discovery_fn is not None:
        _ASYNC_DISCOVERY_SEARCH = discovery_fn
    if ct_subdomains_fn is not None:
        _CT_SCANNER_GET_SUBDOMAINS = ct_subdomains_fn
    _ensure_patched()

    # Build context
    ctx = PipelineContext(
        query=query,
        store=store,
        max_results=max_results,
        fetch_timeout_s=fetch_timeout_s,
        fetch_max_bytes=fetch_max_bytes,
        fetch_concurrency=fetch_concurrency,
        hermes_engine=hermes_engine,
        graph=graph,
        memory_manager=memory_manager,
        session_id=session_id,
        vector_store=vector_store,
        run_loop=run_loop,
        rl_steps=rl_steps,
        enqueue_hypothesis_pivot=enqueue_hypothesis_pivot,
        public_bootstrap_enabled=public_bootstrap_enabled,
        seed_context=seed_context,
        export_dir=export_dir,
        _sprint_id=_sprint_id,
        clear_query_cache_fn=clear_query_cache_fn,
    )

    try:
        # Phase 1: Initialization
        ctx = await Phase1_Initialization().run(ctx)

        # Phase 2: Resource Governance
        ctx = await Phase2_ResourceGovernance().run(ctx)
        if ctx._is_emergency:
            return _build_emergency_result(ctx)

        # Phase 3: Discovery
        ctx, discovery = await Phase3_DiscoveryRunner().run(ctx)
        public_stage_failure = discovery.discovery_telemetry.get("public_stage_failure")
        public_stage_failure_reason = discovery.discovery_telemetry.get("public_stage_failure_reason")

        # Phase 4: Fetch Orchestrator
        ctx, all_page_results = await Phase4_FetchOrchestrator().run(ctx, discovery)

        # Phase 5: Telemetry Aggregation
        telemetry = Phase5_TelemetryAggregator().run(ctx, discovery)

        # Phase 6: Report Generator
        ctx, generated_report, tot_solution_count = await Phase6_ReportGenerator().run(ctx, all_page_results)

        # Phase 7: Synthesis (memory-bounded)
        await Phase7_SynthesisRunner().run(ctx, telemetry["total_stored"])

        # Phase 8: Export
        if ctx.error is None:
            await Phase8_ExportManager().run(ctx, generated_report, all_page_results)

        # Phase 9: Temporal Persistence
        temporal_status = Phase9_TemporalPersistence().run()
        ctx = PipelineContext(**{**ctx.__dict__, **temporal_status})

        # Build final result
        return _build_pipeline_run_result(
            ctx=ctx,
            telemetry=telemetry,
            discovery=discovery,
            public_stage_failure=public_stage_failure,
            public_stage_failure_reason=public_stage_failure_reason,
            generated_report=generated_report,
            tot_solution_count=tot_solution_count,
        )

    except asyncio.CancelledError:
        logger.debug("Pipeline cancelled")
        raise


def _ensure_patched() -> None:
    """Ensure all components are patched."""
    _ensure_discovery_patched()
    _ensure_ct_scanner_patched()
