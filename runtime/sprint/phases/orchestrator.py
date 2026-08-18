"""
runtime/sprint/phases/orchestrator.py — Sprint orchestrator

F350M-R: Thin orchestrator for run_sprint.

This function delegates all work to extracted phase functions:
- _run_sprint_boot: Pre-flight, init, lock acquisition
- _run_sprint_execute: Scheduler setup, sprint race, execution
- _run_sprint_windup: Result processing, report generation, export
- _run_sprint_teardown: Resource cleanup

ROLE: CANONICAL SPRINT OWNER — SOLE production sprint authority.
All report truth surfaces (canonical_run_summary, runtime_truth, timing_truth,
checkpoint_zero_category, observed_run_tuple) are derived here.

Usage:
    await run_sprint(query="LockBit ransomware", duration_s=1800)
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ..types import SprintRunContext, SprintFlags
from ..context import SprintContextManager, set_current_sprint_context
from .boot import _run_sprint_boot
from .execute import _run_sprint_execute
from .windup import _run_sprint_windup
from .teardown import _run_sprint_teardown

logger = logging.getLogger(__name__)

# Try to setup telemetry
try:
    from hledac.universal.runtime._telemetry_setup import configure
    configure()
except Exception:
    pass

try:
    from otel import instrumented as _otel_instrumented
except ImportError:
    from hledac.universal.otel import instrumented as _otel_instrumented


@_otel_instrumented('sprint.run', component='cli')
async def run_sprint(
    query: str,
    duration_s: float = 1800.0,
    export_dir: str = str(Path.home() / '.hledac' / 'reports'),
    aggressive_mode: bool = False,
    deep_probe_enabled: bool = False,
    deep_research: bool = False,
    extreme_mode: bool = False,
    _no_communication: bool = False,
    ui_mode: bool = False,
    windup_lead_s: float | None = None,
    acquisition_profile: str | None = None,
    rl_train_mode: bool = False,
    force: bool = False,
    flags: SprintFlags | None = None,
    shutdown_event: asyncio.Event | None = None,
    resume: bool = True,
    prng_seed: int | None = None,
    replay_seed: int | None = None,
    warc_dir: str | None = None,
) -> None:
    """
    PHASE REFACTORING F350M-R: Thin orchestrator for run_sprint.
    
    This function now delegates all work to extracted phase functions:
    - _run_sprint_boot: Pre-flight, init, lock acquisition
    - _run_sprint_execute: Scheduler setup, sprint race, execution
    - _run_sprint_windup: Result processing, report generation, export
    - _run_sprint_teardown: Resource cleanup
    
    ROLE: CANONICAL SPRINT OWNER — SOLE production sprint authority.
    All report truth surfaces (canonical_run_summary, runtime_truth, timing_truth,
    checkpoint_zero_category, observed_run_tuple) are derived here.
    
    Args:
        query: Sprint query string
        duration_s: Requested sprint duration (default 1800s = 30min)
        export_dir: Directory for export files
        aggressive_mode: Enable aggressive acquisition mode
        deep_probe_enabled: Enable deep probe research
        deep_research: Enable deep research advisory
        extreme_mode: Enable exhaustive depth for deep research
        _no_communication: Skip CommunicationLayer injection
        ui_mode: Enable UI dashboard
        windup_lead_s: Optional windup lead time override
        acquisition_profile: Acquisition profile (default | nonfeed_diagnostic | deep_osint_m1)
        rl_train_mode: Enable QMIX training mode
        force: Override pre-flight guards
        flags: SprintFlags bundle
        shutdown_event: External shutdown event
        resume: Enable checkpoint recovery
        prng_seed: Explicit PRNG seed
        replay_seed: Replay seed for deterministic replay
        warc_dir: WARC archive directory for replay
    """
    ctx = SprintRunContext()
    sprint_ctx_manager = SprintContextManager()
    set_current_sprint_context(sprint_ctx_manager)
    
    # Warning for replay mode without WARC dir
    if replay_seed is not None and warc_dir is None:
        logger.warning('[ULTIMATE-001] Replay mode without --warc-dir: live HTTP fetching will be used instead of WARC responses')
    
    try:
        # Start per-sprint resources
        await sprint_ctx_manager.start()
        ctx.denorm_buffer = sprint_ctx_manager.denorm_buffer
        ctx.session_tracker = sprint_ctx_manager.session_tracker
        ctx.duckpgq_graph = sprint_ctx_manager.duckpgq_graph
        
        # BOOT: Pre-flight, init, lock acquisition
        await _run_sprint_boot(
            ctx=ctx,
            query=query,
            duration_s=duration_s,
            windup_lead_s=windup_lead_s,
            flags=flags,
            force=force,
            aggressive_mode=aggressive_mode,
            resume=resume,
            prng_seed=prng_seed,
            replay_seed=replay_seed,
        )
        
        # EXECUTE: Scheduler setup, sprint race, execution
        await _run_sprint_execute(
            ctx=ctx,
            query=query,
            duration_s=duration_s,
            aggressive_mode=aggressive_mode,
            deep_research=deep_research,
            extreme_mode=extreme_mode,
            acquisition_profile=acquisition_profile,
            flags=flags,
            rl_train_mode=rl_train_mode,
            ui_mode=ui_mode,
            export_dir=export_dir,
        )
        
        # WINDUP: Result processing, report generation, export
        await _run_sprint_windup(
            ctx=ctx,
            query=query,
            duration_s=duration_s,
            export_dir=export_dir,
            deep_probe_enabled=deep_probe_enabled,
        )
        
    except asyncio.CancelledError:
        logger.info('[run_sprint] Sprint cancelled — running teardown')
        raise
    finally:
        # TEARDOWN: Resource cleanup (always runs, even on cancellation)
        await _run_sprint_teardown(ctx)
        await sprint_ctx_manager.stop()
        set_current_sprint_context(None)
