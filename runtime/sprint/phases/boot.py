"""
runtime/sprint/phases/boot.py — Sprint boot phase

F350M-R: BOOT phase - pre-flight guards, initialization, lock acquisition.

Handles:
- Pre-flight guards (UMA, active window check)
- Seed state generation (ULTIMATE-001)
- Sprint lock acquisition (F266-LOCK)
- DuckDB initialization with concurrent circuit breaker reset
- ToT checkpoint recovery (UNIFIED-006)

Usage:
    await _run_sprint_boot(ctx=ctx, query=query, duration_s=duration_s, ...)
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import time
from typing import TYPE_CHECKING, Any

from ..types import SprintRunContext, SprintFlags
from ..cleanup import _fail_safe_async, _cleanup_stale_locks
from ..delta_writer import write_sprint_delta

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

logger = logging.getLogger(__name__)

# =============================================================================
# Pre-flight Guards
# =============================================================================

MIN_ACTIVE_WINDOW_S: int = 30


def _run_sprint_preflight_guards(
    logger: logging.Logger,
    duration_s: float,
    windup_lead_s: float | None,
    flags: 'SprintFlags | None',
    force: bool,
) -> tuple[float, Any]:
    """
    F360: Extracted pre-flight guard checks from run_sprint().

    Performs all config validation that MUST run before DuckDB init to avoid
    orphaned lock files. Uses sys.exit(2) for config errors.

    Args:
        logger: Logger for messages
        duration_s: Requested sprint duration
        windup_lead_s: Optional windup lead override
        flags: SprintFlags bundle
        force: Force override flag
        
    Returns:
        tuple of (effective_windup_s, uma_pre_sprint)
    """
    from hledac.universal._core.resource_governor import sample_uma_status, HARD_BLOCK_SWAP_GIB, CLEAN_SWAP_MAX_GIB
    from hledac.universal._core.feature_flags import FeatureFlag, FeatureFlags
    
    _force_override = (flags.force if flags else False) or force
    _effective_windup_s = windup_lead_s if windup_lead_s is not None else 180.0
    _active_window_s = float(duration_s) - _effective_windup_s
    
    # F271C: Negative active window check
    if _active_window_s < 0.0:
        logger.error(
            '[F271C-INVARIANT] computed negative active_window_s=%s (duration_s=%s, effective_windup_s=%s). F221 guard is broken.',
            _active_window_s, duration_s, _effective_windup_s
        )
        sys.exit(2)
    
    # F289: Windup percentage check
    if _effective_windup_s >= _active_window_s * 0.8:
        _pct = _effective_windup_s / _active_window_s * 100 if _active_window_s > 0 else 100.0
        if _force_override:
            logger.warning(
                '[F289-FORCED] Windup %.0fs would consume %.0f%% of active window %.0fs. Proceeding due to --force.',
                _effective_windup_s, _pct, _active_window_s
            )
        else:
            logger.error(
                '[F289-ABORT] Windup %.0fs would consume %.0f%% of active window %.0fs. Reduce windup (--windup-lead) or increase duration.',
                _effective_windup_s, _pct, _active_window_s
            )
            sys.exit(2)
    
    # F221: Minimum active window check
    if _active_window_s < float(MIN_ACTIVE_WINDOW_S):
        _required_duration_s = int(_effective_windup_s + float(MIN_ACTIVE_WINDOW_S))
        if _force_override:
            logger.warning(
                '[F221-FORCED] duration=%ds gives only %ds active window (windup_lead_effective=%ds). Proceeding due to --force.',
                int(duration_s), max(0, int(_active_window_s)), int(_effective_windup_s)
            )
        else:
            logger.error(
                '[F221-ABORT] Sprint duration %ds gives only %ds active window (windup_lead_effective=%ds). Minimum recommended: --duration %d. Use --force to override.',
                int(duration_s), max(0, int(_active_window_s)), int(_effective_windup_s), _required_duration_s
            )
            sys.exit(2)
    
    # F289: Windup fraction warning
    if windup_lead_s is not None:
        _windup_fraction = float(windup_lead_s) / float(duration_s)
        if _windup_fraction > 0.9:
            logger.warning(
                '[F289-WINDUP-FRACTION] windup_lead_s=%.0fs is %.0f%% of duration=%ds. This may cause the sprint to enter WINDUP immediately, leaving almost no time for active acquisition.',
                int(windup_lead_s), _windup_fraction * 100, int(duration_s)
            )
    
    # Remote debug disable
    try:
        from hledac.universal._core.feature_flags import FeatureFlag, FeatureFlags
        if FeatureFlags.get(FeatureFlag.REMOTE_DEBUG_DISABLE):
            import os
            if os.environ.get('PYTHON_DISABLE_REMOTE_DEBUG') != '1':
                sys.exit('HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED=1 but PYTHON_DISABLE_REMOTE_DEBUG not set — OSINT runtime requires external debugger disabled')
    except ImportError:
        pass
    
    _uma_pre_sprint = sample_uma_status()
    return (_effective_windup_s, _uma_pre_sprint)


# =============================================================================
# DuckDB Init
# =============================================================================

async def _duckdb_init_coro(store: 'DuckDBShadowStore', logger: logging.Logger) -> bool:
    """
    DuckDB async init coroutine — extracted to module level.

    Module-level placement avoids creating a new coroutine factory on every
    run_sprint() call (closure capture anti-pattern).

    Args:
        store: DuckDBShadowStore instance to initialize.
        logger: Logger for warning messages on failure.

    Returns:
        True on success, False on failure (fail-soft, caller handles retry).
    """
    try:
        await store.async_initialize()
        return True
    except Exception as _init_err:
        logger.warning(f'[P0-3] DuckDB pre-init failed (fail-soft, store will init on first ingest): {_init_err}')
        return False


# =============================================================================
# Circuit Breaker Reset
# =============================================================================

def _sync_reset_circuit_breakers(logger: logging.Logger) -> bool:
    """Reset warmup counters on all domain circuit breakers."""
    try:
        from hledac.universal.transport.circuit_breaker import _BREAKERS
        for breaker in _BREAKERS.values():
            breaker.mark_warmup_done()
        return True
    except Exception as _exc:
        logger.warning(f'[P0-3] Circuit breaker reset failed: {_exc}')
        return False


async def _reset_circuit_breakers_async(logger: logging.Logger) -> bool:
    """Reset warmup counters on all domain circuit breakers — O(n) where n<100)."""
    try:
        async with asyncio.timeout(10.0):
            return await asyncio.to_thread(_sync_reset_circuit_breakers, logger)
    except TimeoutError:
        logger.warning('[P0-3] Circuit breaker reset timed out after 10s — continuing')
        return False
    except asyncio.CancelledError:
        raise


# =============================================================================
# ToT Recovery
# =============================================================================

async def _attempt_tot_recovery(
    store: Any,
    query_hash: str,
    logger: logging.Logger,
) -> tuple[dict | None, int]:
    """
    Attempt ToT checkpoint recovery from prior run.
    
    Args:
        store: DuckDB store
        query_hash: Query hash for lookup
        logger: Logger
        
    Returns:
        tuple of (orphan_nodes dict, orphan_step int)
    """
    try:
        if not hasattr(store, 'get_latest_orphan') or query_hash == '':
            return (None, 0)
        _orphan_row = await store.get_latest_orphan(query_hash)
        if not _orphan_row:
            return (None, 0)
        _orphan_sprint_id, _orphan_step, _tree_json_str, _ts, _stored_checksum = _orphan_row
        import hashlib
        _raw = _tree_json_str.encode('utf-8')
        _computed = hashlib.blake2b(_raw, digest_size=32).hexdigest()
        if _computed != _stored_checksum:
            logger.error('[UNIFIED-006] Checksum mismatch — checkpoint corrupt, starting fresh')
            return (None, 0)
        import orjson
        _envelope = orjson.loads(_raw)
        _raw_nodes = _envelope.get('nodes', {})
        _nodes: dict[str, Any] = {}
        for _nid, _ndata in _raw_nodes.items():
            try:
                _nodes[_nid] = {
                    'node_id': _ndata.get('node_id', _nid),
                    'thought': _ndata.get('thought', ''),
                    'value_estimate': _ndata.get('value_estimate', 0.0),
                    'parent': _ndata.get('parent'),
                    'children': _ndata.get('children', []),
                    'visited': _ndata.get('visited', False),
                    'expanded': _ndata.get('expanded', False),
                    'depth': _ndata.get('depth', 0),
                    'cost': _ndata.get('cost', 0.0),
                    'uncertainty': _ndata.get('uncertainty', 0.0),
                }
            except Exception:
                pass
        if _nodes:
            logger.warning(
                '[UNIFIED-006] 🔄 RESUMING ToT from checkpoint: orphan_sprint=%s step=%d nodes=%d',
                _orphan_sprint_id[:12], _orphan_step, len(_nodes)
            )
            return (_nodes, _orphan_step)
        else:
            logger.warning('[UNIFIED-006] Checkpoint found but all nodes failed deserialization')
            return (None, 0)
    except Exception:
        pass
    return (None, 0)


# =============================================================================
# Pre-warm Utilities
# =============================================================================

def _prewarm_services() -> None:
    """Pre-warm pattern matcher and prewarm daemon."""
    from hledac.universal.runtime.prewarm_daemon import start_prewarm_if_needed
    from hledac.universal.utils.patterns.pattern_matcher import prewarm as prewarm_patterns
    start_prewarm_if_needed()
    prewarm_patterns()


# =============================================================================
# GC Configuration
# =============================================================================

_gc_configured: bool = False


def _configure_gc_for_sprint() -> dict:
    """
    Configure Python GC for sprint workload.

    PHYSICS-06/07: Compatibility wrapper — delegates to BlitzGCStrategy.
    The canonical GC lifecycle is managed by BlitzGCStrategy:
      - sprint_start() disables GC during active acquisition
      - sprint_teardown() re-enables GC at winddown

    Called once at sprint boot. Returns a dict with telemetry fields.
    Opt-out via HLEDAC_DISABLE_GC_FREEZE=1.
    """
    global _gc_configured
    if _gc_configured:
        return {'delegated_to': 'BlitzGCStrategy', 'already_configured': True}
    try:
        from hledac.universal.coordinators.resource.blitz_gc import blitz_gc as _bgc
        _result = _bgc.sprint_start()
        _gc_configured = True
        return {
            'delegated_to': 'BlitzGCStrategy',
            'blitz_active': _result.get('blitz_active', False),
            'freeze_method': _result.get('freeze_method', 'none'),
            'blitz_thresholds': _result.get('blitz_thresholds'),
            'startup_snapshot_count': _result.get('startup_snapshot_count', 0),
        }
    except Exception as _exc:
        logger.debug('[GC] BlitzGCStrategy delegation failed (non-fatal): %s', _exc)
        _gc_configured = True
        return {'delegated_to': 'BlitzGCStrategy', 'error': str(_exc)}


# =============================================================================
# Main Boot Phase
# =============================================================================

def _make_sprint_id() -> str:
    """Generate collision-resistant sprint ID using ns timestamp + short uuid suffix."""
    import time
    import uuid
    ts = time.time_ns() // 1000000
    uid = uuid.uuid7().hex[:6]
    return f'8sa_{ts}_{uid}'


def _query_fingerprint(query: str) -> str:
    """Generate query fingerprint for checkpoint recovery."""
    from hledac.universal.utils.hashing import query_fingerprint as _qfp
    return _qfp(query)


async def _run_sprint_boot(
    ctx: SprintRunContext,
    query: str,
    duration_s: float,
    windup_lead_s: float | None,
    flags: SprintFlags | None,
    force: bool,
    aggressive_mode: bool,
    resume: bool,
    prng_seed: int | None,
    replay_seed: int | None,
) -> None:
    """
    PHASE REFACTORING F350M-R: BOOT phase - pre-flight guards, initialization, lock acquisition.

    Extracted from run_sprint() to reduce cognitive complexity from ~240 to ~30.
    
    Args:
        ctx: SprintRunContext to populate with boot state
        query: Sprint query string
        duration_s: Requested sprint duration
        windup_lead_s: Optional windup lead time override
        flags: SprintFlags bundle
        force: Override pre-flight guards
        aggressive_mode: Aggressive mode flag
        resume: Enable checkpoint recovery
        prng_seed: Optional explicit PRNG seed
        replay_seed: Optional replay seed for deterministic replay
    """
    ctx.phase_times['BOOT'] = time.monotonic()
    ctx.cancel_event = asyncio.Event()
    
    # Pre-flight guards
    _effective_windup_s, _uma_pre_sprint = _run_sprint_preflight_guards(
        logger=logger,
        duration_s=duration_s,
        windup_lead_s=windup_lead_s,
        flags=flags,
        force=force,
    )
    ctx.effective_windup_s = _effective_windup_s
    ctx.uma_baseline_gib = _uma_pre_sprint.system_used_gib
    ctx.swap_detected_pre = _uma_pre_sprint.swap_detected
    ctx.uma_state_pre = _uma_pre_sprint.state
    
    # Sprint ID and query hash
    ctx.sprint_id = _make_sprint_id()
    ctx.query_hash = _query_fingerprint(query) if resume else ''
    
    # Seed state generation
    ctx.seed_state = _generate_seed_state(query, prng_seed, replay_seed)
    
    # Warmup phase timing
    ctx.phase_times['WARMUP'] = time.monotonic()
    
    # Power assertion
    from hledac.universal.runtime.power_assertion import PowerAssertion
    ctx.power_assertion = PowerAssertion.acquire(reason=f'sprint_{ctx.sprint_id}')
    logger.info('[PRE-LOOP] Power assertion acquired (method=%s) — sleep prevented', ctx.power_assertion.method)
    
    # Sprint lock acquisition
    await _acquire_sprint_lock(ctx, query)
    
    # DuckDB store creation
    from hledac.universal.knowledge.duckdb_store import make_shadow_store
    ctx.store = make_shadow_store()
    
    # Pre-warm services
    _prewarm_services()
    
    # ISSUE-009: Concurrent DuckDB init and circuit breaker reset using parallel(taskgroup=True)
    # This enables asyncio.TaskGroup with eager_start=True (Python 3.12+) for faster init.
    _cb_reset_coro = _reset_circuit_breakers_async(logger)
    from hledac.universal.utils.asyncx import parallel
    with contextlib.suppress(asyncio.CancelledError):
        _init_result = await parallel(
            [
                _duckdb_init_coro(ctx.store, logger),
                _cb_reset_coro,
            ],
            policy="collect",
            taskgroup=True,  # ISSUE-009: Enable TaskGroup with eager_start
            ctx="boot:_subsystem_init",
        )
    
    if _init_result and _init_result.ok:
        _duckdb_result = _init_result.ok[0] if _init_result.ok else None
        ctx.duckdb_init_ok = not isinstance(_duckdb_result, Exception) and _duckdb_result
        if not ctx.duckdb_init_ok:
            logger.warning(f'[P0-3] DuckDB pre-init failed (fail-soft): {_duckdb_result}')
    
    # ToT checkpoint recovery
    if ctx.duckdb_init_ok and resume:
        ctx.resume_from, ctx.resume_step = await _attempt_tot_recovery(
            ctx.store, ctx.query_hash, logger
        )


def _generate_seed_state(
    query: str,
    prng_seed: int | None,
    replay_seed: int | None,
) -> Any:
    """Generate or load seed state for deterministic replay."""
    from hledac.universal.runtime.sprint_types import SprintSeedState
    from ..context import set_sprint_seed_state
    
    if replay_seed is not None:
        seed_state = SprintSeedState.generate(query=query, explicit_seed=replay_seed)
        logger.info('[ULTIMATE-001] Replay mode: seed=%d, tot_iv=%s', seed_state.prng_seed, seed_state.tot_iv[:8])
    elif prng_seed is not None:
        seed_state = SprintSeedState.generate(query=query, explicit_seed=prng_seed)
        logger.info('[ULTIMATE-001] Using explicit seed=%d, tot_iv=%s', seed_state.prng_seed, seed_state.tot_iv[:8])
    else:
        seed_state = SprintSeedState.generate(query=query)
        logger.info('[ULTIMATE-001] Generated seed=%d, tot_iv=%s (use --seed %d for deterministic replay)', 
                   seed_state.prng_seed, seed_state.tot_iv[:8], seed_state.prng_seed)
    
    set_sprint_seed_state(seed_state)
    return seed_state


async def _acquire_sprint_lock(ctx: SprintRunContext, query: str) -> None:
    """Acquire sprint lock for this query."""
    import sys
    from hledac.universal._core.graph_lock_manager import GraphLockManager
    from hledac.universal.paths import get_sprint_lock_path
    
    ctx.sprint_lock_path = get_sprint_lock_path(query)
    _janitor_removed = _cleanup_stale_locks(ctx.sprint_lock_path.parent, logger)
    
    try:
        ctx.sprint_lock_mgr = GraphLockManager(str(ctx.sprint_lock_path))
        if not ctx.sprint_lock_mgr.acquire(timeout_s=5.0):
            _holder = ctx.sprint_lock_mgr.holder_pid
            logger.error(f"[F266-LOCK-ABORT] Sprint with query '{query}' already running (PID={_holder})")
            sys.exit(2)
        logger.debug(f'[F266-LOCK] Acquired sprint lock: {ctx.sprint_lock_path}')
    except Exception as _lock_err:
        logger.warning(f'[F266-LOCK] Could not acquire sprint lock (continuing): {_lock_err}')
