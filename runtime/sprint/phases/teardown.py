"""
runtime/sprint/phases/teardown.py — Sprint teardown phase

F350M-R: TEARDOWN phase - resource cleanup.

Handles all cleanup operations:
- _teardown_power_and_tasks: Power assertion + task cancellation
- _teardown_scheduler: Scheduler shutdown
- _teardown_duckdb: DuckDB maintenance
- _teardown_evidence_log: EvidenceLog teardown
- _teardown_transports: HTTP client shutdown
- _teardown_cleanup: Ephemeral wipe, GC, checkpoints, lock

Usage:
    await _run_sprint_teardown(ctx)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ..types import SprintRunContext
from ..cleanup import _fail_safe_async

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

logger = logging.getLogger(__name__)


# =============================================================================
# Power & Tasks
# =============================================================================

async def _teardown_power_and_tasks(ctx: SprintRunContext) -> None:
    """Release power assertion and cancel orphan tasks."""
    # Release power assertion
    if ctx.power_assertion is not None:
        ctx.power_assertion.release()
        logger.info('[TEARDOWN] Power assertion released')
    
    # Cancel orphan tasks
    from hledac.universal.utils.asyncx import cancel_scope_drain
    count = await cancel_scope_drain(timeout=5.0, label='orphan_drain')
    if count > 0:
        logger.debug('[SPRINT] Cancelled and drained %d orphan tasks', count)


# =============================================================================
# Dashboard
# =============================================================================

async def _teardown_dashboard(ctx: SprintRunContext) -> None:
    """Finish dashboard display."""
    if ctx.dashboard is not None:
        with _fail_safe_async('warning', 'dashboard.finish'):
            elapsed_s = time.monotonic() - ctx.phase_times['BOOT']
            ctx.dashboard.finish(ctx.result, elapsed_s)


# =============================================================================
# Scheduler
# =============================================================================

async def _teardown_scheduler(ctx: SprintRunContext) -> None:
    """Shutdown scheduler (F285 - Metal, LMDB, Hermes, transports)."""
    if ctx.scheduler is not None:
        with _fail_safe_async('debug', 'scheduler.aclose'):
            await ctx.scheduler.aclose(timeout_s=10.0)


# =============================================================================
# DuckDB
# =============================================================================

async def _teardown_duckdb(ctx: SprintRunContext) -> None:
    """DuckDB teardown maintenance (BLITZ-07/09)."""
    if ctx.store is not None:
        with _fail_safe_async('debug', 'duckdb.teardown'):
            ctx.store.set_maintenance_disabled_during_active(False)
            await ctx.store.run_teardown_maintenance()
            ctx.store.set_journal_active_optimized(False)
            await ctx.store.run_journal_teardown()


# =============================================================================
# Evidence Log
# =============================================================================

async def _teardown_evidence_log(ctx: SprintRunContext) -> None:
    """Close EvidenceLog and DuckDBStore in parallel."""
    _core_close_targets: list[Any] = []
    
    # Evidence log
    _elog = getattr(ctx.scheduler, '_evidence_log', None)
    if _elog is not None and _elog.value is not None:
        _core_close_targets.append(_elog.value)
    
    # DuckDB store
    if ctx.store is not None:
        _core_close_targets.append(ctx.store)
    
    # Parallel close
    if _core_close_targets:
        with _fail_safe_async('debug', 'parallel_close.core'):
            from hledac.universal.utils.asyncx import parallel_close
            _core_close_errors = await parallel_close(_core_close_targets, concurrency=2, ctx='teardown.core')
            for _err in _core_close_errors:
                if _err is not None:
                    logger.debug(f'[TEARDOWN] Resource close error: {_err}')


# =============================================================================
# Transports
# =============================================================================

async def _teardown_transports(ctx: SprintRunContext) -> None:
    """Close all HTTP clients in parallel."""
    with _fail_safe_async('debug', 'parallel_close_async.transports'):
        from hledac.universal.utils.asyncx import parallel_close_async
        from hledac.universal.transport.httpx_client import close_httpx_client_async
        from hledac.universal.transport.curl_cffi_runtime import close_curl_cffi_sessions_async
        from hledac.universal.fetching.public_fetcher import close_public_fetcher_sessions_async
        from hledac.universal.network.session_runtime import close_aiohttp_session_async
        
        _transport_close_errors = await parallel_close_async(
            [
                ('httpx', close_httpx_client_async),
                ('curl_cffi', close_curl_cffi_sessions_async),
                ('public_fetcher', close_public_fetcher_sessions_async),
                ('aiohttp', close_aiohttp_session_async),
            ],
            concurrency=4,
            ctx='teardown.transports',
        )
        
        failed_transports = [name for name, exc in _transport_close_errors.items() if exc is not None]
        if failed_transports:
            logger.debug(f'[TEARDOWN] transport close failures: {failed_transports}')


# =============================================================================
# Cleanup
# =============================================================================

async def _teardown_cleanup(ctx: SprintRunContext) -> None:
    """Final cleanup: ephemeral wipe, GC, checkpoints, lock."""
    # Ephemeral wipe
    with _fail_safe_async('debug', 'ephemeral_wipe'):
        from hledac.universal.security.ephemeral_wipe import EphemeralStateAnnihilator
        _wipe_result = await EphemeralStateAnnihilator().annihilate()
        if _wipe_result.get('buffers_wiped', 0) > 0 or _wipe_result.get('munlock_count', 0) > 0:
            logger.debug(f"[ADVERSARY-005] ephemeral wipe: buffers={_wipe_result['buffers_wiped']}")
    
    # GC cycle
    with _fail_safe_async('debug', 'gc_cycle_maintain'):
        from hledac.universal._core import memory_cycle
        await asyncio.to_thread(memory_cycle.gc_cycle_maintain, force=False)
    
    # ToT checkpoints cleanup
    if ctx.store is not None:
        with _fail_safe_async('debug', 'tot_checkpoint.cleanup'):
            from hledac.universal.coordinators.tot_checkpointer import TransactionalToTCheckpointer
            _cleanup_ckpt = TransactionalToTCheckpointer(
                sprint_id=ctx.sprint_id,
                duckdb_store=ctx.store,
                interval_s=30.0,
                lmdb_incremental=True,
                fs_fallback=True,
            )
            await _cleanup_ckpt.cleanup()
            logger.debug('[UNIFIED-007] ToT checkpoints cleaned up')
    
    # Sprint lock release
    if ctx.sprint_lock_mgr is not None:
        with _fail_safe_async('debug', 'sprint_lock.release'):
            ctx.sprint_lock_mgr.release()
            logger.debug('[F266-LOCK] Released sprint lock')


# =============================================================================
# Main Teardown Phase
# =============================================================================

async def _run_sprint_teardown(ctx: SprintRunContext) -> None:
    """
    PHASE REFACTORING F350M-R: TEARDOWN phase - resource cleanup.

    Handles all cleanup operations via extracted helpers:
    - _teardown_power_and_tasks: Power assertion + task cancellation
    - _teardown_scheduler: Scheduler shutdown
    - _teardown_duckdb: DuckDB maintenance
    - _teardown_evidence_log: EvidenceLog teardown
    - _teardown_transports: HTTP client shutdown
    - _teardown_cleanup: Ephemeral wipe, GC, checkpoints, lock

    Args:
        ctx: SprintRunContext with all resources
    """
    await _teardown_power_and_tasks(ctx)
    await _teardown_dashboard(ctx)
    await _teardown_scheduler(ctx)
    await _teardown_duckdb(ctx)
    await _teardown_evidence_log(ctx)
    await _teardown_transports(ctx)
    await _teardown_cleanup(ctx)
