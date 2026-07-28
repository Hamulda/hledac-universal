"""STEP 4 Phase 4 — WinddownOrchestrator for SprintScheduler v2.

F350M-R / Issue #P2.

Extracts winddown phase logic from runtime/sprint_scheduler.py:
    - _run_winddown (~1500 lines of teardown/export logic)
    - _run_export (4 renderers + CTI + hypothesis)
    - _run_hypothesis_export (F259 causal graph reasoning)

Design:
    - WinddownOrchestrator.run() is the entry point called by SprintSchedulerV2
    - Each sub-operation is a standalone async function
    - All `self._result.X = Y` → `ctx.result.X = Y`
    - Lazy imports avoid M1 Metal init at import time
"""
import asyncio
import time as _time
from dataclasses import dataclass, field
import msgspec
from typing import Any
from hledac.universal.runtime.scheduler_v2._task_registry import (
    TaskScope,
    get_task_registry,
    safe_create_task_tracked,
)
from hledac.universal.utils.async_helpers import parallel, safe_wait_for

try:
    import orjson

    _ORJSON_AVAILABLE = True
except ImportError:
    _ORJSON_AVAILABLE = False
    import orjson as _orjson_stub  # type: ignore[attr-defined]

    orjson = _orjson_stub


def _maybe_call_pressure_relief(ctx: Any) -> None:
    """F273G: Call malloc_zone_pressure_relief if governor recommends.

    Standalone function (originally SprintScheduler method) for winddown use.
    Safe to call at any point - no-op if unavailable.
    """
    try:
        import resource  # macOS/Unix malloc zone pressure relief
        from hledac.universal.core.resource_governor import M1ResourceGovernor
        _gov = getattr(ctx, '_resource_governor', None)
        if _gov and hasattr(_gov, 'maybe_pressure_relief'):
            _gov.maybe_pressure_relief()
    except Exception:
        pass

class WinddownPhaseResult(msgspec.Struct, gc=False):
    """Result from the winddown phase."""
    export_paths: list[str] = field(default_factory=list)
    synthesis_success: bool = False
    teardown_duration_s: float | None = None
    export_errors: list[str] = field(default_factory=list)
    error: str | None = None

