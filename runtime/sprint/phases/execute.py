"""
runtime/sprint/phases/execute.py — Sprint execute phase

F350M-R: EXECUTE phase - scheduler setup, sprint race, execution.

Handles:
- Phase 1: Scheduler configuration, creation, BlitzGC, V2Init
- Phase 2: Health check and production guard
- Phase 3: Dashboard setup
- Phase 4: Scheduler vs cancel race with cooperative shutdown
- Phase 5: Memory poller stop and lock release

Usage:
    await _run_sprint_execute(ctx=ctx, query=query, duration_s=duration_s, ...)
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import time
from typing import TYPE_CHECKING, Any

from ..types import SprintRunContext, SprintFlags
from ..cleanup import _fail_safe_async

if TYPE_CHECKING:
    from hledac.universal.runtime.scheduler_v2 import SprintSchedulerV2

logger = logging.getLogger(__name__)


# =============================================================================
# Scheduler Setup
# =============================================================================

def _get_live_feed_urls() -> list[str]:
    """
    Return canonical runtime feed URLs for live sprint path.

    Uses get_runtime_feed_seeds() from rss_atom_adapter — the single source
    of truth for the runtime RSS/Atom feed surface.
    """
    from hledac.universal.discovery.rss_atom_adapter import get_runtime_feed_seeds
    return [seed.feed_url for seed in get_runtime_feed_seeds()]


async def _execute_scheduler_setup(
    ctx: SprintRunContext,
    query: str,
    duration_s: float,
    aggressive_mode: bool,
    deep_research: bool,
    extreme_mode: bool,
    acquisition_profile: str | None,
    flags: SprintFlags | None,
    rl_train_mode: bool,
    export_dir: str,
) -> None:
    """Phase 1: Scheduler configuration, creation, BlitzGC, V2Init."""
    from hledac.universal.runtime.scheduler_config import SprintSchedulerConfig
    from hledac.universal.runtime.scheduler_v2 import SprintSchedulerV2
    from hledac.universal.runtime.scheduler_v2._v2_init import V2Init
    import os
    
    # Normalize acquisition profile
    _acq_input = acquisition_profile
    _acq_effective = acquisition_profile
    if _acq_effective == 'nonfeed_diagnostic180':
        _acq_effective = 'nonfeed_diagnostic'
    if _acq_effective not in ('default', 'nonfeed_diagnostic', 'deep_osint_m1'):
        logger.warning("[F228A] Unknown acquisition_profile=%r normalized to 'default'", _acq_input)
        _acq_effective = 'default'
    if 'HLEDAC_ACQUISITION_PROFILE' not in os.environ:
        os.environ['HLEDAC_ACQUISITION_PROFILE'] = _acq_effective or 'default'
    acquisition_profile = _acq_effective or 'default'
    
    # Create scheduler config
    config = SprintSchedulerConfig(
        sprint_duration_s=float(duration_s),
        windup_lead_s=ctx.effective_windup_s,
        export_enabled=True,
        export_dir=export_dir,
        aggressive_mode=aggressive_mode,
        branch_timeout_budget_s=8.0 if aggressive_mode else 0.0,
        acquisition_profile=acquisition_profile,
        deep_research_enabled=deep_research,
        extreme_mode=extreme_mode,
    )
    
    # Blitz mode
    _blitz = getattr(flags, 'blitz_mode', False) if flags else False
    if _blitz:
        from hledac.universal._core.telemetry.context_state import set_blitz_mode as _set_blitz
        _set_blitz(True)
        logger.info('[BLITZ-12] Blitz mode enabled')
        from hledac.universal.fetching.public_fetcher import reset_blitz_dead_hosts
        reset_blitz_dead_hosts()
    
    # Create scheduler
    ctx.scheduler = SprintSchedulerV2(_config=config, _flags=flags)
    
    # BlitzGC
    with _fail_safe_async('debug', 'blitz_gc.sprint_start'):
        from hledac.universal.coordinators.resource.blitz_gc import blitz_gc
        _blitz_telemetry = blitz_gc.sprint_start()
        logger.info('[PHYSICS-06] BlitzGC active — GC disabled for active sprint window')
    
    # V2Init
    _wall_clock_start = ctx.phase_times.get('WARMUP', ctx.phase_times['BOOT'])
    _init = V2Init(ctx.scheduler)
    await _init.run(
        query,
        _wall_clock_start,
        ctx=None,
        cancel_event=ctx.cancel_event,
        flags=flags,
        sprint_id=ctx.sprint_id,
        sprint_duration_s=float(duration_s),
        windup_lead_s=ctx.effective_windup_s,
        duckdb_store=ctx.store,
        rl_train_mode=rl_train_mode,
        logger=logger,
        resume_from=ctx.resume_from,
        resume_step=ctx.resume_step,
        query_hash=ctx.query_hash,
    )
    
    ctx.evidence_log = ctx.scheduler._evidence_log.value if ctx.scheduler._evidence_log else None


# =============================================================================
# Pre-flight Checks
# =============================================================================

async def _execute_preflight_checks(
    ctx: SprintRunContext,
    flags: SprintFlags | None,
) -> Any:
    """Phase 2: Health check and production guard."""
    health = None
    try:
        async with asyncio.timeout(30.0):
            health = await ctx.scheduler.health_check()
    except TimeoutError:
        logger.warning('[F228F] health_check timed out after 30s')
    
    if health is not None and not health.overall_ok:
        logger.warning(f'[F228F] health_check warnings: {health.summary()}')
    elif health is not None:
        logger.debug(f'[F228F] health_check: {health.summary()}')
    
    # Production guard
    if (flags.production if flags else False) and health is not None and not health.fetch_coordinator_ok:
        logger.error('[F272B] --production pre-flight ABORT: fetch coordinator not_initialized')
        sys.exit(2)
    
    # CT log client init
    ctx.ct_log_client = None
    with _fail_safe_async('debug', 'ct_log_client.init'):
        from hledac.universal.intel.ct_log_client import CTLogClient
        from pathlib import Path
        _ct_cache = Path.home() / '.hledac' / 'ct_cache'
        _ct_cache.mkdir(parents=True, exist_ok=True)
        ctx.ct_log_client = CTLogClient(cache_dir=_ct_cache)
    
    return health


# =============================================================================
# Dashboard
# =============================================================================

async def _execute_dashboard(
    ctx: SprintRunContext,
    ui_mode: bool,
    query: str,
    duration_s: float,
) -> None:
    """Phase 3: Dashboard setup."""
    if ui_mode:
        with _fail_safe_async('warning', 'dashboard.create'):
            from hledac.universal.monitoring.sprint_dashboard import SprintDashboard
            ctx.dashboard = SprintDashboard(ctx.sprint_id, query, duration_s)
            ctx.dashboard.start()


# =============================================================================
# Sprint Race
# =============================================================================

def _make_cycle_callback(dashboard: Any) -> Any:
    """
    Factory for progress callback that updates dashboard.

    Replaces closure capture of _dashboard with explicit parameter passing.
    All exceptions are swallowed fail-soft — dashboard must never block sprint.
    """
    def callback(result: Any, phase: str, elapsed_s: float) -> None:
        if dashboard is not None:
            try:
                dashboard.update(result, phase, elapsed_s)
            except Exception:
                pass
    return callback


async def _execute_sprint_race(ctx: SprintRunContext, query: str) -> None:
    """Phase 4: Scheduler vs cancel race with cooperative shutdown."""
    from hledac.universal.utils.asyncx import first_completed, safe_create_task
    from hledac.universal.utils.mlx_cache import start_memory_status_poller, stop_memory_status_poller
    
    # Setup cycle callback
    _make_cycle_callback(ctx.dashboard)
    
    # Start memory poller
    with _fail_safe_async('debug', 'memory_status_poller.start'):
        await start_memory_status_poller(interval_s=0.5)
    
    # Race between scheduler and cancel event
    _cancel_waiter = safe_create_task(ctx.cancel_event.wait(), eager_start=True)
    _scheduler_waiter = safe_create_task(ctx.scheduler.run(query), eager_start=True)
    
    try:
        _first_result, winner_task = await first_completed(_scheduler_waiter, _cancel_waiter)
    except asyncio.TimeoutError:
        raise
    
    if winner_task is _scheduler_waiter and winner_task.done():
        ctx.result = _first_result
        _cancel_waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _cancel_waiter
    else:
        _scheduler_waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _scheduler_waiter
        from hledac.universal.runtime.scheduler_result import SprintSchedulerResult
        _sf = SprintSchedulerResult()
        _sf.scheduler_exit_path = 'cooperative_shutdown'
        _sf.scheduler_exit_reason = 'SIGINT/SIGTERM received via shutdown_event'
        ctx.result = _sf


# =============================================================================
# Cleanup
# =============================================================================

async def _execute_cleanup(ctx: SprintRunContext) -> None:
    """Phase 5: Memory poller stop and lock release."""
    # Stop memory poller
    with _fail_safe_async('debug', 'memory_status_poller.stop'):
        from hledac.universal.utils.mlx_cache import stop_memory_status_poller
        await stop_memory_status_poller()
    
    # Release sprint lock
    if ctx.sprint_lock_mgr is not None:
        with _fail_safe_async('debug', 'sprint_lock.release'):
            ctx.sprint_lock_mgr.release()
            logger.debug('[F266-LOCK] Released sprint lock')


# =============================================================================
# Main Execute Phase
# =============================================================================

async def _run_sprint_execute(
    ctx: SprintRunContext,
    query: str,
    duration_s: float,
    aggressive_mode: bool,
    deep_research: bool,
    extreme_mode: bool,
    acquisition_profile: str | None,
    flags: SprintFlags | None,
    rl_train_mode: bool,
    ui_mode: bool,
    export_dir: str,
) -> None:
    """
    PHASE REFACTORING F350M-R: EXECUTE phase - scheduler setup, sprint race, execution.
    
    Delegates to extracted helper functions for scheduler setup, pre-flight checks,
    dashboard, and the scheduler race.
    
    Args:
        ctx: SprintRunContext
        query: Sprint query string
        duration_s: Requested sprint duration
        aggressive_mode: Enable aggressive mode
        deep_research: Enable deep research
        extreme_mode: Enable extreme mode
        acquisition_profile: Acquisition profile
        flags: SprintFlags bundle
        rl_train_mode: Enable RL training
        ui_mode: Enable UI mode
        export_dir: Export directory
    """
    # Phase 1: Scheduler setup
    await _execute_scheduler_setup(
        ctx=ctx,
        query=query,
        duration_s=duration_s,
        aggressive_mode=aggressive_mode,
        deep_research=deep_research,
        extreme_mode=extreme_mode,
        acquisition_profile=acquisition_profile,
        flags=flags,
        rl_train_mode=rl_train_mode,
        export_dir=export_dir,
    )
    
    # Phase 2: Pre-flight checks
    health = await _execute_preflight_checks(ctx, flags)
    
    # Get live feed URLs
    ctx.live_feed_urls = _get_live_feed_urls()
    
    # Phase 3: Dashboard
    await _execute_dashboard(ctx, ui_mode, query, duration_s)
    
    # Phase 4: Sprint race
    await _execute_sprint_race(ctx, query)
    
    # Phase 5: Cleanup
    await _execute_cleanup(ctx)
