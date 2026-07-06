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

from __future__ import annotations

import asyncio
import time as _time
from dataclasses import dataclass, field
from typing import Any

# ── Winddown Result Types ──────────────────────────────────────────────────────


@dataclass
class WinddownPhaseResult:
    """Result from the winddown phase."""

    export_paths: list[str] = field(default_factory=list)
    synthesis_success: bool = False
    teardown_duration_s: float | None = None
    export_errors: list[str] = field(default_factory=list)
    error: str | None = None


# ── WinddownOrchestrator ───────────────────────────────────────────────────────


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

    async def run(
        self,
        ctx: Any,  # SprintContext
        lifecycle: Any,
        query: str,
    ) -> WinddownPhaseResult:
        """Run the complete winddown sequence.

        Returns WinddownPhaseResult with export paths and telemetry.
        """
        from runtime.scheduler_v2.protocol import WinddownPhaseResult

        _t_winddown_start = _time.monotonic()
        _result = ctx.result
        _config = ctx.config

        export_paths: list[str] = []
        export_errors: list[str] = []
        synthesis_success = False

        try:
            # ── 1. Pressure relief at winddown start ──────────────────────────
            self._maybe_call_pressure_relief(ctx)

            # ── 2. Runner teardown ────────────────────────────────────────────
            if ctx.runner:
                ctx.runner.teardown()

            # ── 3. Entity signal extractor teardown ───────────────────────────
            await self._shutdown_entity_signal_extractor()

            # ── 4. Browser pool teardown ─────────────────────────────────────
            await self._teardown_browser_pool(ctx)

            # ── 5. Run export ─────────────────────────────────────────────────
            _exp_result = await self._run_export(ctx, lifecycle, query)
            export_paths = _exp_result.get('paths', [])
            export_errors = _exp_result.get('errors', [])

            # ── 6. Await synthesis task ───────────────────────────────────────
            _synth_success = await self._await_synthesis(ctx)
            synthesis_success = _synth_success

            # ── 7. DuckDB vacuum ───────────────────────────────────────────────
            await self._run_vacuum(ctx)

            # ── 8. Dedup close ────────────────────────────────────────────────
            await self._close_dedup(ctx)

            # ── 9. Graph save + sync ──────────────────────────────────────────
            await self._close_graph(ctx)

            # ── 10. Enrichment services close ──────────────────────────────────
            await self._close_enrichment(ctx)

            # ── 11. Privacy layer close ────────────────────────────────────────
            await self._close_privacy_layer(ctx)

            # ── 12. Sidecar advisory runner ────────────────────────────────────
            await self._run_sidecars(ctx)

            # ── 13. ANE semantic dedup advisory ────────────────────────────────
            await self._run_ane_semantic_dedup_advisory(ctx)

            # ── 14. Enhanced research ─────────────────────────────────────────
            self._maybe_launch_enhanced_research(ctx)

            # ── 15. Hermes unload ─────────────────────────────────────────────
            await self._unload_hermes_at_teardown(ctx)

            # ── 16. Lazy model unload ─────────────────────────────────────────
            self._unload_lazy_models(ctx)

            # ── 17. Cancel bg tasks ───────────────────────────────────────────
            await self._cancel_bg_tasks(ctx)

            # ── 18. Graceful sidecar shutdown ─────────────────────────────────
            await self._graceful_sidecar_shutdown(ctx)

            # ── 19. DuckDB close ──────────────────────────────────────────────
            await self._close_duckdb(ctx)

            # Finalize result
            _result.final_phase = ctx.runner.current_phase if ctx.runner else "WINDDOWN"

        except Exception as exc:
            _result.aborted = True
            _result.abort_reason = f"winddown_exception:{type(exc).__name__}"
            return WinddownPhaseResult(
                export_paths=export_paths,
                synthesis_success=synthesis_success,
                teardown_duration_s=_time.monotonic() - _t_winddown_start,
                export_errors=export_errors,
                error=f"{type(exc).__name__}:{exc}",
            )

        return WinddownPhaseResult(
            export_paths=export_paths,
            synthesis_success=synthesis_success,
            teardown_duration_s=_time.monotonic() - _t_winddown_start,
            export_errors=export_errors,
        )

    # ── Export ────────────────────────────────────────────────────────────────

    async def _run_export(
        self,
        ctx: Any,
        lifecycle: Any,
        query: str,
    ) -> dict[str, Any]:
        """Run all four exporters + CTI + hypothesis. Returns {paths, errors}."""
        _result = ctx.result
        _config = ctx.config
        paths: list[str] = []
        errors: list[str] = []

        if not _config.export_enabled:
            return {'paths': paths, 'errors': errors}

        # Lazy import exporters
        (
            rend_md,
            rend_jsonld,
            rend_stix,
            rend_cti_stix,
            collect_cti_inputs,
        ) = _import_exporters()

        # Build diagnostic report
        report = await self._build_diagnostic_report(ctx, lifecycle)

        export_dir = _config.export_dir

        for render_fn, suffix in [
            (rend_md, "md"),
            (rend_jsonld, "jsonld"),
            (rend_stix, "stix.json"),
        ]:
            try:
                path = render_fn(report, export_dir or None)
                paths.append(str(path))
                _result.export_paths.append(str(path))
            except Exception as exc:
                errors.append(f"EXPORT_ERROR:{suffix}:{exc}")
                _result.export_paths.append(f"EXPORT_ERROR:{suffix}:{exc}")

        # CTI STIX export
        await self._run_cti_export(ctx, rend_cti_stix, collect_cti_inputs, report, export_dir)

        # Hypothesis export (F259)
        await self._run_hypothesis_export(ctx, report, export_dir)

        return {'paths': paths, 'errors': errors}

    async def _run_cti_export(
        self,
        ctx: Any,
        rend_cti_stix: Any,
        collect_cti_inputs: Any,
        report: dict[str, Any],
        export_dir: str | None,
    ) -> None:
        """Run CTI STIX export."""
        try:
            await self._cti_export_impl(ctx, rend_cti_stix, collect_cti_inputs, report, export_dir)
        except Exception as exc:
            ctx.result.export_paths.append(f"EXPORT_ERROR:cti_stix:{exc}")

    async def _cti_export_impl(
        self,
        ctx: Any,
        rend_cti_stix: Any,
        collect_cti_inputs: Any,
        report: dict[str, Any],
        export_dir: str | None,
    ) -> None:
        """CTI STIX export implementation (lazy import inner)."""
        try:
            cti_inputs = collect_cti_inputs()
            path = rend_cti_stix(cti_inputs, report, export_dir or None)
            ctx.result.export_paths.append(str(path))
        except Exception:
            pass  # fail-soft

    async def _run_hypothesis_export(
        self,
        ctx: Any,
        report: dict[str, Any],
        export_dir: str | None,
    ) -> None:
        """Sprint F259: Run causal hypothesis generation and export."""
        try:
            _hypothesis_result = await self._hypothesis_export_impl(ctx, report, export_dir)
            if _hypothesis_result:
                ctx.result.hypothesis_export_path = _hypothesis_result
        except Exception as exc:
            ctx.result.export_paths.append(f"EXPORT_ERROR:hypothesis:{exc}")

    async def _hypothesis_export_impl(
        self,
        ctx: Any,
        report: dict[str, Any],
        export_dir: str | None,
    ) -> str | None:
        """Hypothesis export implementation (lazy import inner)."""
        # Lazy import to avoid pulling in heavy hypothesis dependencies at import time
        try:
            from hledac.universal.brain.hypothesis_engine import HypothesisEngine
            engine = HypothesisEngine()
            hypotheses = await engine.generate_hypotheses(report, ctx.query)
            # Export hypotheses as JSON
            import json
            import os
            if export_dir:
                path = os.path.join(export_dir, "hypotheses.json")
                with open(path, 'w') as f:
                    json.dump([h.as_dict() if hasattr(h, 'as_dict') else str(h) for h in hypotheses], f)
                return path
        except Exception:
            pass
        return None

    # ── Sub-operation stubs ──────────────────────────────────────────────────

    async def _shutdown_entity_signal_extractor(self) -> None:
        """Shutdown entity_signal_extractor ThreadPoolExecutor."""
        try:
            from hledac.universal.intelligence.entity_signal_extractor import (
                reset_extractor_stats,
                shutdown_executor,
            )
            shutdown_executor()
            reset_extractor_stats()
        except Exception:
            pass

    async def _teardown_browser_pool(self, ctx: Any) -> None:
        """Teardown nodriver/camoufox lazy state at sprint winddown."""
        try:
            from hledac.universal.core.env_config import ENV
            if ENV.get_bool("HLEDAC_ENABLE_NODRIVER"):
                from fetching.public_fetcher import _teardown_browser_pool
                await _teardown_browser_pool()
        except Exception:
            pass

    async def _await_synthesis(self, ctx: Any) -> bool:
        """Await synthesis task launched during windup entry."""
        _synth_task = getattr(ctx, '_synth_windup_task', None)
        if _synth_task is not None:
            try:
                await _synth_task
                ctx._synth_windup_task = None
                return True
            except Exception:
                return False
        return False

    async def _run_vacuum(self, ctx: Any) -> None:
        """Post-export DuckDB vacuum — reclaim space if DB > 2GB."""
        _store = getattr(ctx, '_duckdb_store', None) or getattr(ctx, 'duckdb_store', None)
        if _store is not None:
            try:
                await _store.async_vacuum_if_needed(threshold_bytes=2 * (1024**3))
            except Exception:
                pass

    async def _close_dedup(self, ctx: Any) -> None:
        """Close persistent dedup at TEARDOWN."""
        try:
            _ds = getattr(ctx, '_duckdb_store', None) or getattr(ctx, 'duckdb_store', None)
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
                _engine.save_graph(LMDB_ROOT / "rel_discovery_graph.pkl")
                self._sync_latent_relationships_to_graph(ctx)
            except Exception:
                pass

    def _sync_latent_relationships_to_graph(self, ctx: Any) -> None:
        """Sync latent NetworkX relationships → DuckPGQ with low confidence."""
        try:
            if ctx.graph_service and hasattr(ctx.graph_service, 'upsert_relationship_batch'):
                _engine = getattr(ctx, '_rel_discovery_engine', None)
                if _engine and hasattr(_engine, 'get_latent_relationships'):
                    rels = _engine.get_latent_relationships()
                    if rels:
                        import asyncio
                        asyncio.create_task(
                            ctx.graph_service.upsert_relationship_batch(rels)
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
            if ENV.get_bool("HLEDAC_ENABLE_PRIVACY_LAYER"):
                _privacy = getattr(ctx, '_privacy_layer', None)
                if not _privacy and hasattr(ctx, 'layer_manager'):
                    _privacy = getattr(ctx.layer_manager, 'privacy', None)
                if _privacy and hasattr(ctx, '_privacy_context_id') and ctx._privacy_context_id:
                    await _privacy.close_privacy_context(ctx._privacy_context_id)
        except Exception:
            pass

    async def _run_sidecars(self, ctx: Any) -> None:
        """Run all advisory steps via SidecarOrchestrator."""
        _so = getattr(ctx, '_sidecar_orchestrator', None)
        if _so is None and hasattr(ctx, 'sidecar_orchestrator'):
            _so = ctx.sidecar_orchestrator
        if _so and hasattr(_so, 'run_advisory_runner'):
            try:
                task = asyncio.create_task(_so.run_advisory_runner())
                task.add_done_callback(
                    lambda t: None  # fail-soft done callback
                )
            except Exception:
                pass

    async def _run_ane_semantic_dedup_advisory(self, ctx: Any) -> None:
        """Run ANE semantic dedup advisory (near-duplicate detection)."""
        try:
            from hledac.universal.utils.semantic_dedup import run_ane_semantic_dedup
            _store = getattr(ctx, '_duckdb_store', None) or getattr(ctx, 'duckdb_store', None)
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

    def _unload_lazy_models(self, ctx: Any) -> None:
        """Release all lazy models (NER, GNN, ANE, MoE) via brain._lazy."""
        try:
            from hledac.universal.brain import _lazy as lazy_module
            lazy_module.unload_all()
        except Exception:
            pass

    async def _cancel_bg_tasks(self, ctx: Any) -> None:
        """Cancel all background speculative tasks."""
        _bg_tasks = getattr(ctx, 'bg_tasks', None) or getattr(ctx, '_bg_tasks', None)
        if not _bg_tasks:
            return
        for t in list(_bg_tasks):
            t.cancel()
        if _bg_tasks:
            try:
                from hledac.universal.utils.async_helpers import safe_gather_fire_and_forget
                await safe_gather_fire_and_forget(*_bg_tasks, label="sprint_scheduler:winddown_bg")
            except Exception:
                pass
            _bg_tasks.clear()

    async def _graceful_sidecar_shutdown(self, ctx: Any) -> None:
        """Graceful sidecar task shutdown with 15s bounded timeout."""
        _sidecar_tasks = getattr(ctx, '_sidecar_tasks', None)
        if not _sidecar_tasks:
            return
        _pending = list(_sidecar_tasks)
        try:
            async with asyncio.timeout(15.0):
                from hledac.universal.utils.async_helpers import safe_gather_fire_and_forget
                await safe_gather_fire_and_forget(
                    *_pending, label="sprint_scheduler:sidecar_tasks"
                )
        except TimeoutError:
            for t in _pending:
                if not t.done():
                    t.cancel()
        except Exception:
            pass
        finally:
            _sidecar_tasks.clear()

    async def _close_duckdb(self, ctx: Any) -> None:
        """Close DuckDB store at teardown."""
        _store = getattr(ctx, '_duckdb_store', None) or getattr(ctx, 'duckdb_store', None)
        if _store and hasattr(_store, 'async_close'):
            try:
                await _store.async_close()
            except Exception:
                pass

    # ── Report building ────────────────────────────────────────────────────────

    async def _build_diagnostic_report(
        self,
        ctx: Any,
        lifecycle: Any,
    ) -> dict[str, Any]:
        """Build minimal diagnostic report from result + config."""
        return {
            'query': ctx.query,
            'config': {
                'sprint_duration_s': ctx.config.sprint_duration_s,
                'aggressive_mode': ctx.config.aggressive_mode,
                'export_dir': ctx.config.export_dir,
            },
            'result': ctx.result.__dict__ if hasattr(ctx.result, '__dict__') else {},
            'lifecycle_phase': lifecycle.current_phase if lifecycle else 'UNKNOWN',
        }


# ── Lazy import helpers ───────────────────────────────────────────────────────


def _import_exporters() -> tuple:
    """Lazy import all four exporters + CTI collector."""
    from hledac.universal.export.report_render import (
        render_markdown as rend_md,
        render_jsonld as rend_jsonld,
        render_stix as rend_stix,
        render_cti_stix as rend_cti_stix,
        collect_cti_inputs,
    )
    return (rend_md, rend_jsonld, rend_stix, rend_cti_stix, collect_cti_inputs)


# ── Protocol re-export ────────────────────────────────────────────────────────

from runtime.scheduler_v2.protocol import WinddownPhaseResult

__all__ = [
    "WinddownOrchestrator",
    "WinddownPhaseResult",
]