class WinddownOrchestrator:
    """Orchestrates the winddown phase: export, synthesis, teardown.

    Replaces the 33 449 LOC SprintScheduler's winddown section
    with a thin class that delegates to typed sub-operations.

    Lifecycle:
        run() → _run_winddown_sequence()
            → _maybe_call_pressure_relief()
            → runner.teardown()
            → _run_export(lifecycle)
            → _await_synthesis()
            → _run_vacuum()
            → _close_dedup()
            → _close_graph()
            → _close_enrichment()
            → _run_sidecars()
            → _run_ane_semantic_dedup()
            → _maybe_launch_research()
            → _unload_hermes()
            → _unload_lazy_models()
            → _cancel_bg_tasks()
            → _graceful_sidecar_shutdown()
            → _close_duckdb()
    """
    __slots__ = ()

    def __init__(self) -> None:
        pass

    async def run(self, ctx: Any, lifecycle: Any, query: str) -> WinddownPhaseResult:
        _t_winddown_start = _time.monotonic()
        _result = ctx.result
        _config = ctx.config
        export_paths: list[str] = []
        export_errors: list[str] = []
        synthesis_success = False

        # Phase 0: Serial sync operations (required first, cannot parallelize)
        _maybe_call_pressure_relief(ctx)
        if ctx.runner:
            ctx.runner.teardown()

        # Phase 1: Parallel I/O-bound winddown using TaskGroup
        # NOTE: _graceful_sidecar_shutdown runs AFTER the parallel phase due to
        # its 15s timeout - we don't want it blocking other I/O operations.
        _parallel_errors: list[str] = []
        try:
            async with asyncio.TaskGroup() as _tg:
                _tg.create_task(self._shutdown_entity_signal_extractor())
                _tg.create_task(self._teardown_browser_pool(ctx))
                _tg.create_task(self._run_export_as_task(ctx, lifecycle, query))
                _tg.create_task(self._run_vacuum(ctx))
                _tg.create_task(self._close_dedup(ctx))
                _tg.create_task(self._close_graph(ctx))
                _tg.create_task(self._close_enrichment(ctx))
                _tg.create_task(self._close_privacy_layer(ctx))
                _tg.create_task(self._run_sidecars(ctx))
                _tg.create_task(self._run_ane_semantic_dedup_advisory(ctx))
                _tg.create_task(self._cancel_bg_tasks(ctx))
        except BaseExceptionGroup as _eg:
            # TaskGroup captures all exceptions; we log and continue for winddown
            for _exc in _eg.exceptions:
                _parallel_errors.append(f'WINDDOWN_PARALLEL:{type(_exc).__name__}:{_exc}')

        # ISSUE F350M-R: Cancel all tracked tasks BEFORE Phase 2 synthesis/serial ops.
        # TaskRegistry.cancel_all() sends CancelledError to all registered tasks,
        # preventing them from racing against DuckDB close in Phase 4.
        # cleanup_after_cancel() then reclaims MLX Metal cache on M1 8GB.
        try:
            _registry = get_task_registry()
            await _registry.cancel_all(timeout=2.0)
            await _registry.cleanup_after_cancel()
        except Exception:
            pass

        # Extract export results (set by _run_export_as_task via ctx._cycle)
        _exp_result = getattr(ctx._cycle, '_export_result', None) or {}
        export_paths = _exp_result.get('paths', [])
        export_errors = _parallel_errors + _exp_result.get('errors', [])

        # Phase 2: Serial barrier - synthesis must complete before hermes unload
        _synth_success = await self._await_synthesis(ctx)
        synthesis_success = _synth_success

        # Phase 3: Hermes unload (depends on synthesis complete) + lazy models
        await self._unload_hermes_at_teardown(ctx)
        self._unload_lazy_models(ctx)

        # Phase 4: DuckDB close - MUST be last
        await self._close_duckdb(ctx)

        # Final cleanup
        self._maybe_launch_enhanced_research(ctx)
        # Note: sidecar_tasks removed from _CycleState — lives on SprintSchedulerV2 only
        # ctx._cycle fields are intentionally NOT cleared — sprint is terminating
        _result.final_phase = ctx.runner.current_phase if ctx.runner else 'WINDDOWN'

        return WinddownPhaseResult(
            export_paths=export_paths,
            synthesis_success=synthesis_success,
            teardown_duration_s=_time.monotonic() - _t_winddown_start,
            export_errors=export_errors,
        )

    async def _run_export_as_task(self, ctx: Any, lifecycle: Any, query: str) -> None:
        """Run export and store results in ctx._export_result for parallel retrieval.

        This wraps _run_export for use in TaskGroup - stores results in ctx._cycle
        so the calling code can retrieve them after the TaskGroup completes.
        """
        _result = await self._run_export(ctx, lifecycle, query)
        ctx._cycle._export_result = _result

    async def _run_export(self, ctx: Any, lifecycle: Any, query: str) -> dict[str, Any]:
        """Run all four exporters + CTI + hypothesis. Returns {paths, errors}."""
        _result = ctx.result
        _config = ctx.config
        paths: list[str] = []
        errors: list[str] = []
        if not _config.export_enabled:
            return {'paths': paths, 'errors': errors}
        rend_md, rend_jsonld, rend_stix, rend_cti_stix, collect_cti_inputs = _import_exporters()
        report = await self._build_diagnostic_report(ctx, lifecycle)
        export_dir = _config.export_dir
        for render_fn, suffix in [(rend_md, 'md'), (rend_jsonld, 'jsonld'), (rend_stix, 'stix.json')]:
            try:
                path = render_fn(report, export_dir or None)
                paths.append(str(path))
                _result.export_paths.append(str(path))
            except Exception as exc:
                errors.append(f'EXPORT_ERROR:{suffix}:{exc}')
                _result.export_paths.append(f'EXPORT_ERROR:{suffix}:{exc}')
        await self._run_cti_export(ctx, rend_cti_stix, collect_cti_inputs, report, export_dir)
        await self._run_hypothesis_export(ctx, report, export_dir)
        return {'paths': paths, 'errors': errors}

    async def _run_cti_export(self, ctx: Any, rend_cti_stix: Any, collect_cti_inputs: Any, report: dict[str, Any], export_dir: str | None) -> None:
        """Run CTI STIX export."""
        try:
            await self._cti_export_impl(ctx, rend_cti_stix, collect_cti_inputs, report, export_dir)
        except Exception as exc:
            ctx.result.export_paths.append(f'EXPORT_ERROR:cti_stix:{exc}')

    async def _cti_export_impl(self, ctx: Any, rend_cti_stix: Any, collect_cti_inputs: Any, report: dict[str, Any], export_dir: str | None) -> None:
        """CTI STIX export implementation (lazy import inner)."""
        try:
            cti_inputs = collect_cti_inputs()
            path = rend_cti_stix(cti_inputs, report, export_dir or None)
            ctx.result.export_paths.append(str(path))
        except Exception:
            pass

    async def _run_hypothesis_export(self, ctx: Any, report: dict[str, Any], export_dir: str | None) -> None:
        """Sprint F259: Run causal hypothesis generation and export."""
        try:
            _hypothesis_result = await self._hypothesis_export_impl(ctx, report, export_dir)
            if _hypothesis_result:
                ctx.result.hypothesis_export_path = _hypothesis_result
        except Exception as exc:
            ctx.result.export_paths.append(f'EXPORT_ERROR:hypothesis:{exc}')

    async def _hypothesis_export_impl(self, ctx: Any, report: dict[str, Any], export_dir: str | None) -> str | None:
        """Hypothesis export implementation (lazy import inner)."""
        try:
            from hledac.universal.brain.hypothesis_engine import HypothesisEngine
            engine = HypothesisEngine()
            hypotheses = await engine.generate_hypotheses(report, ctx.query)
            import os
            if export_dir:
                path = os.path.join(export_dir, 'hypotheses.json')
                data = [h.as_dict() if hasattr(h, 'as_dict') else str(h) for h in hypotheses]
                if _ORJSON_AVAILABLE:
                    with open(path, 'wb') as f:
                        f.write(orjson.dumps(data))
                else:
                    import json as _stdlib_json
                    with open(path, 'w') as f:
                        _stdlib_json.dump(data, f)
                return path
        except Exception:
            pass
        return None

    async def _shutdown_entity_signal_extractor(self) -> None:
        """Shutdown entity_signal_extractor ThreadPoolExecutor."""
        try:
            from hledac.universal.intel.entity_signal_extractor import reset_extractor_stats, shutdown_executor
            shutdown_executor()
            reset_extractor_stats()
        except Exception:
            pass

    async def _teardown_browser_pool(self, ctx: Any) -> None:
        """Teardown nodriver/camoufox lazy state at sprint winddown."""
        try:
            from hledac.universal.core.env_config import ENV
            if ENV.get_bool('HLEDAC_ENABLE_NODRIVER'):
                from fetching.public_fetcher import _teardown_browser_pool
                await _teardown_browser_pool()
        except Exception:
            pass

    async def _await_synthesis(self, ctx: Any) -> bool:
        """Await synthesis task launched during windup entry."""
        _synth_task = getattr(ctx._cycle, 'synth_windup_task', None)
        if _synth_task is not None:
            try:
                await _synth_task
                ctx._cycle.synth_windup_task = None
                return True
            except Exception:
                return False
        return False

    async def _run_vacuum(self, ctx: Any) -> None:
        """Post-export DuckDB vacuum — reclaim space if DB > 2GB."""
        # SC-05 FIX: ctx._duckdb_store no longer exists. ctx.duckdb_store is the convenience property.
        _store = ctx.duckdb_store
        if _store is not None:
            try:
                await _store.async_vacuum_if_needed(threshold_bytes=2 * 1024 ** 3)
            except Exception:
                pass

    async def _close_dedup(self, ctx: Any) -> None:
        """Close persistent dedup at TEARDOWN."""
        # SC-05 FIX: ctx._duckdb_store no longer exists. ctx.duckdb_store is the convenience property.
        try:
            _ds = ctx.duckdb_store
            if _ds and hasattr(_ds, 'close_dedup'):
                await _ds.close_dedup()
        except Exception:
            pass

    async def _close_graph(self, ctx: Any) -> None:
        """Save RelationshipDiscoveryEngine graph and sync latent relationships."""
        _engine = getattr(ctx, '_rel_discovery_engine', None)
        if _engine is not None:
            try:
                from hledac.universal.paths import LMDB_ROOT
                _engine.save_graph(LMDB_ROOT / 'rel_discovery_graph.pkl')
                await self._sync_latent_relationships_to_graph(ctx)
            except Exception:
                pass

    async def _sync_latent_relationships_to_graph(self, ctx: Any) -> None:
        """Sync latent NetworkX relationships → DuckPGQ with low confidence.

        Awaits the batch upsert before returning to ensure no relationships
        are lost when winddown ends.
        """
        try:
            if ctx.graph_service and hasattr(ctx.graph_service, 'upsert_relationship_batch'):
                _engine = getattr(ctx, '_rel_discovery_engine', None)
                if _engine and hasattr(_engine, 'get_latent_relationships'):
                    rels = _engine.get_latent_relationships()
                    if rels:
                        # Await the tracked task so relationships are persisted before winddown exits
                        await safe_create_task_tracked(
                            ctx.graph_service.upsert_relationship_batch(rels),
                            name="winddown:upsert_relationship_batch",
                            scope=TaskScope.WINDUP,
                        )
        except Exception:
            pass

    async def _close_enrichment(self, ctx: Any) -> None:
        """Close forensics enricher and LMDB at TEARDOWN."""
        if ctx.enrichment_services:
            try:
                await ctx.enrichment_services.close()
            except Exception:
                pass

    async def _close_privacy_layer(self, ctx: Any) -> None:
        """Close privacy context at TEARDOWN."""
        try:
            from hledac.universal.core.env_config import ENV
            if ENV.get_bool('HLEDAC_ENABLE_PRIVACY_LAYER'):
                _privacy = getattr(ctx._cycle, 'privacy_layer', None)
                if not _privacy and hasattr(ctx, 'layer_manager'):
                    _privacy = getattr(ctx.layer_manager, 'privacy', None)
                if _privacy and hasattr(ctx._cycle, 'privacy_context_id') and ctx._cycle.privacy_context_id:
                    await _privacy.close_privacy_context(ctx._cycle.privacy_context_id)
        except Exception:
            pass

    async def _run_sidecars(self, ctx: Any) -> None:
        """Run all advisory steps via SidecarOrchestrator.

        Awaits the advisory runner task before returning so all sidecar
        work completes during winddown.
        """
        _so = getattr(ctx, '_sidecar_orchestrator', None)
        if _so is None and hasattr(ctx, 'sidecar_orchestrator'):
            _so = ctx.sidecar_orchestrator
        if _so and hasattr(_so, 'run_advisory_runner'):
            try:
                # Await so sidecar work completes before winddown exits
                await safe_create_task_tracked(
                    _so.run_advisory_runner(),
                    name="winddown:advisory_runner",
                    scope=TaskScope.WINDUP_SIDECAR,
                )
            except Exception:
                pass

    async def _run_ane_semantic_dedup_advisory(self, ctx: Any) -> None:
        """Run ANE semantic dedup advisory (near-duplicate detection)."""
        try:
            from hledac.universal.utils.semantic_dedup import run_ane_semantic_dedup
            # SC-05 FIX: ctx._duckdb_store no longer exists. ctx.duckdb_store is the convenience property.
            _store = ctx.duckdb_store
            if _store:
                await run_ane_semantic_dedup(_store)
        except Exception:
            pass

    def _maybe_launch_enhanced_research(self, ctx: Any) -> None:
        """Launch deep research advisory fire-and-forget after teardown/export."""
        try:
            from hledac.universal.brain.research_advisor import maybe_launch_research
            maybe_launch_research(ctx.result, ctx.query)
        except Exception:
            pass

    async def _unload_hermes_at_teardown(self, ctx: Any) -> None:
        """Hermes engine teardown via ModelManager (bounded M1 8GB lifecycle)."""
        try:
            if ctx.hermes_engine and hasattr(ctx.hermes_engine, 'unload'):
                await ctx.hermes_engine.unload()
        except Exception:
            pass

    def _unload_lazy_models(self, _ctx: Any) -> None:
        """Release all lazy models (NER, GNN, ANE, MoE) via brain._lazy."""
        try:
            from hledac.universal.brain import _lazy as lazy_module
            lazy_module.unload_all()
        except Exception:
            pass

    async def _cancel_bg_tasks(self, ctx: Any) -> None:
        """Cancel all background speculative tasks.

        Uses asyncio.wait_for with 5s timeout (instead of parallel() with 300s default)
        so winddown cannot be blocked by stuck cancelled tasks on M1 8GB.
        """
        _bg_tasks = getattr(ctx, 'bg_tasks', None) or getattr(ctx, '_bg_tasks', None)
        if not _bg_tasks:
            return
        for t in list(_bg_tasks):
            t.cancel()
        if _bg_tasks:
            try:
                # F3XX: parallel(policy="log", timeout=5.0) replaces asyncio.gather + safe_wait_for.
                # CancelledError is re-raised per I6; TimeoutError returns here (task cleanup below).
                await parallel(list(_bg_tasks), policy="log", timeout=5.0, ctx="_cancel_bg_tasks")
            except asyncio.TimeoutError:
                for t in _bg_tasks:
                    if not t.done():
                        t.cancel()
            except Exception:
                pass
            _bg_tasks.clear()

    async def _close_duckdb(self, ctx: Any) -> None:
        """Close DuckDB store at teardown."""
        # SC-05 FIX: ctx._duckdb_store no longer exists. ctx.duckdb_store is the convenience property.
        _store = ctx.duckdb_store
        if _store and hasattr(_store, 'async_close'):
            try:
                await _store.async_close()
            except Exception:
                pass

    async def _build_diagnostic_report(self, ctx: Any, lifecycle: Any) -> dict[str, Any]:
        """Build minimal diagnostic report from result + config."""
        return {'query': ctx.query, 'config': {'sprint_duration_s': ctx.config.sprint_duration_s, 'aggressive_mode': ctx.config.aggressive_mode, 'export_dir': ctx.config.export_dir}, 'result': ctx.result.__dict__ if hasattr(ctx.result, '__dict__') else {}, 'lifecycle_phase': lifecycle.current_phase if lifecycle else 'UNKNOWN'}

def _import_exporters() -> tuple:
    """Lazy import all four exporters + CTI collector."""
    from hledac.universal.export.report_render import render_markdown as rend_md, render_jsonld as rend_jsonld, render_stix as rend_stix, render_cti_stix as rend_cti_stix, collect_cti_inputs
    return (rend_md, rend_jsonld, rend_stix, rend_cti_stix, collect_cti_inputs)
__all__ = ['WinddownOrchestrator', 'WinddownPhaseResult']