"""
runtime/sprint/phases/windup.py — Sprint windup phase

F350M-R: WINDUP phase - result processing, report generation, export.

Handles:
- Phase 1: Early windup - events, CT discovery, sprint delta
- Phase 2: Compute phase timings and get scheduler intelligence
- Phase 3: Compute derived metrics and classifications
- Phase 4: Log runtime truth and sprint done
- Phase 5: Export handoff and deep probe

Usage:
    await _run_sprint_windup(ctx=ctx, query=query, duration_s=duration_s, ...)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ..types import (
    SprintRunContext,
    ReportBuildInput,
    VerdictHintInput,
    CheckpointInput,
    RuntimeTruthInput,
    ExportHandoffInput,
    _compute_verdict_and_hint,
    _compute_checkpoint_category,
    _compute_checkpoint_priority,
    _CheckpointPriority,
    _CHECKPOINT_PRIORITY_MAP,
    _CHECKPOINT_REASON_TEMPLATES,
    _serialize_report,
    _PLATFORM_INFO,
    AcqReportPayload,  # Issue #9: msgspec.Struct
)
from ..cleanup import _fail_safe_async
from ..delta_writer import write_sprint_delta
from ..truth_logger import (
    _runtime_truth,
    compute_timing_truth,
    build_observed_run_tuple,
)

# Lazy import for heavy module (M1 8GB optimization)
# msgspec is imported lazily inside _scheduler_result_acquisition_payload
# DuckDB is imported lazily in phase1_early via resource_governor

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

logger = logging.getLogger(__name__)


# =============================================================================
# Phase 1: Early Windup
# =============================================================================

async def _windup_phase1_early(
    ctx: SprintRunContext,
    query: str,
    actual_duration: float,
) -> None:
    """Phase 1: Early windup - events, CT discovery, sprint delta."""
    result = ctx.result
    
    # Evidence log windup event
    if ctx.evidence_log is not None:
        with _fail_safe_async('debug', 'evidence_log.windup_event'):
            ctx.evidence_log.create_event(
                event_type='observation',
                payload={'phase': 'WINDUP', 'sprint_id': ctx.sprint_id, 'query': query},
                confidence=1.0
            )
    
    # CT log discovery (if not aggressive mode)
    if not ctx.scheduler._config.aggressive_mode:
        with _fail_safe_async('debug', 'ct_log_discovery'):
            await ctx.scheduler._run_ct_log_discovery_in_cycle(query=query, store=ctx.store)
            result.accepted_findings += result.ct_log_stored
    
    # Write sprint delta
    from hledac.universal._core.resource_governor import sample_uma_status
    uma_peak_gib = sample_uma_status().system_used_gib
    await write_sprint_delta(
        store=ctx.store,
        sprint_id=ctx.sprint_id,
        query=query,
        new_findings=result.accepted_findings,
        dedup_hits=result.duplicate_entry_hashes_skipped,
        ioc_nodes=result.unique_entry_hashes_seen,
        uma_baseline_gib=ctx.uma_baseline_gib,
        uma_peak_gib=uma_peak_gib,
        synthesis_success=result.accepted_findings > 0,
        duration_s=actual_duration,
        hits_per_source=result.hits_per_source,
        seed_state=ctx.seed_state,
    )
    
    # Evidence log finalize
    if ctx.evidence_log is not None:
        with _fail_safe_async('warning', 'evidence_log.teardown'):
            ctx.evidence_log.finalize()
            ctx.evidence_log.freeze()
    
    # Record teardown timing
    ctx.phase_times['TEARDOWN'] = time.monotonic()
    
    # Evidence log teardown event
    if ctx.evidence_log is not None:
        with _fail_safe_async('debug', 'evidence_log.teardown_event'):
            ctx.evidence_log.create_event(
                event_type='observation',
                payload={
                    'phase': 'TEARDOWN',
                    'sprint_id': ctx.sprint_id,
                    'actual_duration_s': round(actual_duration, 2)
                },
                confidence=1.0
            )


# =============================================================================
# Phase 2: Timing
# =============================================================================

def _windup_phase2_timing(ctx: SprintRunContext, duration_s: float) -> dict[str, float]:
    """Phase 2: Compute phase timings and get scheduler intelligence."""
    from hledac.universal.runtime.sprint_lifecycle import _PHASE_ORDER
    
    phases = _PHASE_ORDER
    _phase_durations: dict[str, float] = {}
    
    for i, ph in enumerate(phases):
        ph_name = ph.name
        if ph_name in ctx.phase_times:
            next_ph = phases[i + 1] if i + 1 < len(phases) else None
            if next_ph is not None and next_ph.name in ctx.phase_times:
                _phase_durations[ph_name] = round(
                    ctx.phase_times[next_ph.name] - ctx.phase_times[ph_name], 2
                )
    
    with _fail_safe_async('debug', 'compute_sprint_intelligence'):
        ctx.intel = ctx.scheduler.compute_sprint_intelligence() or {}
    
    return _phase_durations


# =============================================================================
# Phase 3: Metrics & Classifications
# =============================================================================

def _windup_phase3_compute_metrics(
    ctx: SprintRunContext,
    result: Any,
    actual_duration: float,
) -> dict[str, Any]:
    """Phase 3a: Compute derived metrics."""
    findings_per_min = result.accepted_findings / (actual_duration / 60.0) if actual_duration > 0 else 0.0
    total_seen = result.unique_entry_hashes_seen + result.duplicate_entry_hashes_skipped
    dup_rate = result.duplicate_entry_hashes_skipped / total_seen * 100 if total_seen > 0 else 0.0
    feed_fnd = result.accepted_findings - result.public_accepted_findings
    public_pct = result.public_accepted_findings / result.accepted_findings * 100 if result.accepted_findings > 0 else 0.0
    
    src_mix: list[str] = []
    for src, cnt in sorted(result.hits_per_source.items(), key=lambda x: x[1], reverse=True):
        src_mix.append(f'{src}={cnt}')
    src_mix_str = ', '.join(src_mix) if src_mix else 'none'
    
    return {
        'findings_per_min': findings_per_min,
        'dup_rate': dup_rate,
        'feed_fnd': feed_fnd,
        'public_pct': public_pct,
        'src_mix_str': src_mix_str,
    }


def _windup_phase3_compute_classifications(
    ctx: SprintRunContext,
    result: Any,
    metrics: dict,
    query: str,
    duration_s: float,
) -> dict[str, Any]:
    """
    Phase 3b: Compute verdict, runtime_truth, timing_truth, checkpoint category.
    
    REFACTORED: Extracted decision logic to reduce cognitive complexity.
    Uses decision tables from types module.
    """
    # Timing calculations
    time_to_windup_s = ctx.phase_times['WINDUP'] - ctx.phase_times['BOOT']
    actual_duration = ctx.phase_times.get('TEARDOWN', ctx.phase_times['WINDUP']) - ctx.phase_times['BOOT']
    pre_scheduler_boot_s = ctx.phase_times.get('WARMUP', 0) - ctx.phase_times['BOOT']
    _windup_mark = ctx.phase_times.get('WINDUP', ctx.phase_times.get('TEARDOWN', ctx.phase_times['BOOT']))
    scheduler_wall_s = _windup_mark - ctx.phase_times.get('WARMUP', ctx.phase_times['BOOT'])
    windup_lead_observed_s = ctx.phase_times['TEARDOWN'] - ctx.phase_times.get('WINDUP', 0)
    
    # Timing truth
    timing_truth = compute_timing_truth(
        duration_s=duration_s,
        windup_lead_s=ctx.scheduler._config.windup_lead_s,
        time_to_windup_s=time_to_windup_s,
        time_to_teardown_s=ctx.phase_times['TEARDOWN'] - ctx.phase_times['BOOT'],
        active_window_budget_s=duration_s - ctx.effective_windup_s,
        windup_lead_observed_s=windup_lead_observed_s,
        pre_scheduler_boot_s=pre_scheduler_boot_s,
        scheduler_wall_s=scheduler_wall_s,
        entered_active_at_monotonic=result.entered_active_at_monotonic,
        first_cycle_started_at_monotonic=result.first_cycle_started_at_monotonic,
        pre_active_starved=result.pre_active_starved,
        pre_active_blocker=result.pre_loop_blocker_reason or None,
    )
    
    # Hardware limited checks
    _inline_hardware_limited = (
        result.accepted_findings == 0 and
        result.total_pattern_hits == 0 and
        result.cycles_started == 0 and
        (ctx.swap_detected_pre or ctx.uma_state_pre in ('critical', 'emergency'))
    )
    _is_hardware_limited = (
        not metrics.get('is_meaningful', False) and
        result.cycles_started == 0 and
        (ctx.swap_detected_pre or ctx.uma_state_pre in ('critical', 'emergency'))
    )
    _is_pre_active_mem_starved = (
        not metrics.get('is_meaningful', False) and
        result.cycles_started == 0 and
        result.entered_active_at_monotonic is not None and
        (ctx.swap_detected_pre or ctx.uma_state_pre in ('critical', 'emergency', 'warn'))
    )
    
    # Verdict and hint
    verdict, next_hint = _compute_verdict_and_hint(
        inp=VerdictHintInput(
            aborted=result.aborted,
            accepted_findings=result.accepted_findings,
            dup_rate=metrics['dup_rate'],
            public_pct=metrics['public_pct'],
            feed_fnd=metrics['feed_fnd'],
            hardware_limited=_inline_hardware_limited,
            public_backend_degraded=result.public_backend_degraded,
            public_discovered=result.public_discovered,
            total_pattern_hits=result.total_pattern_hits,
            public_fetched=result.public_fetched,
            stop_requested=result.stop_requested,
        )
    )
    
    # Runtime truth
    _total_ct = (result.lane_ct_accepted_findings or 0) + (result.ct_log_stored or 0)
    runtime_truth = _runtime_truth(
        inp=RuntimeTruthInput(
            actual_duration_s=actual_duration,
            query=query,
            duration_s=duration_s,
            cycles_completed=result.cycles_completed,
            cycles_started=result.cycles_started,
            accepted_findings=result.accepted_findings,
            total_pattern_hits=result.total_pattern_hits,
            public_accepted_findings=result.public_accepted_findings,
            feed_findings=metrics['feed_fnd'],
            ct_findings=_total_ct,
            swap_detected=ctx.swap_detected_pre,
            uma_state=ctx.uma_state_pre,
            branch_timeout_count=result.branch_timeout_count,
            public_branch_timed_out=result.public_branch_timed_out,
            ct_branch_timed_out=result.ct_branch_timed_out,
        )
    )
    
    is_meaningful = runtime_truth['is_meaningful']
    evidence_note = runtime_truth['evidence_note']
    
    # Update timing truth
    timing_truth['active_runtime_occurred'] = is_meaningful and time_to_windup_s > 0
    
    # Runtime truth level
    runtime_truth_level = (
        'active' if is_meaningful and result.accepted_findings > 0
        else 'pre_active_memory_starvation' if _is_pre_active_mem_starved
        else 'survival_active_minimal' if is_meaningful and ctx.uma_state_pre in ('warn', 'critical', 'emergency')
        else 'hardware_limited_smoke' if _is_hardware_limited
        else 'short_signal' if is_meaningful and result.total_pattern_hits > 0
        else 'meaningful_empty' if is_meaningful
        else 'smoke'
    )
    
    # Observed run tuple
    observed_run_tuple = build_observed_run_tuple(
        query=query,
        actual_duration=actual_duration,
        cycles_completed=result.cycles_completed,
        src_mix_str=metrics['src_mix_str'],
        runtime_truth_level=runtime_truth_level,
    )
    
    # Checkpoint category
    _public_backend = result.public_backend_degraded
    _feed_zero_check = result.accepted_findings == 0 and metrics['feed_fnd'] == 0
    _cross_branch_fail_check = (
        result.accepted_findings == 0 and
        result.total_pattern_hits > 0 and
        not _public_backend and
        not result.public_error
    )
    
    _ckpt_category, _checkpoint_zero_reason = _compute_checkpoint_category(
        inp=CheckpointInput(
            accepted_findings=result.accepted_findings,
            total_pattern_hits=result.total_pattern_hits,
            public_error=result.public_error,
            public_discovered=result.public_discovered,
            public_backend=_public_backend,
            feed_zero_check=_feed_zero_check,
            cross_branch_fail_check=_cross_branch_fail_check,
            is_pre_active_mem_starved=_is_pre_active_mem_starved,
            is_hardware_limited=_is_hardware_limited,
            is_meaningful=is_meaningful,
            uma_state_pre=ctx.uma_state_pre,
            feed_fnd=metrics['feed_fnd'],
            phase_times=ctx.phase_times,
        ),
        evidence_note=evidence_note,
    )
    
    # Export finish status
    _export_finish_status = _compute_export_finish_status(
        final_phase=result.final_phase,
        accepted_findings=result.accepted_findings,
        aborted=result.aborted,
    )
    
    # Peak UMA
    from hledac.universal._core.resource_governor import sample_uma_status
    uma_peak_gib = sample_uma_status().system_used_gib
    
    return {
        'verdict': verdict,
        'next_hint': next_hint,
        'runtime_truth': runtime_truth,
        'timing_truth': timing_truth,
        'runtime_truth_level': runtime_truth_level,
        'observed_run_tuple': observed_run_tuple,
        'ckpt_category': _ckpt_category,
        'checkpoint_zero_reason': _checkpoint_zero_reason,
        'export_finish_status': _export_finish_status,
        'uma_peak_gib': uma_peak_gib,
        'is_meaningful': is_meaningful,
        'evidence_note': evidence_note,
    }


def _compute_export_finish_status(
    final_phase: str,
    accepted_findings: int,
    aborted: bool,
) -> str:
    """Compute export finish status string."""
    if final_phase == 'SYNTHESIS' and accepted_findings > 0:
        return 'success'
    elif aborted:
        return 'aborted'
    elif final_phase == 'WINDUP':
        return 'early_windup'
    elif accepted_findings == 0:
        return 'no_findings'
    return 'unknown'


# =============================================================================
# Phase 4: Logging
# =============================================================================

def _windup_phase4_logging(
    ctx: SprintRunContext,
    result: Any,
    classifications: dict,
    metrics: dict,
) -> None:
    """Phase 4: Log runtime truth, sprint done, summary, next hint, sources, intel."""
    is_meaningful = classifications['is_meaningful']
    evidence_note = classifications['evidence_note']
    
    if is_meaningful:
        logger.info(f'[RUNTIME TRUTH] ✅ MEANINGFUL ACTIVE RUN | {evidence_note}')
    else:
        logger.warning(f'[RUNTIME TRUTH] 🚨 SMOKE ONLY | {evidence_note}')
    
    logger.info(f'[SPRINT DONE] {ctx.sprint_id} | findings: {result.accepted_findings}')
    logger.info(f"[SUMMARY] {classifications['verdict']}")
    logger.info(f"[NEXT] {classifications['next_hint']}")
    logger.info(f"[SOURCES] {metrics['src_mix_str']}")
    
    sv = ctx.intel.get('sprint_verdict') or {}
    if sv:
        logger.info(f"[INTEL] posture={sv.get('posture', '?')} | dominant={sv.get('dominant_signal', '?')}")


# =============================================================================
# Phase 5: Export
# =============================================================================

def _extract_result_fields(result: Any, export_finish_status: str) -> dict:
    """Extract result fields for export."""
    return {
        'export_finish_status': export_finish_status,
        'synthesis_success': result.accepted_findings > 0,
        'findings_deduplicated': getattr(result, 'findings_deduplicated', 0),
    }


async def _windup_phase5_export(
    ctx: SprintRunContext,
    result: Any,
    phase_durations: dict,
    acq_payload: dict,
    src_mix_str: str,
    classifications: dict,
    deep_probe_enabled: bool,
    query: str,
    actual_duration: float,
) -> None:
    """Phase 5: Export handoff and deep probe."""
    runtime_truth = classifications['runtime_truth']
    timing_truth = classifications['timing_truth']
    runtime_truth_level = classifications['runtime_truth_level']
    observed_run_tuple = classifications['observed_run_tuple']
    ckpt_category = classifications['ckpt_category']
    checkpoint_zero_reason = classifications['checkpoint_zero_reason']
    export_finish_status = classifications['export_finish_status']
    
    with _fail_safe_async('warning', 'sprint_exporter'):
        from hledac.universal.export.sprint_exporter import export_sprint
        from hledac.universal.project_types import ExportHandoff
        
        top_seed_nodes = []
        with _fail_safe_async('debug', 'get_top_seed_nodes'):
            top_seed_nodes = ctx.store.get_top_seed_nodes(n=5) if ctx.store else []
        
        # Build export handoff
        intel = ctx.intel
        scorecard = {
            'synthesis_engine_used': 'hermes3',
            'gnn_predicted_links': 0,
            'top_graph_nodes': top_seed_nodes,
            'phase_duration_seconds': phase_durations,
            'runtime_accepted_findings': (result.accepted_findings or 0) + (result.public_accepted_findings or 0),
            'findings_per_minute': round(
                ((result.accepted_findings or 0) + (result.public_accepted_findings or 0)) / (actual_duration / 60.0), 2
            ) if actual_duration > 0 else 0.0,
            'identity_candidates_found': result.identity_candidates_found,
            'identity_findings_produced': result.identity_findings_produced,
            **acq_payload,
        }
        
        canonical_run_summary = {
            'meaningful': runtime_truth['is_meaningful'],
            'primary_signal': runtime_truth['primary_signal_source'],
            'posture': (intel.get('sprint_verdict') or {}).get('posture', 'unknown'),
            'dominant_signal_path': (intel.get('signal_path') or {}).get('dominant_signal_path', 'unknown'),
            'corroborated': (intel.get('signal_path') or {}).get('is_corroborated', False),
            'is_noisy': (intel.get('signal_path') or {}).get('is_noisy', False),
            'next_pivot': (intel.get('signal_path') or {}).get('next_pivot_recommendation', 'unknown'),
            'branch_verdict': (intel.get('branch_value') or {}).get('branch_verdict', 'unknown'),
            'risk_score': (intel.get('correlation') or {}).get('risk_score', 0.0),
            'hypothesis_count': (intel.get('hypothesis_pack') or {}).get('hypothesis_count', 0),
            'first_action': (intel.get('sprint_verdict') or {}).get('first_action', ''),
            'confidence': (intel.get('sprint_verdict') or {}).get('confidence', ''),
            'runtime_truth_level': runtime_truth_level,
            'checkpoint_zero_category': ckpt_category,
            'checkpoint_zero_reason': checkpoint_zero_reason,
            'observed_run_tuple': observed_run_tuple,
            'canonical_sprint_owner': 'runtime.sprint_entrypoint.run_sprint',
            'canonical_path_used': 'run_sprint',
            'effective_source_mix': src_mix_str,
            'effective_parallelism': len(ctx.live_feed_urls),
            'effective_timeouts': {},
            'active_iteration_count': result.cycles_completed,
            **_extract_result_fields(result, export_finish_status),
            'timing_truth': timing_truth,
            **acq_payload,
        }
        
        handoff = ExportHandoff(
            sprint_id=ctx.sprint_id,
            scorecard=scorecard,
            top_nodes=top_seed_nodes,
            phase_durations=phase_durations,
            runtime_truth=runtime_truth,
            execution_context={
                'query': query,
                'requested_duration_s': ctx.duration_s,
                'actual_duration_s': round(actual_duration, 2),
                'source_count': len(ctx.live_feed_urls),
                'sources': ctx.live_feed_urls,
                'platform': {'python_version': _PLATFORM_INFO['python_version'], 'macos_version': _PLATFORM_INFO['macos_version']},
                'report_path': str(ctx.report_path),
                'git_snapshot': 'unknown',
                'export_dir': str(ctx.report_path.parent),
            },
            canonical_run_summary=canonical_run_summary,
            synthesis_outcome_payload=None,
            sprint_verdict=intel.get('sprint_verdict'),
            analyst_brief=ctx.scheduler.get_analyst_brief() if ctx.scheduler else None,
            timer_events=getattr(result, 'timer_events', None),
            uncertainty_flags=getattr(result, 'synthesis_uncertainty_flags', None) or None,
        )
        
        _elog_instance = ctx.scheduler._evidence_log.value if ctx.scheduler._evidence_log else None
        export_result = await export_sprint(
            store=ctx.store,
            handoff=handoff,
            sprint_id=ctx.sprint_id,
            evidence_log=_elog_instance,
        )
        logger.info(f"[EXPORT] finish layer → seeds={export_result.get('seeds_json', '')}")
    
    # Deep probe
    if deep_probe_enabled:
        with _fail_safe_async('warning', 'deep_probe'):
            from hledac.universal.deep_research.probe_runner import run_deep_probe_if_enabled
            probe_result = await run_deep_probe_if_enabled(
                query=query,
                store=ctx.store,
                deep_probe_enabled=True,
            )
            if probe_result:
                logger.info(f'[DEEP_PROBE] completed: {probe_result}')


# =============================================================================
# Report Building
# =============================================================================

def _build_report_dict(inp: ReportBuildInput) -> dict:
    """
    Sprint F350M-R: Build the canonical report dictionary using bundled input.
    
    Reduces function signature from 23 parameters to 1.
    """
    ctx = inp.ctx
    result = inp.result
    intel = ctx.intel
    
    result_dict = {
        'sprint_id': ctx.sprint_id,
        'query': inp.query,
        'duration_s': inp.duration_s,
        'actual_duration_s': inp.actual_duration,
        'accepted_findings': result.accepted_findings,
        'feed_findings': inp.feed_fnd,
        'public_accepted_findings': result.public_accepted_findings,
        'public_discovered': result.public_discovered,
        'public_fetched': result.public_fetched,
        'public_matched_patterns': result.public_matched_patterns,
        'public_stored_findings': result.public_stored_findings,
        'public_error': result.public_error,
        'ct_log_discovered': result.ct_log_discovered,
        'ct_log_stored': result.ct_log_stored,
        'ct_log_accepted_findings': result.ct_log_accepted_findings,
        'ct_log_error': result.ct_log_error,
        'cycles_completed': result.cycles_completed,
        'cycles_started': result.cycles_started,
        'unique_entry_hashes_seen': result.unique_entry_hashes_seen,
        'duplicate_entry_hashes_skipped': result.duplicate_entry_hashes_skipped,
        'total_pattern_hits': result.total_pattern_hits,
        'dup_rate_pct': round(inp.dup_rate, 2),
        'findings_per_min': round(inp.findings_per_min, 2),
        'final_phase': result.final_phase,
        'aborted': result.aborted,
        'abort_reason': result.abort_reason,
        'stop_requested': result.stop_requested,
        'entries_per_source': result.entries_per_source,
        'hits_per_source': result.hits_per_source,
        'export_paths': result.export_paths,
        'uma_peak_gib': inp.uma_peak_gib - ctx.uma_baseline_gib,
        'synthesis_success': result.accepted_findings > 0,
        'verdict': inp.verdict,
        'next_hint': inp.next_hint,
        'phase_timing': inp.phase_durations,
        'runtime_truth': inp.runtime_truth,
        'correlation_summary': intel.get('correlation'),
        'hypothesis_pack_summary': intel.get('hypothesis_pack'),
        'signal_path': intel.get('signal_path'),
        'feed_verdict': intel.get('feed_verdict'),
        'public_verdict': intel.get('public_verdict'),
        'branch_value': intel.get('branch_value'),
        'sprint_verdict': intel.get('sprint_verdict'),
        'execution_context': {
            'query': inp.query,
            'requested_duration_s': inp.duration_s,
            'actual_duration_s': round(inp.actual_duration, 2),
            'source_count': len(ctx.live_feed_urls),
            'sources': ctx.live_feed_urls,
            'platform': _PLATFORM_INFO.copy(),
            'report_path': str(ctx.report_path),
            'git_snapshot': 'unknown',
            'export_dir': str(ctx.report_path.parent),
        },
    }
    
    # Canonical run summary
    crs = {
        'meaningful': inp.runtime_truth['is_meaningful'],
        'primary_signal': inp.runtime_truth['primary_signal_source'],
        'posture': (intel.get('sprint_verdict') or {}).get('posture', 'unknown'),
        'dominant_signal_path': (intel.get('signal_path') or {}).get('dominant_signal_path', 'unknown'),
        'corroborated': (intel.get('signal_path') or {}).get('is_corroborated', False),
        'is_noisy': (intel.get('signal_path') or {}).get('is_noisy', False),
        'next_pivot': (intel.get('signal_path') or {}).get('next_pivot_recommendation', 'unknown'),
        'branch_verdict': (intel.get('branch_value') or {}).get('branch_verdict', 'unknown'),
        'risk_score': (intel.get('correlation') or {}).get('risk_score', 0.0),
        'hypothesis_count': (intel.get('hypothesis_pack') or {}).get('hypothesis_count', 0),
        'first_action': (intel.get('sprint_verdict') or {}).get('first_action', ''),
        'confidence': (intel.get('sprint_verdict') or {}).get('confidence', ''),
        'runtime_truth_level': inp.runtime_truth_level,
        'checkpoint_zero_category': inp.ckpt_category,
        'checkpoint_zero_reason': inp.checkpoint_zero_reason,
        'observed_run_tuple': inp.observed_run_tuple,
        'canonical_sprint_owner': 'runtime.sprint_entrypoint.run_sprint',
        'canonical_path_used': 'run_sprint',
        'effective_source_mix': inp.src_mix_str,
        'effective_parallelism': len(ctx.live_feed_urls),
        'effective_timeouts': {},
        'active_iteration_count': result.cycles_completed,
        **_extract_result_fields(result, inp.export_finish_status),
        'timing_truth': inp.timing_truth,
        'early_exit_class': getattr(result, 'early_exit_class', ''),
        'early_exit_reason': getattr(result, 'early_exit_reason', ''),
        'requested_duration_s': inp.duration_s,
        'actual_duration_s': round(inp.actual_duration, 2),
        'elapsed_pct': round(inp.actual_duration / inp.duration_s * 100, 1) if inp.duration_s > 0 else 0.0,
        'active_window_budget_s': inp.timing_truth['active_window_budget_s'],
        'active_window_elapsed_s': inp.timing_truth['time_to_windup_s'],
        'governor_uma_state': getattr(result, 'governor_uma_state', ''),
        'governor_system_used_gib': getattr(result, 'governor_system_used_gib', 0.0),
        'governor_swap_detected': getattr(result, 'governor_swap_detected', False),
        'governor_io_only': getattr(result, 'governor_io_only', False),
    }
    result_dict['canonical_run_summary'] = crs
    result_dict.update(inp.acq_payload_filtered)
    result_dict['timing_truth'] = inp.timing_truth
    result_dict['gc_telemetry'] = {}
    result_dict['nonfeed_mission_active'] = getattr(result, 'nonfeed_mission_active', False)
    result_dict['nonfeed_required_families'] = getattr(result, 'nonfeed_required_families', ())
    result_dict['nonfeed_optional_families'] = getattr(result, 'nonfeed_optional_families', ())
    result_dict['nonfeed_family_status'] = getattr(result, 'nonfeed_family_status', {})
    result_dict['nonfeed_all_required_terminal'] = getattr(result, 'nonfeed_all_required_terminal', False)
    result_dict['nonfeed_any_accepted'] = getattr(result, 'nonfeed_any_accepted', False)
    result_dict['nonfeed_provider_failures'] = getattr(result, 'nonfeed_provider_failures', ())
    result_dict['nonfeed_memory_skips'] = getattr(result, 'nonfeed_memory_skips', ())
    result_dict['nonfeed_mission_exit_reason'] = getattr(result, 'nonfeed_mission_exit_reason', '')
    
    # DuckDB stats
    if ctx.store:
        result_dict['duckdb_stats'] = getattr(ctx.store, 'get_stats', lambda: {})()
    
    # Rust extensions
    try:
        _ext_mod = __import__('hledac.universal._core.rust_backend', fromlist=['rust']).rust.raw.module
        if _ext_mod is not None:
            _stat_collector = __import__('core.rust_backend.stats', fromlist=['']).StatCollector
            result_dict['rust_extensions'] = _stat_collector().collect(_ext_mod)
        else:
            result_dict['rust_extensions'] = {}
    except Exception:
        result_dict['rust_extensions'] = {}
    
    # Evidence manifest
    if ctx.evidence_log is not None:
        result_dict['evidence_manifest'] = {
            'total_count': ctx.evidence_log.size,
            'ram_size': ctx.evidence_log.ram_size,
            'persist_path': str(ctx.evidence_log.persist_path) if ctx.evidence_log.persist_path else None,
        }
    else:
        result_dict['evidence_manifest'] = {'total_count': 0, 'ram_size': 0, 'persist_path': None, 'note': 'elog_not_wired'}
    
    return result_dict


# =============================================================================
# Main Windup Phase
# =============================================================================

async def _run_sprint_windup(
    ctx: SprintRunContext,
    query: str,
    duration_s: float,
    export_dir: str,
    deep_probe_enabled: bool,
) -> None:
    """
    PHASE REFACTORING F350M-R: WINDUP phase - result processing, report generation, export.
    
    Delegates to extracted helper functions for each phase of windup processing.
    
    Args:
        ctx: SprintRunContext
        query: Sprint query string
        duration_s: Requested sprint duration
        export_dir: Export directory
        deep_probe_enabled: Enable deep probe
    """
    from hledac.universal.paths import get_sprint_json_report_path
    
    ctx.phase_times['WINDUP'] = time.monotonic()
    result = ctx.result
    actual_duration = ctx.phase_times.get('TEARDOWN', ctx.phase_times['WINDUP']) - ctx.phase_times['BOOT']
    
    # Phase 1: Early windup
    await _windup_phase1_early(ctx, query, actual_duration)
    
    # Phase 2: Timing
    _phase_durations = _windup_phase2_timing(ctx, duration_s)
    
    # Phase 3a: Metrics
    metrics = _windup_phase3_compute_metrics(ctx, result, actual_duration)
    
    # Phase 3b: Classifications
    classifications = _windup_phase3_compute_classifications(ctx, result, metrics, query, duration_s)
    
    # Phase 4: Logging
    _windup_phase4_logging(ctx, result, classifications, metrics)
    
    # Build report
    ctx.report_path = get_sprint_json_report_path(ctx.sprint_id)
    _acq_payload = _scheduler_result_acquisition_payload(result, ctx.scheduler, query, duration_s)
    _acq_payload_filtered = {k: v for k, v in _acq_payload.items() if k != 'source_family_outcomes'}
    
    report_dict = _build_report_dict(
        inp=ReportBuildInput(
            query=query,
            duration_s=duration_s,
            actual_duration=actual_duration,
            feed_fnd=metrics['feed_fnd'],
            dup_rate=metrics['dup_rate'],
            findings_per_min=metrics['findings_per_min'],
            public_pct=metrics['public_pct'],
            src_mix_str=metrics['src_mix_str'],
            verdict=classifications['verdict'],
            next_hint=classifications['next_hint'],
            phase_durations=_phase_durations,
            runtime_truth=classifications['runtime_truth'],
            timing_truth=classifications['timing_truth'],
            runtime_truth_level=classifications['runtime_truth_level'],
            observed_run_tuple=classifications['observed_run_tuple'],
            ckpt_category=classifications['ckpt_category'],
            checkpoint_zero_reason=classifications['checkpoint_zero_reason'],
            export_finish_status=classifications['export_finish_status'],
            uma_peak_gib=classifications['uma_peak_gib'],
            ctx=ctx,
            result=result,
            acq_payload_filtered=_acq_payload_filtered,
        )
    )
    
    ctx.report_path.write_bytes(_serialize_report(report_dict))
    logger.info(f'[REPORT] {ctx.report_path}')
    
    # Phase 5: Export
    await _windup_phase5_export(
        ctx=ctx,
        result=result,
        phase_durations=_phase_durations,
        acq_payload=_acq_payload,
        src_mix_str=metrics['src_mix_str'],
        classifications=classifications,
        deep_probe_enabled=deep_probe_enabled,
        query=query,
        actual_duration=actual_duration,
    )


# =============================================================================
# Acquisition Payload (imported from original)
# =============================================================================

def _scheduler_result_acquisition_payload(
    result: 'SprintSchedulerResult',  # type: ignore[name-defined] # Issue #9: type hint fix
    scheduler: Any,
    query: str,
    duration_s: float,
) -> dict:
    """
    Build acquisition report payload from SprintSchedulerResult.

    ISSUE #9 FIX:
    - Uses AcqReportPayload (msgspec.Struct) for type conversion
    - Proper type hints for result parameter
    - Lazy imports for heavy modules (M1 8GB optimization)

    Args:
        result: SprintSchedulerResult from sprint execution
        scheduler: Sprint scheduler instance
        query: Query string
        duration_s: Actual sprint duration

    Returns:
        Dictionary with acquisition report and telemetry data
    """
    # Lazy imports for M1 8GB optimization
    import msgspec

    from hledac.universal.runtime.acquisition_strategy import (
        ACQUISITION_REPORT_SCHEMA_VERSION,
        normalize_source_family_outcome,
        canonicalize_source_family_outcomes,
        complete_source_family_outcomes_from_lane_details,
        reconcile_lane_detail_fields,
        build_acquisition_report,
    )
    from hledac.universal.runtime.acquisition_telemetry_reconcile import complete_source_family_outcomes_from_prelude
    from hledac.universal.utils.config_introspection import safe_attr_get

    # Import AcqReportPayload from types (Issue #9 fix)
    from ..types import AcqReportPayload as _AcqReportPayload

    try:
        r = msgspec.convert(result, _AcqReportPayload)
    except Exception as _conv_exc:
        logger.exception('[Issue9] msgspec.convert(SprintSchedulerResult->AcqReportPayload) failed: %s', _conv_exc)
        # Issue #9 fix: Use keyword args for msgspec.Struct construction
        r = _AcqReportPayload()
    
    sfo_list = _build_sfo_list(r)
    se_dict = {
        'exit_path': r.scheduler_exit_path,
        'exit_reason': r.scheduler_exit_reason,
        'exit_phase': r.scheduler_exit_phase,
        'exit_cycle': r.scheduler_exit_cycle,
        'exit_elapsed_s': r.scheduler_exit_elapsed_s,
        'exit_guard_checked': r.scheduler_exit_guard_checked,
        'exit_guard_satisfied': r.scheduler_exit_guard_satisfied,
    }
    rg_dict = {
        'return_guard_checked': r.return_guard_checked,
        'return_guard_satisfied': r.return_guard_satisfied,
        'return_guard_block_reason': r.return_guard_block_reason,
        'return_guard_attempted_lanes': r.return_guard_attempted_lanes,
        'return_guard_skipped_lanes': r.return_guard_skipped_lanes,
        'return_guard_errors': r.return_guard_errors,
        'return_guard_delayed_for_nonfeed': r.return_guard_delayed_for_nonfeed,
    }
    wg_dict = {
        'windup_guard_call_count': r.windup_guard_call_count,
        'windup_guard_callback_supplied_count': r.windup_guard_callback_supplied_count,
        'windup_guard_callback_executed_count': r.windup_guard_callback_executed_count,
        'windup_guard_required_lanes': r.windup_guard_required_lanes,
        'windup_guard_not_applicable': r.windup_guard_not_applicable,
        'windup_guard_last_reason': r.windup_guard_last_reason,
        'windup_guard_last_allowed': r.windup_guard_last_allowed,
        'windup_guard_callback_not_executed_reason': r.windup_guard_last_callback_not_executed_reason,
    }
    pwb = {
        'checked': r.prewindup_barrier_checked,
        'satisfied': r.prewindup_barrier_satisfied,
        'required_lanes': r.prewindup_barrier_required_lanes,
        'attempted_lanes': r.prewindup_barrier_attempted_lanes,
        'skipped_lanes': r.prewindup_barrier_skipped_lanes,
        'errors': r.prewindup_barrier_errors,
        'duration_s': r.prewindup_barrier_duration_s,
        'windup_delayed': r.windup_delayed_for_nonfeed,
        'nonfeed_scheduler_gap_resolved': getattr(result, 'nonfeed_scheduler_gap_resolved', False),
    }
    
    term_rep: dict = r.acquisition_terminality_report or {}
    plan = getattr(scheduler, '_acquisition_plan', None)
    nd_raw = getattr(plan, 'nonfeed_plan_debug', None) if plan else None
    cfg = getattr(scheduler, '_config', None)
    cfg_profile = safe_attr_get(cfg, 'acquisition_profile', None) if cfg else None
    profile_from_nd = getattr(nd_raw, 'acquisition_profile', None) if nd_raw else None
    acq_effective = profile_from_nd or cfg_profile or 'default'
    
    nd: dict | None = _extract_nonfeed_debug_fields(nd_raw)
    _acq_profile = safe_attr_get(nd, 'acquisition_profile', 'default') if nd else acq_effective or 'default'
    _feed_cap_reason = nd.get('feed_cap_reason') if nd else None
    _nonfeed_priority_enabled = nd.get('nonfeed_priority_enabled', False) if nd else acq_effective == 'nonfeed_diagnostic'
    _nonfeed_profile_expected_lanes = nd.get('nonfeed_profile_expected_lanes', []) if nd else ['CT', 'WAYBACK', 'PASSIVE_DNS', 'PIVOT_EXECUTOR', 'DOH'] if acq_effective in ('nonfeed_diagnostic', 'deep_osint_m1') else []
    
    try:
        from hledac.universal.runtime.acquisition_strategy import build_acquisition_report
        _acq_report = build_acquisition_report(
            plan=plan,
            terminality=term_rep,
            nonfeed_plan_debug=nd,
            source_family_outcomes=sfo_list,
            return_guard=rg_dict,
            prewindup_barrier=pwb,
            scheduler_exit=se_dict,
            windup_guard_observation=wg_dict,
            query=query,
            acquisition_profile=_acq_profile,
            feed_cap_reason=_feed_cap_reason,
            nonfeed_priority_enabled=_nonfeed_priority_enabled,
            nonfeed_profile_expected_lanes=_nonfeed_profile_expected_lanes,
            public_terminal_stage=r.public_terminal_stage,
            public_stage_counters=r.public_stage_counters,
            public_discovery_empty_reason=r.public_discovery_empty_reason,
            public_discovery_debug_reason=r.public_discovery_debug_reason,
            public_provider_selection_debug=r.public_provider_selection_debug or {},
            ct_provider_status=r.ct_provider_status,
            ct_cache_used=r.ct_cache_used,
            ct_cache_stale=r.ct_cache_stale,
            ct_cache_age_s=r.ct_cache_age_s,
            ct_quarantine_count=r.ct_quarantine_count,
            ct_quarantine_samples=r.ct_quarantine_samples,
            ct_planned=r.ct_planned,
            ct_scheduled=r.ct_scheduled,
            ct_provider_selected=r.ct_provider_selected,
            ct_request_attempted=r.ct_request_attempted,
            ct_request_timeout=r.ct_request_timeout,
            ct_raw_count=r.ct_raw_count,
            ct_bridge_invoked=r.ct_bridge_invoked,
            ct_candidates_built=r.ct_candidates_built,
            ct_storage_attempted=r.ct_storage_attempted,
            ct_storage_accepted=r.ct_storage_accepted,
            ct_terminal_stage=r.ct_terminal_stage,
            ct_prelude_missing_but_final_attempted=r.ct_prelude_missing_but_final_attempted,
            quality_rejection_summary_by_family=r.quality_rejection_summary_by_family,
            duplicate_rejection_summary_by_family=r.duplicate_rejection_summary_by_family,
            low_information_by_family=r.low_information_by_family,
            nonfeed_candidate_ledger_summary=r.nonfeed_candidate_ledger_summary,
            feed_dominance_budget=getattr(plan, 'feed_dominance_budget', None) if plan else None,
            doh_planned=r.doh_planned,
            doh_scheduled=r.doh_scheduled,
            doh_request_attempted=r.doh_request_attempted,
            doh_domains_attempted=r.doh_domains_attempted,
            doh_raw_count=r.doh_raw_count,
            doh_accepted_findings=r.doh_accepted_findings,
            doh_terminal_stage=r.doh_terminal_stage,
            doh_provider_errors=r.doh_provider_errors,
            doh_cache_used=r.doh_cache_used,
            nonfeed_expected_lanes=r.nonfeed_expected_lanes,
            nonfeed_missing_expected_lanes=r.nonfeed_missing_expected_lanes,
            wayback_terminal_state=r.wayback_terminal_state,
            passive_dns_terminal_state=r.passive_dns_terminal_state,
            nonfeed_surface_complete=getattr(result, 'nonfeed_surface_complete', False),
            pivot_seed_domains=r.pivot_seed_domains,
            pivot_seed_ips=r.pivot_seed_ips,
            pivot_seed_urls=r.pivot_seed_urls,
            pivot_seed_hashes=r.pivot_seed_hashes,
            pivot_seed_cves=r.pivot_seed_cves,
            seed_context_available=bool(r.pivot_seed_domains or r.pivot_seed_ips or r.pivot_seed_urls or r.pivot_seed_hashes or r.pivot_seed_cves),
            seed_context_propagated=r.seed_context_propagated,
            lanes_unlocked_by_seed_context=r.lanes_unlocked_by_seed_context,
            acquisition_plan_build_failed=r.acquisition_plan_build_failed,
            acquisition_plan_build_error_type=r.acquisition_plan_build_error_type,
            acquisition_plan_build_error=r.acquisition_plan_build_error,
            acquisition_plan_present_for_prelude=r.acquisition_plan_present_for_prelude,
            acquisition_plan_lanes_for_prelude=r.acquisition_plan_lanes_for_prelude,
            acquisition_plan_enabled_lanes_for_prelude=r.acquisition_plan_enabled_lanes_for_prelude,
            acquisition_plan_profile_for_prelude=r.acquisition_plan_profile_for_prelude,
            acquisition_plan_build_error_for_prelude=r.acquisition_plan_build_error_for_prelude,
            nonfeed_prelude_enabled=r.nonfeed_prelude_enabled,
            nonfeed_prelude_expected_lanes=r.nonfeed_prelude_expected_lanes,
            nonfeed_prelude_attempted_lanes=r.nonfeed_prelude_attempted_lanes,
            nonfeed_prelude_terminal_lanes=r.nonfeed_prelude_terminal_lanes,
            nonfeed_prelude_missing_lanes=r.nonfeed_prelude_missing_lanes,
            nonfeed_prelude_error_by_lane=r.nonfeed_prelude_error_by_lane,
            nonfeed_prelude_accepted_by_lane=r.nonfeed_prelude_accepted_by_lane,
            nonfeed_prelude_duration_s=r.nonfeed_prelude_duration_s,
            nonfeed_prelude_feed_blocked_until_complete=r.nonfeed_prelude_feed_blocked_until_complete,
        )
        _acq_report['acquisition_profile_input'] = None
        _acq_report['acquisition_profile_effective'] = acq_effective
        _acq_report['acquisition_profile_normalized'] = False
        _acq_report['budget_violations'] = r.budget_violations
        _acq_report['return_guard_block_reason'] = r.return_guard_block_reason or ''
        _acq_report['ct_quarantine_count'] = r.ct_quarantine_count
        _acq_report['ct_quarantine_samples'] = r.ct_quarantine_samples
        _acq_report = reconcile_lane_detail_fields(_acq_report)
        _acq_report = complete_source_family_outcomes_from_lane_details(_acq_report)
        _acq_report = complete_source_family_outcomes_from_prelude(_acq_report)
        _acq_report = _normalize_seed_context(_acq_report, r)
    except Exception as _exc:
        logger.exception('[Issue9-FALLBACK] build_acquisition_report raised: %s', _exc)
        _acq_report = msgspec.to_builtins(r)
        _acq_report.update(
            schema_version=f'{ACQUISITION_REPORT_SCHEMA_VERSION}-fallback',
            fallback_reason=f'canonical_build_failed: {_exc}',
            acquisition_report_fallback_used=True,
            terminality=term_rep,
            source_family_outcomes=sfo_list,
            return_guard=rg_dict,
            prewindup_barrier=pwb,
            scheduler_exit=se_dict,
            windup_guard_observation=wg_dict,
            nonfeed_plan_debug=nd,
            plan=getattr(plan, 'plans', None) if plan else None,
            prelude_plan=getattr(plan, 'plans', []) if plan else [],
            required_lane_plan=term_rep.get('required_lanes', []) if term_rep else [],
            runtime_attempted_lanes=[o.family for o in sfo_list if o.attempted and o.family],
            effective_acquisition_plan=list(set(term_rep.get('required_lanes', []) if term_rep else []) | {o.family for o in sfo_list if o.attempted and o.family}),
            plan_semantics='effective_runtime' if any((o.attempted for o in sfo_list)) else 'prelude_only',
        )
        _acq_report['acquisition_profile_input'] = None
        _acq_report['acquisition_profile_effective'] = acq_effective
        _acq_report['acquisition_profile_normalized'] = False
        _acq_report['budget_violations'] = r.budget_violations
        _acq_report['return_guard_block_reason'] = r.return_guard_block_reason or ''
        _acq_report['ct_quarantine_count'] = r.ct_quarantine_count
        _acq_report['ct_quarantine_samples'] = r.ct_quarantine_samples
        _acq_report = reconcile_lane_detail_fields(_acq_report)
        _acq_report = complete_source_family_outcomes_from_lane_details(_acq_report)
        _acq_report = complete_source_family_outcomes_from_prelude(_acq_report)
        _acq_report = _normalize_seed_context(_acq_report, r)
    
    return {
        'acquisition_report': _acq_report,
        'acquisition_terminality_checked': r.acquisition_terminality_checked,
        'acquisition_terminality_satisfied': r.acquisition_terminality_satisfied,
        'acquisition_terminality_missing_lanes': r.acquisition_terminality_missing_lanes,
        'acquisition_terminality_report': term_rep,
        'source_family_outcomes': sfo_list,
        'scheduler_exit': se_dict,
        'return_guard': rg_dict,
        'windup_guard_observation': wg_dict,
        'prewindup_barrier': pwb,
        'acquisition_prelude_checked': r.acquisition_prelude_checked,
        'acquisition_prelude_ran': r.acquisition_prelude_ran,
        'acquisition_prelude_required_lanes': r.acquisition_prelude_required_lanes,
        'acquisition_prelude_terminal_lanes': r.acquisition_prelude_terminal_lanes,
        'acquisition_prelude_missing_lanes': r.acquisition_prelude_missing_lanes,
        'acquisition_prelude_skipped_lanes': r.acquisition_prelude_skipped_lanes,
        'acquisition_prelude_errors': r.acquisition_prelude_errors,
        'acquisition_prelude_duration_s': r.acquisition_prelude_duration_s,
        'acquisition_prelude_reason': r.acquisition_prelude_reason,
        'early_exit_class': r.early_exit_class,
        'early_exit_reason': r.early_exit_reason,
        'requested_duration_s': r.requested_duration_s,
        'actual_duration_s': r.actual_duration_s,
        'elapsed_pct': r.elapsed_pct,
        'active_window_budget_s': r.active_window_budget_s,
        'active_window_elapsed_s': r.active_window_elapsed_s,
    }


def _build_sfo_list(r: AcqReportPayload) -> list:
    """Build source_family_outcomes list from AcqReportPayload (msgspec.Struct)."""
    from hledac.universal.runtime.acquisition_strategy import normalize_source_family_outcome
    
    sfo_list: list = []
    if r.accepted_findings > 0 or r.total_pattern_hits > 0:
        sfo_list.append(normalize_source_family_outcome('FEED', {
            'family': 'FEED',
            'attempted': True,
            'skipped': False,
            'skip_reason': None,
            'raw_count': r.total_pattern_hits,
            'built_count': 0,
            'accepted_count': r.accepted_findings,
            'error': None,
            'timeout': False,
            'duration_s': None,
        }))
    
    pub_pts = r.public_terminal_stage
    pub_fetch_attempted = bool(r.public_stage_counters and r.public_stage_counters.get('fetch_attempted', 0) > 0)
    pub_has_outcome = bool(
        r.public_discovered > 0 or
        r.public_accepted_findings > 0 or
        (pub_pts and pub_pts != 'NOT_SCHEDULED') or
        r.public_error or
        pub_fetch_attempted
    )
    if pub_has_outcome:
        sfo_list.append(normalize_source_family_outcome('PUBLIC', {
            'family': 'PUBLIC',
            'attempted': True,
            'skipped': False,
            'skip_reason': None,
            'raw_count': r.public_discovered,
            'built_count': 0,
            'accepted_count': r.public_accepted_findings,
            'error': r.public_error or r.public_terminal_stage or None,
            'timeout': r.public_terminal_stage == 'DISCOVERY_TIMEOUT',
            'duration_s': None,
        }))
    
    ct_has_outcome = bool(
        r.ct_log_discovered > 0 or
        r.ct_log_accepted_findings > 0 or
        r.ct_terminal_stage or
        r.ct_log_error or
        r.ct_planned or
        r.ct_scheduled or
        r.ct_request_attempted or
        r.ct_provider_status
    )
    if ct_has_outcome:
        sfo_list.append(normalize_source_family_outcome('CT', {
            'family': 'CT',
            'attempted': bool(r.ct_request_attempted or r.ct_scheduled or r.ct_planned),
            'skipped': False,
            'skip_reason': None,
            'raw_count': r.ct_log_discovered,
            'built_count': 0,
            'accepted_count': r.ct_log_accepted_findings,
            'error': r.ct_log_error or r.ct_terminal_stage or None,
            'timeout': r.ct_terminal_stage == 'request_timeout',
            'duration_s': None,
        }))
    
    lanes_seen: set = set()
    for _o in r.acquisition_lane_outcomes or ():
        if not hasattr(_o, 'lane'):
            continue
        _lane = _o.lane
        if _lane in lanes_seen:
            continue
        lanes_seen.add(_lane)
        sfo_list.append(normalize_source_family_outcome(
            getattr(_o, 'source_family', _lane.upper()),
            {
                'family': getattr(_o, 'source_family', _lane.upper()),
                'attempted': getattr(_o, 'attempted', False),
                'skipped': not getattr(_o, 'attempted', False),
                'skip_reason': None if getattr(_o, 'attempted', False) else 'lane_not_attempted',
                'raw_count': getattr(_o, 'ct_results_raw', 0),
                'built_count': getattr(_o, 'ct_candidates_built', 0),
                'accepted_count': getattr(_o, 'accepted_findings', 0),
                'error': getattr(_o, 'error', None),
                'timeout': getattr(_o, 'timeout', False),
                'duration_s': getattr(_o, 'duration_s', None),
            }
        ))
    
    return canonicalize_source_family_outcomes(sfo_list)


def _extract_nonfeed_debug_fields(nd_raw: Any | None) -> dict | None:
    """Extract all nonfeed_plan_debug fields safely."""
    if nd_raw is None:
        return None
    return {
        'domain_detected': getattr(nd_raw, 'domain_detected', False),
        'wallet_detected': getattr(nd_raw, 'wallet_detected', False),
        'enabled_nonfeed_lanes': getattr(nd_raw, 'enabled_nonfeed_lanes', ()) or (),
        'disabled_nonfeed_lanes': getattr(nd_raw, 'disabled_nonfeed_lanes', ()) or (),
        'disabled_reasons': getattr(nd_raw, 'disabled_reasons', ()) or (),
        'scheduled_nonfeed_lanes': getattr(nd_raw, 'scheduled_nonfeed_lanes', ()) or (),
        'hardware_skipped_lanes': getattr(nd_raw, 'hardware_skipped_lanes', ()) or (),
        'nonfeed_execution_scheduled': getattr(nd_raw, 'nonfeed_execution_scheduled', False),
        'nonfeed_execution_skip_reason': getattr(nd_raw, 'nonfeed_execution_skip_reason', None),
        'acquisition_profile': getattr(nd_raw, 'acquisition_profile', 'default'),
        'feed_cap_reason': getattr(nd_raw, 'feed_cap_reason', None),
        'nonfeed_priority_enabled': getattr(nd_raw, 'nonfeed_priority_enabled', False),
        'nonfeed_profile_expected_lanes': getattr(nd_raw, 'nonfeed_profile_expected_lanes', ()) or (),
    }


def _normalize_seed_context(report: dict, r: AcqReportPayload) -> dict:
    """
    Normalize seed context fields from AcqReportPayload.

    Args:
        report: The report dictionary to update
        r: AcqReportPayload (msgspec.Struct) from conversion

    Returns:
        Updated report dictionary
    """
    if not report.get('seed_context_available'):
        has_seeds = r.pivot_seed_domains or r.pivot_seed_ips or r.pivot_seed_urls or r.pivot_seed_hashes or r.pivot_seed_cves
        if has_seeds:
            report['seed_context_available'] = True
            report['seed_context_propagated'] = r.seed_context_propagated
            if not report.get('seed_context_skip_reason'):
                report['seed_context_skip_reason'] = ''
        elif not report.get('seed_context_skip_reason'):
            report['seed_context_skip_reason'] = 'no_runtime_pivot_seeds'
    return report


# =============================================================================
# Export AcqReportPayload for external use (Issue #9)
# =============================================================================
# Note: AcqReportPayload is imported from ..types and re-exported here
# for backward compatibility with any code importing from this module
