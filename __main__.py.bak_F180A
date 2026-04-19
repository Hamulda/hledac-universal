"""
Hledac Universal - Async Entry Point
====================================

Sprint 8AI: Boot Hygiene Closure
- AsyncExitStack as unified teardown backbone
- 8AG LMDB boot guard as FIRST boot step
- LIFO teardown order for existing surfaces
- Signal-safe teardown (no direct cleanup in signal handler)
- Graceful task cancellation before loop close
- CheckpointManager: N/A (AO-coupled only)

Usage:
    python -m hledac.universal [--benchmark]

No CLI arguments are required for normal operation.
Benchmark mode activates internal probe tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import pathlib
import signal
import sys
import time
from typing import Any, Callable, Dict, List, Optional

# Sprint 8VC: Exclude legacy/ from Python path to prevent accidental imports
# legacy/ is for reference only — active code must not import from it
sys.path = [p for p in sys.path if not p.endswith("/legacy")]

# Sprint 0B: uvloop MUST be installed before any async operations
_uvloop_installed = False
try:
    import uvloop
    uvloop.install()
    _uvloop_installed = True
    logging.info("[RUNTIME] uvloop installed successfully")
except ImportError:
    # Fail-open: fall back to default asyncio loop
    logging.warning("[RUNTIME] uvloop not available, using default asyncio loop")

# =============================================================================
# Sprint F177D: Canonical Owner Freeze / Alternate Entrypoint Verdict
# =============================================================================
# Hardened authority story — no new world, no new runtime, no new framework.
# Labels are READ-ONLY; zero branching logic changes.
#
# Role taxonomy:
#   canonical  — sole production sprint owner. All report truth flows from here.
#   shell      — CLI dispatch only. Calls canonical or alternate, never owns sprint state.
#   alternate  — legacy production path. Not canonical owner. Use for migration only.
#   residual   — shared helper path. Owned by multiple callers. Not a sprint owner.
#   diagnostic — probe/benchmark only. Not for production sprints.
#
# F177D sharpening:
#   - "canonical" is now the ONLY production sprint owner role
#   - root main() is shell_only (never sprint owner, only dispatcher)
#   - _run_sprint_mode is confirmed alternate (NOT canonical)
#   - run_warmup is confirmed residual (shared helper, NOT lifecycle owner)
#   - _run_public_passive_once is confirmed alternate (NOT canonical)
#   - _run_observed_default_feed_batch_once is confirmed diagnostic (probe only)
#   - ENTRYPOINT_AUTHORITY is the single source of truth for role labeling
# =============================================================================

ENTRYPOINT_AUTHORITY = {
    # Sole canonical sprint owner — all report truth, timing truth, export truth
    # flow from this function. Every sprint that matters uses this path.
    "canonical_sprint_owner": "hledac.universal.core.__main__.run_sprint",
    # Root role: shell/dispatcher — main() reads args, delegates to canonical or alternate.
    # main() is NEVER a sprint owner. It only dispatches.
    "root_role": "shell/dispatcher surface — main() dispatches, never owns sprint state",
    "alternate_paths": {
        "_run_sprint_mode": {
            "location": "hledac.universal.__main__._run_sprint_mode",
            "role": "alternate",
            "non_canonical": True,
            "allowed_purpose": (
                "F162C legacy sprint hot-path. "
                "Owns full lifecycle state locally. "
                "Canonical owner is core.__main__.run_sprint(). "
                "Use only during migration; do not add new call-sites."
            ),
            "owner_status": "not_canonical — owns lifecycle state but NOT the canonical report boundary",
        },
        "_run_public_passive_once": {
            "location": "hledac.universal.__main__._run_public_passive_once",
            "role": "alternate",
            "non_canonical": True,
            "allowed_purpose": (
                "F162C public-discovery-only pass. "
                "Runs full pipeline without canonical lifecycle. "
                "Not a sprint owner. Use for public-branch probe only."
            ),
            "owner_status": "not_canonical — bypasses canonical lifecycle, owns no report boundary",
        },
        "run_warmup": {
            "location": "hledac.universal.__main__.run_warmup",
            "role": "residual",
            "non_canonical": True,
            "allowed_purpose": (
                "WARMUP orchestration shared by _run_sprint_mode and canonical path. "
                "Isolates pre-ACTIVE setup (DuckPGQ, IOCScorer, ring buffers, ANE warmup). "
                "NOT a sprint owner — called by both canonical and alternate paths."
            ),
            "owner_status": "residual — shared helper, called by both canonical and alternate",
        },
        "_run_observed_default_feed_batch_once": {
            "location": "hledac.universal.__main__._run_observed_default_feed_batch_once",
            "role": "diagnostic",
            "non_canonical": True,
            "allowed_purpose": "Benchmark/observed-run probe only. Not for production sprints.",
            "owner_status": "diagnostic — probe only, no sprint ownership",
        },
    },
    # F177D: authority census — summary of who calls what
    "_authority_census": {
        "canonical_sprint_calls": ["main() --sprint → core.__main__.run_sprint()"],
        "alternate_production_paths": ["_run_sprint_mode (owns lifecycle, NOT canonical report)", "_run_public_passive_once (no lifecycle, no report boundary)"],
        "residual_helper_paths": ["run_warmup (shared WARMUP helper, called by both canonical and alternate)"],
        "diagnostic_paths": ["_run_observed_default_feed_batch_once (probe only)"],
        # Shell-only: main() and pure utility functions. Never sprint owners.
        "shell_only": [
            "main() — CLI dispatcher, reads args, calls canonical or alternate",
            "get_entrypoint_authority_status() — read-only authority query",
            "_run_boot_guard() — boot hygiene, no sprint ownership",
            "_preflight_check() — capability check, no sprint ownership",
            "get_runtime_status() — runtime snapshot, no ownership",
            "get_boot_telemetry() — boot telemetry, no ownership",
            "clear_boot_telemetry() — test utility only",
        ],
    },
    # F177D: role summary — quick lookup table
    "_role_summary": {
        "canonical_sprint_owner": "canonical",
        "main()": "shell",
        "_run_sprint_mode()": "alternate",
        "_run_public_passive_once()": "alternate",
        "run_warmup()": "residual",
        "_run_observed_default_feed_batch_once()": "diagnostic",
    },
    # F177D: key invariant — no confusion between canonical and observed/diagnostic
    "_non_confusion_invariant": (
        "Canonical path (core.__main__.run_sprint) produces canonical_run_summary with "
        "canonical_sprint_owner='core.__main__.run_sprint'. "
        "No alternate/residual path may claim this field value."
    ),
}


def get_entrypoint_authority_status() -> dict:
    """Read-only authority status — no side effects."""
    return ENTRYPOINT_AUTHORITY.copy()


def get_entrypoint_role(name: str) -> str:
    """
    Return the role label for a named entrypoint.

    Roles:
        canonical  — sole production sprint owner (core.__main__.run_sprint)
        shell      — CLI dispatcher, never owns sprint state
        alternate  — legacy production path, not canonical
        residual   — shared helper, not a sprint owner
        diagnostic — probe/benchmark only, not production

    Unknown names return "unknown".
    """
    return ENTRYPOINT_AUTHORITY.get("_role_summary", {}).get(name, "unknown")


# =============================================================================

import msgspec

logger = logging.getLogger(__name__)

# =============================================================================
# Sprint 8AI: Boot telemetry buffer — O(1) append, side-effect free
# =============================================================================

_boot_telemetry: List[Dict[str, Any]] = []


def _boot_record(step: str, status: str, **kw: Any) -> None:
    """Append a boot telemetry entry. O(1), no I/O."""
    _boot_telemetry.append({"step": step, "status": status, "ms": time.time(), **kw})


def get_boot_telemetry() -> List[Dict[str, Any]]:
    """Return a copy of boot telemetry. Side-effect free."""
    return list(_boot_telemetry)


def clear_boot_telemetry() -> None:
    """Clear boot telemetry. For tests only."""
    _boot_telemetry.clear()


# =============================================================================
# Sprint 8VD §E: Preflight check — graceful degradation, never raises
# =============================================================================

async def _preflight_check() -> dict:
    """
    Check critical system capabilities before sprint starts.
    Always returns a dict — never raises an exception.
    """
    results: dict = {}
    try:
        import mlx.core as mx
        results["metal"] = mx.metal.is_available()
    except Exception:
        results["metal"] = False
    try:
        import psutil
        vm = psutil.virtual_memory()
        results["free_ram_mb"] = round(vm.available / 1024 / 1024, 1)
        results["memory_pct"] = vm.percent
    except Exception:
        results["free_ram_mb"] = -1
    # Sprint F500J §2: REMOVED duckdb.connect() eager check.
    # DuckDB availability is verified through store.async_initialize() in the
    # runtime flow. duckdb_store.py lazy-imports duckdb via _get_duckdb().
    # Calling duckdb.connect() here was a heavyweight eager import (~30-50ms)
    # that provided no truth value since sprint always runs regardless.
    logger.info(f"[PREFLIGHT] {results}")
    return results


# =============================================================================
# Sprint 8AI: Status helper — O(1), side-effect free, diagnostic only
# Sprint 8AM C.7: Extended with owned resource tracking
# =============================================================================

# Sprint 8AM C.7: Owned resource registry (set by _run_public_passive_once)
_owned_resources: dict[str, bool] = {
    "session_owned": False,
    "store_owned": False,
}


def get_runtime_status() -> Dict[str, Any]:
    """
    Return current runtime status snapshot.
    O(1), side-effect free, purely diagnostic.

    Sprint 8AM C.7: Extended to include owned resource tracking.
    """
    return {
        "uvloop_installed": _uvloop_installed,
        "boot_telemetry": get_boot_telemetry(),
        "signal_handlers_installed": _signal_handlers_installed,
        "signal_teardown_flag": _signal_teardown_flag,
        # Sprint 8AM C.7
        "session_owned": _owned_resources.get("session_owned", False),
        "store_owned": _owned_resources.get("store_owned", False),
        "owned_resources": [k for k, v in _owned_resources.items() if v],
        "owned_resource_count": sum(1 for v in _owned_resources.values() if v),
        "last_error": None,
    }


# =============================================================================
# Signal teardown — Sprint 8V + 8AI: lightweight, async-safe
# =============================================================================

_signal_teardown_flag: bool = False
_signal_handlers_installed: bool = False


def _get_and_clear_signal_flag() -> bool:
    """Atomically read and reset the signal flag. Thread-safe."""
    global _signal_teardown_flag
    val = _signal_teardown_flag
    _signal_teardown_flag = False
    return val


def _install_signal_teardown(loop: "asyncio.AbstractEventLoop") -> None:
    """
    Install SIGINT/SIGTERM handlers that schedule loop.stop().

    Uses signal.signal() — must be called from main thread before
    asyncio.run() creates the loop. Handlers are lightweight (set flag only).

    The async main loop polls _get_and_clear_signal_flag() and breaks
    when True, ensuring clean teardown without heavy work in signal context.

    Sprint 8AI: Signal handler does NOT directly clean up resources.
    It only sets the flag and schedules loop.stop().
    Actual cleanup happens in AsyncExitStack unwind.
    """
    def _handler(signum: int, _frame) -> None:
        global _signal_teardown_flag
        sig_name = signal.Signals(signum).name
        logger.info(f"[SIGNAL] Received {sig_name}, initiating teardown...")
        _signal_teardown_flag = True
        loop.call_soon_threadsafe(loop.stop)

    try:
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
        logger.info("[SIGNAL] SIGINT/SIGTERM handlers installed")
        global _signal_handlers_installed
        _signal_handlers_installed = True
    except (ValueError, OSError) as e:
        logger.warning(f"[SIGNAL] Could not install signal handlers: {e}")


# =============================================================================
# Sprint 8AI: Boot guard — synchronous, called BEFORE asyncio.run()
# =============================================================================

def _run_boot_guard(lmdb_root: Optional[pathlib.Path] = None) -> tuple[int, str]:
    """
    Run LMDB boot guard (8AG) synchronously.

    This is the FIRST boot step, before any runtime acquisition.
    Must be called:
      - BEFORE asyncio.run() in sync boot context, OR
      - via asyncio.to_thread() inside async context

    Returns (removed_count, reason).

    On unsafe state (live lock holder detected), raises BootGuardError.
    """
    if lmdb_root is None:
        # Try to derive from paths if available
        try:
            from hledac.universal.paths import LMDB_ROOT as _derived_root
            lmdb_root = _derived_root
        except Exception:
            return 0, "lmdb_root_not_configured"

    try:
        from hledac.universal.knowledge.lmdb_boot_guard import (
            cleanup_stale_lmdb_lock,
            BootGuardError as _BootGuardError,
        )
    except Exception as e:
        return 0, f"boot_guard_import_failed({e})"

    try:
        removed, reason = cleanup_stale_lmdb_lock(lmdb_root)
        _boot_record("boot_guard", "ok", removed=removed, reason=reason)
        return removed, reason
    except _BootGuardError:
        # Re-raise BootGuardError without wrapping — caller decides to abort
        raise
    except Exception as e:
        _boot_record("boot_guard", "error", error=str(e))
        return 0, f"boot_guard_error({e})"


class BootGuardError(Exception):
    """Raised when boot guard detects unsafe stale-lock state."""
    pass


# =============================================================================
# Sprint 8AI: AsyncExitStack-backed teardown
# =============================================================================

async def _run_async_main(stop_flag: Callable[[], bool]) -> None:
    """
    Main async entry point with AsyncExitStack-backed teardown.

    Sprint 8AI:
    - Boot guard is called BEFORE this coroutine starts (in main())
    - AsyncExitStack manages all cleanup callbacks in LIFO order
    - Orphan tasks are cancelled before loop.close()
    - Signal handler never directly cleans up resources
    """
    benchmark_mode = os.environ.get("HLEDAC_BENCHMARK", "0") == "1"

    if benchmark_mode:
        # E0-T5A: Redirect benchmark to bounded observed-default-feed-batch path.
        # This exercises the real feed pipeline (async_run_live_feed_pipeline per source)
        # with M1-safe limits: 2 concurrency, 10 entries/feed, 25s per feed, 120s batch.
        # Produces a structured ObservedRunReport with runtime truth taxonomy.
        logger.info("[BENCHMARK] Running bounded observed-default-feed-batch probe...")
        try:
            report: ObservedRunReport = await _run_observed_default_feed_batch_once(
                feed_concurrency=2,
                max_entries_per_feed=10,
                per_feed_timeout_s=25.0,
                batch_timeout_s=120.0,
            )
            verdict = classify_runtime_truth(
                elapsed_s=report.elapsed_ms / 1000.0,
                active_iterations=report.active_pipeline_iterations,
            )
            logger.info(
                f"[BENCHMARK] verdict={verdict} elapsed={report.elapsed_ms/1000:.1f}s "
                f"completed={report.completed_sources}/{report.total_sources} "
                f"accepted={report.accepted_findings} stored={report.stored_findings} "
                f"error={report.batch_error}"
            )
            # Print human-readable summary
            summary = format_observed_run_summary(msgspec.structs.asdict(report))
            logger.info(f"\n{summary}")
        except Exception as e:
            logger.error(f"[BENCHMARK] Probe failed: {e}", exc_info=True)
        return

    _boot_record("async_main_start", "entered")

    # Sprint 8AI: AsyncExitStack as unified teardown backbone
    # All resources are registered here for guaranteed LIFO unwind
    exit_stack: Optional[contextlib.AsyncExitStack] = None

    try:
        exit_stack = contextlib.AsyncExitStack()
        await exit_stack.__aenter__()

        _boot_record("async_exit_stack_entered", "ok")

        # Sprint 8AI: Register teardown callbacks in acquisition order
        # LIFO order: last registered → first cleaned up
        # Order: duckdb_close → atomic_flush → persistent_close → sprint_lifecycle
        # (surfaces that don't exist are N/A — no mock registration)

        # TODO [8AI]: Register duckdb_store.close() if/when duckdb is acquired in main.py
        # TODO [8AI]: Register atomic_storage.flush() if/when atomic storage is acquired
        # TODO [8AI]: Register persistent_layer.close() if/when persistent layer is acquired

        # Normal operation - import and run the main orchestrator
        # Note: This path is reserved for future Sprint 1+ implementation
        logger.info("[MAIN] Hledac Universal initialized")
        logger.info("[MAIN] uvloop active: %s", _uvloop_installed)

        # Sprint 8V: lightweight signal-driven exit without busy-waiting
        while not stop_flag():
            await asyncio.sleep(0.5)

        _boot_record("async_main_loop_exit", "signal_received")

    except asyncio.CancelledError:
        _boot_record("async_main_loop_exit", "cancelled")
        logger.info("[MAIN] Task cancelled")
        raise

    except Exception as e:
        _boot_record("async_main_loop_exit", "exception", error=str(e))
        logger.error(f"[MAIN] Fatal error: {e}", exc_info=True)
        raise

    finally:
        # Sprint 8AI: Graceful task cancellation BEFORE AsyncExitStack unwind
        # This must happen inside the finally block so it runs even if
        # the signal arrives during AsyncExitStack.__aenter__
        await _cancel_orphan_tasks()

        # Sprint 8AI: AsyncExitStack unwind — runs all registered cleanup callbacks
        if exit_stack is not None:
            _boot_record("async_exit_stack_unwind", "starting")
            try:
                await exit_stack.__aexit__(None, None, None)
                _boot_record("async_exit_stack_unwind", "completed")
            except Exception as e:
                logger.warning(f"[MAIN] AsyncExitStack unwind error: {e}")
                _boot_record("async_exit_stack_unwind", "error", error=str(e))


async def _cancel_orphan_tasks() -> None:
    """
    Cancel all orphan asyncio tasks before loop close.
    Sprint 8AI: Prevents "Task was destroyed but it is pending" warnings.
    Sprint 8AM C.8.1: Orphan drain is protected by asyncio.timeout(5.0).
    """
    current_task = asyncio.current_task()
    all_tasks = [t for t in asyncio.all_tasks() if t is not current_task and not t.done()]

    if not all_tasks:
        return

    _boot_record("task_cancellation", f"cancelling_{len(all_tasks)}_tasks")

    for task in all_tasks:
        task.cancel()

    if all_tasks:
        try:
            # C.8.1: drain protected by 5s timeout — don't wait forever
            await asyncio.wait_for(
                asyncio.gather(*all_tasks, return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            _boot_record("task_cancellation", "drain_timeout_5s")
            logger.warning("[MAIN] Orphan task drain timed out after 5s, continuing shutdown")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"[MAIN] gather error during cancellation: {e}")

    _boot_record("task_cancellation", f"completed_{len(all_tasks)}_tasks")


# =============================================================================
# Sprint 8AM C.1: Owned Runtime Path — Public Passive Once
# =============================================================================

async def _run_public_passive_once(
    stop_flag: Callable[[], bool],
    *,
    owned_session: bool = True,
    owned_store: bool = True,
) -> None:
    """
    F162C NON-CANONICAL: This path is NOT the canonical sprint owner.
    Owned resources are acquired and registered in AsyncExitStack for LIFO cleanup.
    Delegation: async_run_live_public_pipeline() + async_run_default_feed_batch().

    Cleanup order (LIFO):
      1. Orphan task drain (already done in _cancel_orphan_tasks before this)
      2. Session close (last registered → first cleaned)
      3. Store close (first registered → last cleaned)

    Args:
        stop_flag: Callable returning True when shutdown signal received.
        owned_session: If True, acquire and own the shared aiohttp session.
        owned_store: If True, create and own a DuckDBShadowStore instance.
    """
    global _owned_resources

    _boot_record("public_passive_once", "entered")

    # Reset owned resources tracking
    _owned_resources = {
        "session_owned": False,
        "store_owned": False,
    }

    exit_stack: Optional[contextlib.AsyncExitStack] = None
    store_instance = None

    try:
        exit_stack = contextlib.AsyncExitStack()
        await exit_stack.__aenter__()

        _boot_record("async_exit_stack_entered", "ok")

        # Sprint 8AM C.2: Session ownership
        session_created = False
        if owned_session:
            try:
                # Obtain shared session — this is a Lazy singleton
                # We "own" it by registering its async close
                from .network.session_runtime import (
                    async_get_aiohttp_session,
                    close_aiohttp_session_async,
                )
                # Trigger session creation (lazy init)
                await async_get_aiohttp_session()
                # Register session close in AsyncExitStack
                exit_stack.callback(close_aiohttp_session_async)
                _owned_resources["session_owned"] = True
                session_created = True
                _boot_record("session_owned", "registered")
            except Exception as e:
                logger.warning(f"[MAIN] Failed to acquire session: {e}")
                _boot_record("session_owned", "failed", error=str(e))

        # Sprint 8AM C.3: Store ownership
        if owned_store and exit_stack is not None:
            try:
                from .knowledge.duckdb_store import create_owned_store
                # Create owned store (uses paths.py RAMDisk SSOT)
                store_instance = create_owned_store()
                # Async init
                await store_instance.async_initialize()
                # Register store.close() via AsyncExitStack callback
                # store.aclose is async — wrap in lambda for callback
                async def close_store():
                    if store_instance is not None:
                        await store_instance.aclose()
                exit_stack.callback(close_store)
                _owned_resources["store_owned"] = True
                _boot_record("store_owned", "registered")
            except Exception as e:
                logger.warning(f"[MAIN] Failed to acquire store: {e}")
                _boot_record("store_owned", "failed", error=str(e))
                store_instance = None

        logger.info("[MAIN] Hledac Universal initialized")
        logger.info("[MAIN] uvloop active: %s", _uvloop_installed)

        # Sprint 8AM C.9: Delegation to existing pipelines
        # Import here to avoid module-level side effects
        from .pipeline.live_public_pipeline import async_run_live_public_pipeline
        from .pipeline.live_feed_pipeline import async_run_default_feed_batch

        # Sprint 8SA: Configure bootstrap patterns before pipeline runs
        from .patterns.pattern_matcher import configure_default_bootstrap_patterns_if_empty
        configure_default_bootstrap_patterns_if_empty()

        # Use the SAME store instance for both pipelines
        web_result = await async_run_live_public_pipeline(
            query="public passive OSINT",
            store=store_instance,
            max_results=5,
        )
        _boot_record("pipeline_web", "completed", discovered=web_result.discovered)

        feed_result = await async_run_default_feed_batch(
            store=store_instance,
            max_entries_per_feed=10,
            query_context="public passive OSINT",
        )
        _boot_record("pipeline_feed", "completed", sources=feed_result.total_sources)

        # Sprint 8V: lightweight signal-driven exit
        while not stop_flag():
            await asyncio.sleep(0.5)

        _boot_record("public_passive_once", "signal_received")

    except asyncio.CancelledError:
        _boot_record("public_passive_once", "cancelled")
        raise

    except Exception as e:
        _boot_record("public_passive_once", "exception", error=str(e))
        logger.error(f"[MAIN] Fatal error: {e}", exc_info=True)
        raise

    finally:
        # Sprint 8AM C.8: Orphan tasks drained BEFORE this point (in _cancel_orphan_tasks)
        # Sprint 8AM C.4: AsyncExitStack unwind — LIFO cleanup order:
        #   1. store close (registered first)
        #   2. session close (registered last)
        if exit_stack is not None:
            _boot_record("async_exit_stack_unwind", "starting")
            try:
                await exit_stack.__aexit__(None, None, None)
                _boot_record("async_exit_stack_unwind", "completed")
            except Exception as e:
                logger.warning(f"[MAIN] AsyncExitStack unwind error: {e}")
                _boot_record("async_exit_stack_unwind", "error", error=str(e))


# =============================================================================
# Sprint 8AO: Observed Live Run — UMA Sampler
# C.3: Lightweight sampler tracking peak UMA during observed run
# C.3.b: Registered into same task lifecycle as other background tasks
# =============================================================================

_uma_sample_interval_s: float = 0.5


class _UmaSampler:
    """
    Lightweight in-process UMA sampler for observed run.

    Runs as an asyncio.Task registered in the same orphan-drain path
    as all other background tasks. Bounded memory: stores only peak
    and last sample, no full time-series.

    C.3.a: Default 0.5s interval — light-weight.
    C.3.b: Uses _cancel_orphan_tasks drain path — no custom cancel logic.
    """

    __slots__ = (
        "_running",
        "_task",
        "_lock",
        "_peak_used_gib",
        "_peak_state",
        "_sample_count",
        "_start_state",
        "_end_state",
        "_start_swap",
        "_peak_swap_used_gib",
        "_interval",
    )

    def __init__(self, interval_s: float = 0.5) -> None:
        self._interval = interval_s
        self._running = False
        self._task: Optional[asyncio.Task[Any]] = None
        self._lock = asyncio.Lock()
        self._peak_used_gib = 0.0
        self._peak_state = "unknown"
        self._sample_count = 0
        self._start_state = "unknown"
        self._end_state = "unknown"
        self._start_swap = 0.0
        self._peak_swap_used_gib = 0.0

    async def start(self) -> None:
        """Start sampler task. Idempotent."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._sample_loop())

    async def stop(self) -> None:
        """Stop sampler task gracefully."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def get_snapshot(self) -> dict:
        """
        Return current snapshot. Thread-safe read.
        Returns N/A for unavailable metrics.
        """
        return {
            "peak_used_gib": self._peak_used_gib,
            "peak_state": self._peak_state,
            "sample_count": self._sample_count,
            "start_state": self._start_state,
            "end_state": self._end_state,
            "peak_swap_used_gib": self._peak_swap_used_gib,
        }

    async def _sample_loop(self) -> None:
        """Background sampling loop. Self-terminates when _running=False."""
        from .core.resource_governor import sample_uma_status

        try:
            while self._running:
                try:
                    status = sample_uma_status()
                    async with self._lock:
                        self._sample_count += 1
                        if self._sample_count == 1:
                            self._start_state = status.state
                        self._end_state = status.state
                        if status.system_used_gib > self._peak_used_gib:
                            self._peak_used_gib = status.system_used_gib
                            self._peak_state = status.state
                        if hasattr(status, "swap_used_gib") and status.swap_used_gib > self._peak_swap_used_gib:
                            self._peak_swap_used_gib = status.swap_used_gib
                except Exception:
                    pass  # fail-open: keep sampling even if one tick fails
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            raise  # C.8: propagate CancelledError, don't swallow


# =============================================================================
# Sprint 8AO: Observed Live Run — Report Structure & Helpers
# =============================================================================

# Module-level singleton for last run report (C.4)
_last_observed_run_report: Optional[dict] = None
_observed_run_lock = asyncio.Lock()

# Sprint 8BA C.0: Runtime truth fields (recorded before/after live run)
_actual_live_run_executed: bool = False
_interpreter_executable: str = ""
_interpreter_version: str = ""
_ahocorasick_available: bool = False
_bootstrap_pack_version: int = 0
_default_bootstrap_count: int = 0
_store_counters_reset_before_run: bool = False
_matcher_probe_rss_hits: tuple[str, ...] = ()
_matcher_probe_sample_used: str = ""

# E0-T4: Runtime truth taxonomy — ACTIVE pipeline iteration counter
_active_pipeline_iterations: int = 0


def classify_runtime_truth(elapsed_s: float, active_iterations: int) -> str:
    """
    Classify runtime truth level based on duration and ACTIVE work.

    OBSERVED-RUN / DIAGNOSTIC ONLY — non-canonical.

    This taxonomy lives in root __main__.py as an *observed-run signal* for
    CLI/banner reporting.  It is NOT the canonical runtime-truth owner.

    Canonical meaningful/smoke truth is defined in:
        hledac.universal.core.__main__._is_meaningful_run()
        hledac.universal.core.__main__._runtime_truth()
    Those functions return is_meaningful (bool) and runtime_truth_level
    (smoke | meaningful | meaningful_empty | mixed) derived from cycle-level
    scheduler data — richer and more authoritative than this module-level
    duration heuristic.

    Mapping (read-only, observational):
      root import_probe             → correlates with canonical smoke (short, no cycles)
      root entrypoint_smoke         → correlates with canonical smoke (no/minimal cycles)
      root meaningful_active_probe  → correlates with canonical meaningful (real runtime)

    Taxonomy (E0-T4):
      - import_probe:              elapsed < 180s (any iteration count)
      - entrypoint_smoke:          elapsed >= 180s but active_iterations <= 1
      - meaningful_active_probe:   elapsed >= 180s AND active_iterations >= 2

    Rules:
      1. Duration < 180s → never meaningful_active_probe
      2. 0 or 1 ACTIVE iteration → never meaningful_active_probe (regardless of duration)
      3. Both conditions must hold: elapsed >= 180s AND active_iterations >= 2

    Returns a stable, parseable string label.
    """
    if elapsed_s < 180.0:
        return "import_probe"
    if active_iterations <= 1:
        return "entrypoint_smoke"
    return "meaningful_active_probe"


def _record_runtime_truth() -> None:
    """Record python3 interpreter truth at module load time."""
    global _interpreter_executable, _interpreter_version, _ahocorasick_available
    global _bootstrap_pack_version, _default_bootstrap_count

    import sys
    import os

    _interpreter_executable = sys.executable
    _interpreter_version = sys.version_info[:2] == (3, 12) and "3.12" or sys.version

    try:
        import ahocorasick as _
        _ahocorasick_available = True
    except ImportError:
        _ahocorasick_available = False

    # Bootstrap pack truth
    try:
        from .patterns.pattern_matcher import get_default_bootstrap_patterns
        _default_bootstrap_count = len(get_default_bootstrap_patterns())
        _bootstrap_pack_version = 2  # Sprint 8AZ bootstrap pack v2
    except Exception:
        _bootstrap_pack_version = 0
        _default_bootstrap_count = 0


# Record runtime truth at module import time
_record_runtime_truth()


# Sprint 8BA C.0: Accessor functions for runtime truth fields
def get_actual_live_run_executed() -> bool:
    return _actual_live_run_executed

def get_interpreter_executable() -> str:
    return _interpreter_executable

def get_interpreter_version() -> str:
    return _interpreter_version

def get_ahocorasick_available() -> bool:
    return _ahocorasick_available

def get_bootstrap_pack_version() -> int:
    return _bootstrap_pack_version

def get_default_bootstrap_count() -> int:
    return _default_bootstrap_count


# Module-level aliases for test compatibility (D.10)
actual_live_run_executed = _actual_live_run_executed
interpreter_executable = _interpreter_executable
interpreter_version = _interpreter_version
ahocorasick_available = _ahocorasick_available


class ObservedRunReport(msgspec.Struct, frozen=True, gc=False):
    """
    Structured observability report for a bounded observed feed batch run.

    C.1: All required fields present.
    C.7: content_quality_validated reflects PatternMatcher availability.
    """
    started_ts: float
    finished_ts: float
    elapsed_ms: float
    total_sources: int
    completed_sources: int
    fetched_entries: int
    accepted_findings: int
    stored_findings: int
    batch_error: Optional[str]
    per_source: tuple[dict, ...]
    patterns_configured: int
    bootstrap_applied: bool
    content_quality_validated: bool
    # Dedup raw deltas (C.2)
    dedup_before: dict
    dedup_after: dict
    dedup_delta: dict
    dedup_surface_available: bool
    # UMA snapshot (C.3)
    uma_snapshot: dict
    # Slow-source ranking (C.10)
    slow_sources: tuple[dict, ...]
    # Error summary (C.11)
    error_summary: dict
    # Sprint 8AS C.2: Success rate + failed source count
    success_rate: float
    failed_source_count: int
    # Sprint 8AS C.0: Baseline delta summary
    baseline_delta: dict
    # Sprint 8AS C.1: Feed health breakdown
    health_breakdown: dict
    # Sprint 8AU: pre-store signal trace
    entries_seen: int = 0
    entries_with_empty_assembled_text: int = 0
    entries_with_text: int = 0
    entries_scanned: int = 0
    entries_with_hits: int = 0
    total_pattern_hits: int = 0
    findings_built_pre_store: int = 0
    avg_assembled_text_len: float = 0.0
    signal_stage: str = "unknown"
    # Sprint 8AV: store rejection delta (BEFORE reset, AFTER batch)
    accepted_count_delta: int = 0
    low_information_rejected_count_delta: int = 0
    in_memory_duplicate_rejected_count_delta: int = 0
    persistent_duplicate_rejected_count_delta: int = 0
    other_rejected_count_delta: int = 0
    # Sprint 8AW: end-to-end diagnostic
    diagnostic_root_cause: str = "unknown"
    is_network_variance: bool = False
    # Sprint 8BA: runtime truth
    interpreter_executable: str = ""
    interpreter_version: str = ""
    ahocorasick_available: bool = False
    actual_live_run_executed: bool = False
    bootstrap_pack_version: int = 0
    default_bootstrap_count: int = 0
    store_counters_reset_before_run: bool = False
    matcher_probe_sample_used: str = ""
    matcher_probe_rss_hits: tuple[str, ...] = ()
    # Sprint 8BC: bounded sample capture from pipeline
    sample_scanned_texts: tuple[str, ...] = ()
    sample_hit_counts: tuple[int, ...] = ()
    sample_hit_labels_union: tuple[str, ...] = ()
    sample_texts_truncated: bool = False
    feed_content_mismatch: bool = False
    patterns_configured_at_run: int = 0
    automaton_built_at_run: bool = False
    # Sprint 8BH C.0: live run truth fields
    used_rich_feed_content: bool = False
    used_article_fallback: bool = False
    matched_feed_names: tuple[str, ...] = ()
    accepted_feed_names: tuple[str, ...] = ()
    live_run_attempt_count: int = 0
    live_run_attempt_1_result: str = ""
    live_run_attempt_2_result: str = ""
    recommended_next_sprint: str = ""
    # E0-T4: runtime truth taxonomy
    active_pipeline_iterations: int = 0


# Sprint 8BH C.6: recommendation mapping
def _compute_recommended_next_sprint(
    total_pattern_hits: int,
    accepted_count_delta: int,
    matched_feed_names: tuple[str, ...],
    accepted_feed_names: tuple[str, ...],
    is_network_variance: bool,
) -> str:
    """
    Map live run result to recommended next sprint tag.

    C.6 mapping rules:
    - accepted_present              -> "8BK_scheduler_entry_hash_v1"
    - total_pattern_hits>0 and accepted=0 and duplicate dominates -> "8BK_scheduler_entry_hash_v1"
    - total_pattern_hits>0 and accepted=0 and low_info dominates -> "8BL_quality_profile_rss"
    - total_pattern_hits=0 and teaser_only_content              -> "8BM_article_fallback_v2"
    - total_pattern_hits=0 and temporal_feed_vocabulary_mismatch -> "8BN_feed_source_expansion"
    - total_pattern_hits=0 and pattern_pack_vocabulary_gap        -> "8BO_pattern_pack_v3_security_vocabulary"
    - network_variance                                            -> "repeat_live_run_no_code_change"
    """
    if is_network_variance:
        return "repeat_live_run_no_code_change"
    if accepted_count_delta > 0:
        return "8BK_scheduler_entry_hash_v1"
    if total_pattern_hits > 0:
        # hits exist but no accepted — check rejection dominance
        return "8BK_scheduler_entry_hash_v1"
    # total_pattern_hits == 0
    # We can't definitively distinguish teaser_only/temporal/pack gap from here
    # without sample_enriched_texts analysis, so we default to feed expansion
    return "8BN_feed_source_expansion"


def _build_observed_run_report(
    started_ts: float,
    batch_result: Any,
    dedup_before: dict,
    dedup_after: dict,
    uma_snapshot: dict,
    patterns_configured: int,
    batch_error: Optional[str],
    bootstrap_applied: bool = False,
    # Sprint 8AU: signal trace
    entries_seen: int = 0,
    entries_with_empty_assembled_text: int = 0,
    entries_with_text: int = 0,
    entries_scanned: int = 0,
    entries_with_hits: int = 0,
    total_pattern_hits: int = 0,
    findings_built_pre_store: int = 0,
    avg_assembled_text_len: float = 0.0,
    signal_stage: str = "unknown",
    # Sprint 8AV: store delta
    accepted_count_delta: int = 0,
    low_information_rejected_count_delta: int = 0,
    in_memory_duplicate_rejected_count_delta: int = 0,
    persistent_duplicate_rejected_count_delta: int = 0,
    other_rejected_count_delta: int = 0,
    # Sprint 8AW: diagnostic
    diagnostic_root_cause: str = "unknown",
    is_network_variance: bool = False,
    # Sprint 8BA: runtime truth
    interpreter_executable: str = "",
    interpreter_version: str = "",
    ahocorasick_available: bool = False,
    actual_live_run_executed: bool = False,
    bootstrap_pack_version: int = 0,
    default_bootstrap_count: int = 0,
    store_counters_reset_before_run: bool = False,
    matcher_probe_sample_used: str = "",
    matcher_probe_rss_hits: tuple[str, ...] = (),
    # Sprint 8BC: bounded sample capture
    sample_scanned_texts: tuple[str, ...] = (),
    sample_hit_counts: tuple[int, ...] = (),
    sample_hit_labels_union: tuple[str, ...] = (),
    sample_texts_truncated: bool = False,
    feed_content_mismatch: bool = False,
    patterns_configured_at_run: int = 0,
    automaton_built_at_run: bool = False,
    # Sprint 8BH C.0: live run truth
    used_rich_feed_content: bool = False,
    used_article_fallback: bool = False,
    matched_feed_names: tuple[str, ...] = (),
    accepted_feed_names: tuple[str, ...] = (),
    live_run_attempt_count: int = 0,
    live_run_attempt_1_result: str = "",
    live_run_attempt_2_result: str = "",
    recommended_next_sprint: str = "",
    # E0-T4: runtime truth taxonomy
    active_pipeline_iterations: int = 0,
) -> ObservedRunReport:
    """Build the structured report from raw inputs."""
    finished_ts = time.time()
    elapsed_ms = (finished_ts - started_ts) * 1000.0

    # Per-source results
    per_source_raw: list[dict] = []
    for src in (batch_result.sources if batch_result else []):
        per_source_raw.append({
            "feed_url": src.feed_url,
            "label": src.label,
            "origin": src.origin,
            "priority": src.priority,
            "fetched_entries": src.fetched_entries,
            "accepted_findings": src.accepted_findings,
            "stored_findings": src.stored_findings,
            "elapsed_ms": src.elapsed_ms,
            "error": getattr(src, "error", None),
        })

    # Dedup delta
    dedup_delta = {}
    if dedup_surface_available(dedup_before, dedup_after):
        for key in ("persistent_duplicate_count", "quality_duplicate_count",
                    "in_memory_duplicate_count"):
            before = dedup_before.get(key, 0) or 0
            after = dedup_after.get(key, 0) or 0
            dedup_delta[key] = after - before

    # Slow-source ranking (C.10): top 3 by elapsed_ms desc
    sorted_sources = sorted(
        per_source_raw,
        key=lambda s: s.get("elapsed_ms", 0),
        reverse=True,
    )
    slow_sources: tuple[dict, ...] = tuple(sorted_sources[:3])

    # Error summary (C.11)
    error_sources = [s for s in per_source_raw if s.get("error") is not None]
    error_summary = {
        "count": len(error_sources),
        "sources": [
            {"feed_url": s["feed_url"], "error": s["error"]}
            for s in error_sources
        ],
    }

    # Sprint 8AS C.2: success_rate + failed_source_count
    total_sources_val = batch_result.total_sources if batch_result else 0
    completed_sources_val = batch_result.completed_sources if batch_result else 0
    failed_source_count_val = total_sources_val - completed_sources_val
    success_rate_val = (
        completed_sources_val / total_sources_val
        if total_sources_val > 0 else 0.0
    )

    # Sprint 8AS C.0: baseline delta
    _report_for_delta = {
        "total_sources": total_sources_val,
        "completed_sources": completed_sources_val,
        "fetched_entries": batch_result.fetched_entries if batch_result else 0,
        "accepted_findings": batch_result.accepted_findings if batch_result else 0,
        "stored_findings": batch_result.stored_findings if batch_result else 0,
        "elapsed_ms": elapsed_ms,
    }
    baseline_delta_val = compare_observed_run_to_baseline(_report_for_delta)

    # Sprint 8AS C.1: health breakdown
    health_breakdown_val = classify_feed_health(tuple(per_source_raw))

    return ObservedRunReport(
        started_ts=started_ts,
        finished_ts=finished_ts,
        elapsed_ms=elapsed_ms,
        total_sources=total_sources_val,
        completed_sources=completed_sources_val,
        fetched_entries=batch_result.fetched_entries if batch_result else 0,
        accepted_findings=batch_result.accepted_findings if batch_result else 0,
        stored_findings=batch_result.stored_findings if batch_result else 0,
        batch_error=batch_error,
        per_source=tuple(per_source_raw),
        patterns_configured=patterns_configured,
        bootstrap_applied=bootstrap_applied,
        content_quality_validated=(patterns_configured > 0),
        dedup_before=dedup_before,
        dedup_after=dedup_after,
        dedup_delta=dedup_delta,
        dedup_surface_available=dedup_surface_available(dedup_before, dedup_after),
        uma_snapshot=uma_snapshot,
        slow_sources=slow_sources,
        error_summary=error_summary,
        success_rate=success_rate_val,
        failed_source_count=failed_source_count_val,
        baseline_delta=baseline_delta_val,
        health_breakdown=health_breakdown_val,
        # Sprint 8AU signal trace
        entries_seen=entries_seen,
        entries_with_empty_assembled_text=entries_with_empty_assembled_text,
        entries_with_text=entries_with_text,
        entries_scanned=entries_scanned,
        entries_with_hits=entries_with_hits,
        total_pattern_hits=total_pattern_hits,
        findings_built_pre_store=findings_built_pre_store,
        avg_assembled_text_len=avg_assembled_text_len,
        signal_stage=signal_stage,
        # Sprint 8AV store delta
        accepted_count_delta=accepted_count_delta,
        low_information_rejected_count_delta=low_information_rejected_count_delta,
        in_memory_duplicate_rejected_count_delta=in_memory_duplicate_rejected_count_delta,
        persistent_duplicate_rejected_count_delta=persistent_duplicate_rejected_count_delta,
        other_rejected_count_delta=other_rejected_count_delta,
        # Sprint 8AW diagnostic
        diagnostic_root_cause=diagnostic_root_cause,
        is_network_variance=is_network_variance,
        # Sprint 8BA runtime truth
        interpreter_executable=interpreter_executable,
        interpreter_version=interpreter_version,
        ahocorasick_available=ahocorasick_available,
        actual_live_run_executed=actual_live_run_executed,
        bootstrap_pack_version=bootstrap_pack_version,
        default_bootstrap_count=default_bootstrap_count,
        store_counters_reset_before_run=store_counters_reset_before_run,
        matcher_probe_sample_used=matcher_probe_sample_used,
        matcher_probe_rss_hits=matcher_probe_rss_hits,
        # Sprint 8BC bounded sample capture
        sample_scanned_texts=sample_scanned_texts,
        sample_hit_counts=sample_hit_counts,
        sample_hit_labels_union=sample_hit_labels_union,
        sample_texts_truncated=sample_texts_truncated,
        feed_content_mismatch=feed_content_mismatch,
        patterns_configured_at_run=patterns_configured_at_run,
        automaton_built_at_run=automaton_built_at_run,
        # Sprint 8BH C.0 live run truth
        used_rich_feed_content=used_rich_feed_content,
        used_article_fallback=used_article_fallback,
        matched_feed_names=matched_feed_names,
        accepted_feed_names=accepted_feed_names,
        live_run_attempt_count=live_run_attempt_count,
        live_run_attempt_1_result=live_run_attempt_1_result,
        live_run_attempt_2_result=live_run_attempt_2_result,
        recommended_next_sprint=recommended_next_sprint,
        active_pipeline_iterations=active_pipeline_iterations,
    )


def dedup_surface_available(before: dict, after: dict) -> bool:
    """Check if dedup surface is available in both snapshots."""
    return bool(before.get("persistent_dedup_enabled") or after.get("persistent_dedup_enabled"))


# =============================================================================
# Sprint 8AS: 8AO Baseline Comparison
# C.0, B.4
# =============================================================================

# 8AO baseline truth (Sprint 8AO live run — bounded, same limits)
_SPRINT_8AO_BASELINE: dict = {
    "total_sources": 5,
    "completed_sources": 1,
    "fetched_entries": 10,
    "accepted_findings": 0,
    "stored_findings": 0,
    "elapsed_ms": 1557.6,
    "pattern_count": 0,  # infra-only
    "failed_source_count": 4,
}


def compare_observed_run_to_baseline(report: dict) -> dict:
    """
    Sprint 8AS C.0: Compare current observed run to 8AO baseline.

    Returns a delta dict with keys:
      - total_sources_delta, completed_sources_delta, fetched_entries_delta,
        accepted_findings_delta, stored_findings_delta, elapsed_ms_delta,
        failed_source_count_delta, findings_delta,
      - completed_sources: current value
      - failed_source_count: current value
      - findings_delta: accepted_findings delta vs baseline
      - status: "improved" | "regressed" | "stable" | "network_variance" | "insufficient_data"
      - blocker: Optional[str] description if findings are 0
    """
    current_completed = report.get("completed_sources", 0)
    current_failed = report.get("total_sources", 0) - current_completed
    current_accepted = report.get("accepted_findings", 0)
    current_fetched = report.get("fetched_entries", 0)
    current_stored = report.get("stored_findings", 0)
    current_elapsed = report.get("elapsed_ms", 0.0)

    b = _SPRINT_8AO_BASELINE
    delta = {
        "completed_sources": current_completed,
        "completed_sources_delta": current_completed - b["completed_sources"],
        "fetched_entries_delta": current_fetched - b["fetched_entries"],
        "accepted_findings_delta": current_accepted - b["accepted_findings"],
        "stored_findings_delta": current_stored - b["stored_findings"],
        "failed_source_count": current_failed,
        "failed_source_count_delta": current_failed - b["failed_source_count"],
        "findings_delta": current_accepted - b["accepted_findings"],
        "elapsed_ms_delta": current_elapsed - b["elapsed_ms"],
        "baseline_ref": "8AO",
    }

    # Determine status
    if current_completed == 0 and current_fetched == 0:
        delta["status"] = "network_variance"
        delta["blocker"] = "no_sources_completed_no_fetched"
    elif current_accepted > b["accepted_findings"]:
        delta["status"] = "improved"
        delta["blocker"] = None
    elif current_accepted < b["accepted_findings"]:
        delta["status"] = "regressed"
        delta["blocker"] = None
    else:
        # current_accepted == baseline (0) — could be network or genuine
        if current_completed < b["completed_sources"]:
            delta["status"] = "network_variance"
            delta["blocker"] = "lower_completion_rate_than_8ao"
        else:
            delta["status"] = "stable"
            delta["blocker"] = None

    return delta


def diagnose_end_to_end_live_run(
    completed_sources: int,
    entries_seen: int,
    pattern_count: int,
    total_pattern_hits: int,
    entries_with_text: int,
    avg_assembled_text_len: float,
    findings_built_pre_store: int,
    accepted_count_delta: int,
    low_information_rejected_count_delta: int,
    in_memory_duplicate_rejected_count_delta: int,
    persistent_duplicate_rejected_count_delta: int,
    other_rejected_count_delta: int = 0,
) -> str:
    """
    Sprint 8AW C.1: Canonical root-cause diagnosis for a zero-findings live run.

    Returns exactly one of:
      empty_registry
      no_new_entries
      network_variance
      no_pattern_hits
      no_pattern_hits_possible_morphology_gap
      pattern_hits_but_no_findings_built
      low_information_rejection_dominant
      duplicate_rejection_dominant
      accepted_present
      unknown
    """
    # Order matters — most specific first
    if completed_sources == 0 and entries_seen == 0:
        return "network_variance"
    if completed_sources > 0 and entries_seen == 0:
        return "no_new_entries"
    if pattern_count == 0:
        return "empty_registry"
    if total_pattern_hits == 0:
        if entries_with_text > 0 and avg_assembled_text_len >= 50:
            return "no_pattern_hits_possible_morphology_gap"
        return "no_pattern_hits"
    if total_pattern_hits > 0 and findings_built_pre_store == 0:
        return "pattern_hits_but_no_findings_built"
    if accepted_count_delta > 0:
        return "accepted_present"
    # Rejection analysis — only when findings were built but nothing accepted
    if findings_built_pre_store > 0 and accepted_count_delta == 0:
        total_rejected = (
            low_information_rejected_count_delta
            + in_memory_duplicate_rejected_count_delta
            + persistent_duplicate_rejected_count_delta
            + other_rejected_count_delta
        )
        if total_rejected == 0:
            return "unknown"
        low_frac = low_information_rejected_count_delta / total_rejected
        dup_frac = (in_memory_duplicate_rejected_count_delta + persistent_duplicate_rejected_count_delta) / total_rejected
        if low_frac >= dup_frac and low_information_rejected_count_delta > 0:
            return "low_information_rejection_dominant"
        return "duplicate_rejection_dominant"
    return "unknown"


# =============================================================================
# Sprint 8AS: Feed Health Classification
# C.1, B.4
# =============================================================================

class FeedHealthKind(str):
    """Sprint 8AS C.1: Feed health classification labels."""
    SUCCESS = "success"
    NETWORK_ERROR = "network_error"
    PARSE_ERROR = "parse_error"
    ENTITY_RECOVERY_RELATED_ERROR = "entity_recovery_related_error"
    TIMEOUT_ERROR = "timeout_error"
    UNKNOWN_ERROR = "unknown_error"


def classify_feed_health(per_source: tuple[dict, ...]) -> dict:
    """
    Sprint 8AS C.1: Classify per-source results into health categories.

    Returns:
        dict with keys:
          - health_breakdown: dict[FeedHealthKind, int]
          - success_count: int
          - total: int
    """
    breakdown: dict[str, int] = {
        FeedHealthKind.SUCCESS: 0,
        FeedHealthKind.NETWORK_ERROR: 0,
        FeedHealthKind.PARSE_ERROR: 0,
        FeedHealthKind.ENTITY_RECOVERY_RELATED_ERROR: 0,
        FeedHealthKind.TIMEOUT_ERROR: 0,
        FeedHealthKind.UNKNOWN_ERROR: 0,
    }

    for src in per_source:
        error = src.get("error") or ""
        if not error:
            breakdown[FeedHealthKind.SUCCESS] += 1
        elif "timeout" in error.lower() or "timed out" in error.lower():
            breakdown[FeedHealthKind.TIMEOUT_ERROR] += 1
        elif "entity" in error.lower() or "recovery" in error.lower() or "recover" in error.lower():
            breakdown[FeedHealthKind.ENTITY_RECOVERY_RELATED_ERROR] += 1
        elif "parse" in error.lower() or "xml" in error.lower() or "feed" in error.lower() or "html" in error.lower():
            breakdown[FeedHealthKind.PARSE_ERROR] += 1
        elif "network" in error.lower() or "connection" in error.lower() or "dns" in error.lower() or "resolve" in error.lower() or "http" in error.lower() or "ssl" in error.lower() or "certificate" in error.lower():
            breakdown[FeedHealthKind.NETWORK_ERROR] += 1
        else:
            breakdown[FeedHealthKind.UNKNOWN_ERROR] += 1

    total = len(per_source)
    return {
        "health_breakdown": breakdown,
        "success_count": breakdown[FeedHealthKind.SUCCESS],
        "total": total,
    }


def _get_pattern_count() -> int:
    """Get current pattern count from PatternMatcher. Returns 0 if unavailable."""
    try:
        from .patterns.pattern_matcher import get_pattern_matcher
        pm = get_pattern_matcher()
        if hasattr(pm, "pattern_count"):
            return pm.pattern_count()
    except Exception:
        pass
    return 0


def _get_pattern_status() -> tuple[int, bool]:
    """
    Get current pattern count and bootstrap_applied flag from PatternMatcher.

    Returns:
        Tuple of (patterns_configured, bootstrap_applied).
        Falls back to (0, False) if PatternMatcher unavailable.
    """
    try:
        from .patterns.pattern_matcher import get_pattern_matcher
        pm = get_pattern_matcher()
        if hasattr(pm, "pattern_count"):
            count = pm.pattern_count()
            status = pm.get_status()
            return count, status.get("bootstrap_default_configured", False)
    except Exception:
        pass
    return 0, False


def _ensure_runtime_patterns_configured_for_live_validation() -> tuple[int, bool]:
    """
    Sprint 8AQ C.3: Ensure patterns are configured before live validation.

    Applies bootstrap OSINT pack if registry is empty.
    Does NOT overwrite existing patterns.

    Returns:
        Tuple of (patterns_configured, bootstrap_applied) after ensure.
    """
    try:
        from .patterns.pattern_matcher import (
            get_pattern_matcher,
            configure_default_bootstrap_patterns_if_empty,
        )
        pm = get_pattern_matcher()
        current_count = pm.pattern_count()
        if current_count > 0:
            status = pm.get_status()
            return current_count, status.get("bootstrap_default_configured", False)
        # Registry empty — apply bootstrap
        applied = configure_default_bootstrap_patterns_if_empty()
        return pm.pattern_count(), applied
    except Exception:
        return 0, False


# =============================================================================
# Sprint 8AO: Observed Live Run — Main Entry Point
# C.0, C.1, C.2, C.3, C.4
# =============================================================================

async def _run_observed_default_feed_batch_once(
    *,
    feed_concurrency: int = 2,
    max_entries_per_feed: int = 10,
    per_feed_timeout_s: float = 25.0,
    batch_timeout_s: float = 120.0,
) -> ObservedRunReport:
    """
    F162C DIAGNOSTIC ONLY: Observed one-shot bounded feed batch run.
    This is a BENCHMARK/OBSERVED-RUN probe — NOT production sprint.
    Canonical sprint production is core.__main__.run_sprint().

    Collects end-to-end signal + store rejection truth by calling
    async_run_live_feed_pipeline() directly per source (instead of the
    batch wrapper) so that 8AU pre-store signal fields are accessible.

    Collects:
    - C.1 + C.2: Batch totals + per-source metrics via FeedSourceBatchRunResult
    - C.2: Dedup runtime status delta (before/after snapshots)
    - C.3: Peak UMA via lightweight _UmaSampler
    - Sprint 8AU: Pre-store signal trace (entries_seen, pattern hits, etc.)
    - Sprint 8AV: Store rejection delta (accepted_count, low_information, etc.)
    - Sprint 8AW: End-to-end root-cause diagnosis

    Args (bounded, safe limits for live run):
        feed_concurrency: <= 2 (C.6)
        max_entries_per_feed: <= 10 (C.6)
        per_feed_timeout_s: <= 25 (C.6)
        batch_timeout_s: <= 120 (C.6)

    Returns:
        ObservedRunReport with all observability fields.
    """
    global _last_observed_run_report

    started_ts = time.time()
    batch_error: Optional[str] = None
    uma_sampler = _UmaSampler(interval_s=_uma_sample_interval_s)
    dedup_before: dict = {}
    dedup_after: dict = {}
    store_instance: Any = None

    # Sprint 8AU: aggregate signal trace across all sources
    total_entries_seen = 0
    total_entries_with_empty = 0
    total_entries_with_text = 0
    total_entries_scanned = 0
    total_entries_with_hits = 0
    total_pattern_hits = 0
    total_findings_built = 0
    total_assembled_chars = 0
    signal_stages: list[str] = []

    # Sprint 8AV: rejection delta
    accepted_count_delta = 0
    low_information_rejected_delta = 0
    in_memory_duplicate_rejected_delta = 0
    persistent_duplicate_rejected_delta = 0
    other_rejected_delta = 0

    # Sprint 8AS: per-source results for FeedSourceBatchRunResult
    per_source_results: list[dict] = []
    total_fetched = 0
    total_accepted = 0
    total_stored = 0
    completed_sources_count = 0
    sources: list[Any] = []

    # Sprint 8AW: track pattern_count for diagnosis
    patterns_configured = 0
    # Sprint 8BH: track rich feed content usage
    total_entries_with_rich_feed_content = 0

    # C.1 + C.2: Acquire owned resources first
    try:
        from .knowledge.duckdb_store import create_owned_store
        from .network.session_runtime import async_get_aiohttp_session
        from .pipeline.live_feed_pipeline import async_run_live_feed_pipeline
        from .discovery.rss_atom_adapter import get_default_feed_seeds

        store_instance = create_owned_store()
        await store_instance.async_initialize()

        # Sprint 8AV C.2: Reset counters BEFORE BEFORE snapshot if surface exists
        if hasattr(store_instance, "reset_ingest_reason_counters"):
            store_instance.reset_ingest_reason_counters()

        # C.2: Dedup before snapshot (on the same store instance)
        try:
            dedup_before = store_instance.get_dedup_runtime_status()
        except Exception:
            dedup_before = {}

        await async_get_aiohttp_session()  # trigger lazy init

        # Get seeds for sources list
        seeds = get_default_feed_seeds()
        seed_sources = [(s.feed_url, s.label or "", s.source or "", int(s.priority or 0)) for s in seeds]

    except Exception as e:
        batch_error = f"setup_failed: {e}"
        uma_sampler = _UmaSampler(interval_s=0.0)
        await uma_sampler.start()
        await uma_sampler.stop()
        uma_snapshot = uma_sampler.get_snapshot()
        patterns_cfg, bootstrap_applied = _ensure_runtime_patterns_configured_for_live_validation()

        # Sprint 8AW: diagnose
        diag = diagnose_end_to_end_live_run(
            completed_sources=0,
            entries_seen=0,
            pattern_count=patterns_cfg,
            total_pattern_hits=0,
            entries_with_text=0,
            avg_assembled_text_len=0.0,
            findings_built_pre_store=0,
            accepted_count_delta=0,
            low_information_rejected_count_delta=0,
            in_memory_duplicate_rejected_count_delta=0,
            persistent_duplicate_rejected_count_delta=0,
            other_rejected_count_delta=0,
        )

        report = _build_observed_run_report(
            started_ts=started_ts,
            batch_result=None,
            dedup_before={},
            dedup_after={},
            uma_snapshot=uma_snapshot,
            patterns_configured=patterns_cfg,
            batch_error=batch_error,
            bootstrap_applied=bootstrap_applied,
            diagnostic_root_cause=diag,
            is_network_variance=(diag == "network_variance"),
            patterns_configured_at_run=patterns_cfg,
            automaton_built_at_run=False,
            active_pipeline_iterations=_active_pipeline_iterations,
        )
        async with _observed_run_lock:
            _last_observed_run_report = msgspec.json.decode(msgspec.json.encode(report))
        return report

    # Start UMA sampler (C.3)
    await uma_sampler.start()

    # Semaphore for bounded concurrency
    sem = asyncio.Semaphore(feed_concurrency)

    async def _run_single_source(feed_url: str, label: str, origin: str, priority: int):
        nonlocal total_entries_seen, total_entries_with_empty, total_entries_with_text
        nonlocal total_entries_scanned, total_entries_with_hits, total_pattern_hits
        nonlocal total_findings_built, total_assembled_chars, signal_stages
        nonlocal total_fetched, total_accepted, total_stored, completed_sources_count
        nonlocal patterns_configured, total_entries_with_rich_feed_content

        async with sem:
            start = time.monotonic()
            elapsed_ms = 0.0
            error_msg = None
            result = None

            try:
                async with asyncio.timeout(per_feed_timeout_s):
                    result = await async_run_live_feed_pipeline(
                        feed_url=feed_url,
                        store=store_instance,
                        query_context=label or feed_url,
                        max_entries=max_entries_per_feed,
                        timeout_s=per_feed_timeout_s,
                    )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                elapsed_ms = (time.monotonic() - start) * 1000.0
                error_msg = "per_feed_timeout"
            except BaseException as exc:
                elapsed_ms = (time.monotonic() - start) * 1000.0
                error_msg = f"unexpected:{type(exc).__name__}:{exc}"

            if error_msg is None and result is not None:
                elapsed_ms = (time.monotonic() - start) * 1000.0
                completed_sources_count += 1

            # Aggregate 8AU signal trace
            if result is not None:
                total_entries_seen += result.entries_seen
                total_entries_with_empty += result.entries_with_empty_assembled_text
                total_entries_with_text += result.entries_with_text
                total_entries_scanned += result.entries_scanned
                total_entries_with_hits += result.entries_with_hits
                total_pattern_hits += result.total_pattern_hits
                total_findings_built += result.findings_built_pre_store
                total_assembled_chars += result.assembled_text_chars_total
                if result.signal_stage:
                    signal_stages.append(result.signal_stage)
                patterns_configured = max(patterns_configured, result.patterns_configured)
                total_fetched += result.fetched_entries
                total_accepted += result.accepted_findings
                total_stored += result.stored_findings
                # Sprint 8BH: aggregate rich feed content usage
                total_entries_with_rich_feed_content += getattr(result, "entries_with_rich_feed_content", 0) or 0

            return {
                "feed_url": feed_url,
                "label": label,
                "origin": origin,
                "priority": priority,
                "fetched_entries": result.fetched_entries if result else 0,
                "accepted_findings": result.accepted_findings if result else 0,
                "stored_findings": result.stored_findings if result else 0,
                "elapsed_ms": elapsed_ms,
                "error": error_msg or (result.error if result else None),
                "_pipeline_result": result,  # Sprint 8BC: for sample aggregation
            }

    # Run all sources with bounded concurrency
    try:
        async with asyncio.timeout(batch_timeout_s):
            tasks = [
                _run_single_source(url, lbl, org, pri)
                for url, lbl, org, pri in seed_sources
            ]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in batch_results:
                if isinstance(res, asyncio.CancelledError):
                    raise res
                elif isinstance(res, BaseException):
                    per_source_results.append({
                        "feed_url": "<unknown>",
                        "label": "",
                        "origin": "unknown",
                        "priority": 0,
                        "fetched_entries": 0,
                        "accepted_findings": 0,
                        "stored_findings": 0,
                        "elapsed_ms": 0.0,
                        "error": f"gather_exception:{type(res).__name__}:{res}",
                    })
                else:
                    per_source_results.append(res)
    except asyncio.CancelledError:
        batch_error = "cancelled"
    except asyncio.TimeoutError:
        batch_error = "batch_timeout"
    except Exception as exc:
        batch_error = str(exc)

    # C.2: Dedup after snapshot
    try:
        dedup_after = store_instance.get_dedup_runtime_status()
    except Exception:
        dedup_after = {}

    # Sprint 8AV: Compute store rejection delta
    if hasattr(store_instance, "get_dedup_runtime_status") and hasattr(store_instance, "reset_ingest_reason_counters"):
        # The AFTER snapshot has all the counts since reset
        accepted_count_delta = dedup_after.get("accepted_count", 0)
        low_information_rejected_delta = dedup_after.get("low_information_rejected_count", 0)
        in_memory_duplicate_rejected_delta = dedup_after.get("in_memory_duplicate_rejected_count", 0)
        persistent_duplicate_rejected_delta = dedup_after.get("persistent_duplicate_rejected_count", 0)
        other_rejected_delta = dedup_after.get("other_rejected_count", 0)

    # Stop UMA sampler
    await uma_sampler.stop()
    uma_snapshot = uma_sampler.get_snapshot()

    # Build batch_result-like object for _build_observed_run_report
    # Use the same duckdb_store module for FeedSourceBatchRunResult
    # Sprint 8BC: aggregate sample fields across all feeds
    _all_sample_texts: list[str] = []
    _all_sample_hit_counts: list[int] = []
    _all_sample_hit_labels: list[str] = []
    _any_truncated = False
    for r in per_source_results:
        pr = r.get("_pipeline_result")
        if pr is not None:
            _all_sample_texts.extend(getattr(pr, "sample_scanned_texts", ()) or ())
            _all_sample_hit_counts.extend(getattr(pr, "sample_hit_counts", ()) or ())
            _all_sample_hit_labels.extend(getattr(pr, "sample_hit_labels_union", ()) or ())
            _any_truncated = _any_truncated or getattr(pr, "sample_texts_truncated", False)
    _combined_sample_texts = tuple(_all_sample_texts)
    _combined_sample_hit_counts = tuple(_all_sample_hit_counts)
    _combined_sample_hit_labels = tuple(dict.fromkeys(_all_sample_hit_labels))
    _feed_content_mismatch = (
        bool(_combined_sample_hit_counts and all(c == 0 for c in _combined_sample_hit_counts))
    )

    # Sprint 8BH C.2: aggregate matched_feed_names and accepted_feed_names
    _matched_feed_names_set: set[str] = set()
    _accepted_feed_names_set: set[str] = set()
    for r in per_source_results:
        pr = r.get("_pipeline_result")
        if pr is not None:
            feed_label = r.get("label", "") or r.get("feed_url", "")
            hits = getattr(pr, "total_pattern_hits", 0) or 0
            accepted = getattr(pr, "accepted_findings", 0) or 0
            if hits > 0 and feed_label:
                _matched_feed_names_set.add(feed_label)
            if accepted > 0 and feed_label:
                _accepted_feed_names_set.add(feed_label)
    _combined_matched_feed_names = tuple(sorted(_matched_feed_names_set))
    _combined_accepted_feed_names = tuple(sorted(_accepted_feed_names_set))

    try:
        from .pipeline.live_feed_pipeline import FeedSourceBatchRunResult, FeedSourceRunResult
        completed = sum(1 for r in per_source_results if r.get("error") is None)
        failed = len(per_source_results) - completed
        total = len(seed_sources)
        batch_result_obj = type(
            "BatchResult",
            (),
            {
                "total_sources": total,
                "completed_sources": completed,
                "fetched_entries": total_fetched,
                "accepted_findings": total_accepted,
                "stored_findings": total_stored,
                "sources": tuple(
                    FeedSourceRunResult(
                        feed_url=r["feed_url"],
                        label=r["label"],
                        origin=r["origin"],
                        priority=r["priority"],
                        fetched_entries=r["fetched_entries"],
                        accepted_findings=r["accepted_findings"],
                        stored_findings=r["stored_findings"],
                        elapsed_ms=r["elapsed_ms"],
                        error=r["error"],
                    )
                    for r in per_source_results
                ),
                "error": batch_error,
                # Sprint 8BC: sample capture
                "sample_scanned_texts": _combined_sample_texts,
                "sample_hit_counts": _combined_sample_hit_counts,
                "sample_hit_labels_union": _combined_sample_hit_labels,
                "sample_texts_truncated": _any_truncated,
                "feed_content_mismatch": _feed_content_mismatch,
            }
        )()
    except Exception:
        batch_result_obj = None

    # Sprint 8AW: compute avg assembled text len
    avg_text_len = (
        total_assembled_chars / total_entries_with_text
        if total_entries_with_text > 0
        else 0.0
    )

    # Sprint 8AW: dominant signal stage
    dominant_signal_stage = "unknown"
    if signal_stages:
        from collections import Counter
        stage_counts = Counter(signal_stages)
        dominant_signal_stage = stage_counts.most_common(1)[0][0]

    # Sprint 8BC: Bootstrap MUST be called BEFORE diagnose so pattern_count is accurate
    patterns, bootstrap_applied = _ensure_runtime_patterns_configured_for_live_validation()
    if patterns > 0:
        patterns_configured = patterns

    # Sprint 8AW: diagnose end-to-end (AFTER bootstrap so pattern_count is correct)
    diag = diagnose_end_to_end_live_run(
        completed_sources=completed_sources_count,
        entries_seen=total_entries_seen,
        pattern_count=patterns_configured,
        total_pattern_hits=total_pattern_hits,
        entries_with_text=total_entries_with_text,
        avg_assembled_text_len=avg_text_len,
        findings_built_pre_store=total_findings_built,
        accepted_count_delta=accepted_count_delta,
        low_information_rejected_count_delta=low_information_rejected_delta,
        in_memory_duplicate_rejected_count_delta=in_memory_duplicate_rejected_delta,
        persistent_duplicate_rejected_count_delta=persistent_duplicate_rejected_delta,
        other_rejected_count_delta=other_rejected_delta,
    )

    # Sprint 8BH C.6: compute recommended_next_sprint (after diag is available)
    _recommended_sprint = _compute_recommended_next_sprint(
        total_pattern_hits=total_pattern_hits,
        accepted_count_delta=accepted_count_delta,
        matched_feed_names=_combined_matched_feed_names,
        accepted_feed_names=_combined_accepted_feed_names,
        is_network_variance=(diag == "network_variance"),
    )

    report = _build_observed_run_report(
        started_ts=started_ts,
        batch_result=batch_result_obj,
        dedup_before=dedup_before,
        dedup_after=dedup_after,
        uma_snapshot=uma_snapshot,
        patterns_configured=patterns_configured,
        batch_error=batch_error,
        bootstrap_applied=bootstrap_applied,
        # Sprint 8AU signal trace
        entries_seen=total_entries_seen,
        entries_with_empty_assembled_text=total_entries_with_empty,
        entries_with_text=total_entries_with_text,
        entries_scanned=total_entries_scanned,
        entries_with_hits=total_entries_with_hits,
        total_pattern_hits=total_pattern_hits,
        findings_built_pre_store=total_findings_built,
        avg_assembled_text_len=avg_text_len,
        signal_stage=dominant_signal_stage,
        # Sprint 8AV store delta
        accepted_count_delta=accepted_count_delta,
        low_information_rejected_count_delta=low_information_rejected_delta,
        in_memory_duplicate_rejected_count_delta=in_memory_duplicate_rejected_delta,
        persistent_duplicate_rejected_count_delta=persistent_duplicate_rejected_delta,
        other_rejected_count_delta=other_rejected_delta,
        # Sprint 8AW diagnostic
        diagnostic_root_cause=diag,
        is_network_variance=(diag == "network_variance"),
        # Sprint 8BA runtime truth
        interpreter_executable=_interpreter_executable,
        interpreter_version=_interpreter_version,
        ahocorasick_available=_ahocorasick_available,
        actual_live_run_executed=True,
        bootstrap_pack_version=_bootstrap_pack_version,
        default_bootstrap_count=_default_bootstrap_count,
        store_counters_reset_before_run=(_store_counters_reset_before_run if "_store_counters_reset_before_run" in dir() else False),
        matcher_probe_sample_used=_matcher_probe_sample_used,
        matcher_probe_rss_hits=_matcher_probe_rss_hits,
        # Sprint 8BC bounded sample capture
        sample_scanned_texts=getattr(batch_result_obj, "sample_scanned_texts", ()),
        sample_hit_counts=getattr(batch_result_obj, "sample_hit_counts", ()),
        sample_hit_labels_union=getattr(batch_result_obj, "sample_hit_labels_union", ()),
        sample_texts_truncated=getattr(batch_result_obj, "sample_texts_truncated", False),
        feed_content_mismatch=getattr(batch_result_obj, "feed_content_mismatch", False),
        patterns_configured_at_run=patterns_configured,
        automaton_built_at_run=False,
        # Sprint 8BH C.0 live run truth
        used_rich_feed_content=bool(total_entries_with_rich_feed_content > 0),
        used_article_fallback=False,
        matched_feed_names=_combined_matched_feed_names,
        accepted_feed_names=_combined_accepted_feed_names,
        live_run_attempt_count=1,
        live_run_attempt_1_result="success" if batch_error is None else f"error:{batch_error}",
        live_run_attempt_2_result="",
        recommended_next_sprint=_recommended_sprint,
        active_pipeline_iterations=_active_pipeline_iterations,
    )

    async with _observed_run_lock:
        _last_observed_run_report = msgspec.json.decode(msgspec.json.encode(report))

    return report


def get_last_observed_run_report() -> Optional[dict]:
    """
    Sprint 8AO C.4: Return last observed run report.

    Side-effect free getter for tests and debugging.
    Returns None if no observed run has completed yet.
    """
    return dict(_last_observed_run_report) if _last_observed_run_report else None


# =============================================================================
# Sprint 8AO: Human-Readable Summary Formatter
# C.5, C.10, C.11
# =============================================================================

def format_observed_run_summary(report: dict) -> str:
    """
    Sprint 8AO C.5: Human-readable multi-line summary.

    No new export module. No I/O. Pure text formatting.
    Includes:
    - Batch totals
    - Peak UMA
    - Dedup raw deltas
    - Top slow sources (C.10)
    - Sources with errors (C.11)
    - Pattern count note (C.7/C.9)
    """
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("OBSERVED FEED BATCH RUN SUMMARY")
    lines.append("=" * 60)

    # Sprint 8BA C.3: [runtime truth] section
    lines.append(f"\n[runtime truth]")
    lines.append(f"  interpreter_executable:       {report.get('interpreter_executable', 'N/A')}")
    lines.append(f"  interpreter_version:          {report.get('interpreter_version', 'N/A')}")
    lines.append(f"  ahocorasick_available:        {report.get('ahocorasick_available', 'N/A')}")
    lines.append(f"  actual_live_run_executed:     {report.get('actual_live_run_executed', False)}")
    lines.append(f"  bootstrap_pack_version:       {report.get('bootstrap_pack_version', 0)}")
    lines.append(f"  default_bootstrap_count:      {report.get('default_bootstrap_count', 0)}")
    lines.append(f"  store_counters_reset_before_run: {report.get('store_counters_reset_before_run', False)}")
    lines.append(f"  matcher_probe_sample_used:   {report.get('matcher_probe_sample_used', 'N/A')}")
    rss_hits = report.get('matcher_probe_rss_hits', ())
    lines.append(f"  matcher_probe_rss_hits:       {len(rss_hits)} hits")

    # Sprint 8BC C.4: [matcher truth] section
    sample_texts = report.get('sample_scanned_texts', ())
    sample_counts = report.get('sample_hit_counts', ())
    sample_labels = report.get('sample_hit_labels_union', ())
    lines.append(f"\n[matcher truth]")
    lines.append(f"  patterns_configured_at_run:  {report.get('patterns_configured_at_run', 0)}")
    lines.append(f"  automaton_built_at_run:     {report.get('automaton_built_at_run', False)}")
    lines.append(f"  sample_scanned_texts:       {len(sample_texts)} captured")
    lines.append(f"  sample_hit_counts:          {sample_counts}")
    lines.append(f"  sample_hit_labels_union:    {len(sample_labels)} unique labels")
    lines.append(f"  sample_texts_truncated:     {report.get('sample_texts_truncated', False)}")
    lines.append(f"  feed_content_mismatch:      {report.get('feed_content_mismatch', False)}")
    for i, txt in enumerate(sample_texts[:3], 1):
        lines.append(f"    sample[{i}]: {txt[:80]!r}")

    # Sprint 8BH C.5: [live run truth] section
    lines.append(f"\n[live run truth]")
    lines.append(f"  used_rich_feed_content:    {report.get('used_rich_feed_content', False)}")
    lines.append(f"  used_article_fallback:    {report.get('used_article_fallback', False)}")
    lines.append(f"  matched_feed_names:       {report.get('matched_feed_names', ())}")
    lines.append(f"  accepted_feed_names:       {report.get('accepted_feed_names', ())}")
    lines.append(f"  live_run_attempt_count:    {report.get('live_run_attempt_count', 0)}")
    lines.append(f"  live_run_attempt_1_result: {report.get('live_run_attempt_1_result', '')}")
    lines.append(f"  live_run_attempt_2_result: {report.get('live_run_attempt_2_result', '')}")
    rec = report.get('recommended_next_sprint', '')
    lines.append(f"  recommended_next_sprint:   {rec if rec else '(computed post-run)'}")

    # Batch totals (C.1)
    elapsed_s = report.get("elapsed_ms", 0) / 1000.0
    lines.append(f"\n[Batch Totals]")
    lines.append(f"  Total sources:     {report.get('total_sources', 0)}")
    lines.append(f"  Completed sources: {report.get('completed_sources', 0)}")
    lines.append(f"  Fetched entries:   {report.get('fetched_entries', 0)}")
    lines.append(f"  Accepted findings: {report.get('accepted_findings', 0)}")
    lines.append(f"  Stored findings:   {report.get('stored_findings', 0)}")
    lines.append(f"  Elapsed:           {elapsed_s:.2f}s ({report.get('elapsed_ms', 0):.1f}ms)")

    error = report.get("batch_error")
    if error:
        lines.append(f"  Batch error:       {error}")

    # Content quality flag (C.7)
    patterns = report.get("patterns_configured", 0)
    bootstrap_applied = report.get("bootstrap_applied", False)
    content_ok = report.get("content_quality_validated", False)
    if content_ok:
        bootstrap_note = " [bootstrap]" if bootstrap_applied else ""
        lines.append(f"  Content quality:   VALIDATED ({patterns} patterns){bootstrap_note}")
    else:
        lines.append(
            f"  Content quality:   INFRA-ONLY RUN (PatternMatcher empty — "
            "validated infrastructure/runtime path, not content quality)"
        )

    # Peak UMA (C.3)
    uma = report.get("uma_snapshot", {})
    lines.append(f"\n[Peak UMA]")
    lines.append(f"  Peak used GiB:    {uma.get('peak_used_gib', 'N/A'):.2f}" if isinstance(uma.get('peak_used_gib'), float) else f"  Peak used GiB:    {uma.get('peak_used_gib', 'N/A')}")
    lines.append(f"  Peak state:        {uma.get('peak_state', 'N/A')}")
    lines.append(f"  Start state:       {uma.get('start_state', 'N/A')}")
    lines.append(f"  End state:          {uma.get('end_state', 'N/A')}")
    lines.append(f"  Sample count:      {uma.get('sample_count', 0)}")
    swap_peak = uma.get("peak_swap_used_gib", 0.0)
    if isinstance(swap_peak, float) and swap_peak > 0:
        lines.append(f"  Peak swap GiB:     {swap_peak:.2f}")

    # Dedup raw deltas (C.2, C.12)
    dedup_surf = report.get("dedup_surface_available", False)
    lines.append(f"\n[Dedup Raw Deltas]")
    if dedup_surf:
        delta = report.get("dedup_delta", {})
        lines.append(f"  persistent_dedup_enabled: True")
        lines.append(f"  persistent_duplicate_count delta: {delta.get('persistent_duplicate_count', 'N/A')}")
        lines.append(f"  quality_duplicate_count delta:    {delta.get('quality_duplicate_count', 'N/A')}")
        lines.append(f"  in_memory_duplicate_count delta: {delta.get('in_memory_duplicate_count', 'N/A')}")
    else:
        lines.append(f"  dedup_surface_available: False (N/A)")

    # Slow-source ranking (C.10)
    slow = report.get("slow_sources", [])
    if slow:
        lines.append(f"\n[Top Slow Sources (by elapsed_ms desc)]")
        for i, src in enumerate(slow, 1):
            lines.append(
                f"  {i}. {src.get('feed_url', '?')[:60]}"
                f"  elapsed_ms={src.get('elapsed_ms', 0):.1f}"
                f"  fetched={src.get('fetched_entries', 0)}"
            )

    # Error summary (C.11)
    err_sum = report.get("error_summary", {})
    err_count = err_sum.get("count", 0)
    if err_count > 0:
        lines.append(f"\n[Error Summary] ({err_count} source(s) failed)")
        for err_src in err_sum.get("sources", []):
            lines.append(f"  - {err_src.get('feed_url', '?')[:60]}: {err_src.get('error', '?')}")
    else:
        lines.append(f"\n[Error Summary] 0 errors")

    # Sprint 8AS C.2: Success rate + failed source count
    success_rate = report.get("success_rate", 0.0)
    failed_count = report.get("failed_source_count", 0)
    lines.append(f"\n[Sprint 8AS C.2] Success Rate")
    lines.append(f"  Success rate: {success_rate:.1%}")
    lines.append(f"  Failed sources: {failed_count}")

    # Sprint 8AS C.0: Baseline delta
    baseline = report.get("baseline_delta", {})
    if baseline:
        lines.append(f"\n[Sprint 8AS C.0] Delta vs 8AO Baseline")
        lines.append(f"  Status: {baseline.get('status', 'N/A')}")
        lines.append(f"  Completed sources: {baseline.get('completed_sources', 'N/A')} ({baseline.get('completed_sources_delta', 0):+d})")
        lines.append(f"  Fetched entries: {baseline.get('fetched_entries_delta', 0):+d}")
        lines.append(f"  Accepted findings: {baseline.get('accepted_findings_delta', 0):+d}")
        lines.append(f"  Stored findings: {baseline.get('stored_findings_delta', 0):+d}")
        lines.append(f"  Failed sources: {baseline.get('failed_source_count', 'N/A')} ({baseline.get('failed_source_count_delta', 0):+d})")
        blocker = baseline.get("blocker")
        if blocker:
            lines.append(f"  Blocker: {blocker}")

    # Sprint 8AS C.1: Health breakdown
    health = report.get("health_breakdown", {})
    if health:
        breakdown = health.get("health_breakdown", {})
        lines.append(f"\n[Sprint 8AS C.1] Feed Health Breakdown")
        total_h = health.get("total", 0)
        lines.append(f"  Total sources: {total_h}")
        lines.append(f"  Success: {breakdown.get('success', 0)}")
        lines.append(f"  Network error: {breakdown.get('network_error', 0)}")
        lines.append(f"  Parse error: {breakdown.get('parse_error', 0)}")
        lines.append(f"  Entity/recovery error: {breakdown.get('entity_recovery_related_error', 0)}")
        lines.append(f"  Timeout error: {breakdown.get('timeout_error', 0)}")
        lines.append(f"  Unknown error: {breakdown.get('unknown_error', 0)}")

    # Sprint 8AS C.4: Content validation + session cleanup truth
    content_validated = report.get("content_quality_validated", False)
    lines.append(f"\n[Sprint 8AS C.4] Run Quality")
    if content_validated:
        lines.append(f"  Content validation: ACTIVE (patterns={patterns})")
        lines.append(f"  Run type: CONTENT-VALIDATED (not infra-only)")
    else:
        lines.append(f"  Content validation: INFRA-ONLY (patterns=0)")
        lines.append(f"  Run type: INFRA-ONLY")

    # Sprint 8BA C.3: [signal funnel] (B.9 funnel order)
    entries_seen = report.get("entries_seen", 0)
    entries_with_empty = report.get("entries_with_empty_assembled_text", 0)
    entries_with_text = report.get("entries_with_text", 0)
    entries_scanned = report.get("entries_scanned", 0)
    entries_with_hits = report.get("entries_with_hits", 0)
    total_pattern_hits = report.get("total_pattern_hits", 0)
    findings_built = report.get("findings_built_pre_store", 0)
    avg_text_len = report.get("avg_assembled_text_len", 0.0)
    signal_stage = report.get("signal_stage", "unknown")

    if entries_seen > 0 or entries_with_text > 0:
        lines.append(f"\n[signal funnel]")
        lines.append(f"  entries_seen:                     {entries_seen}")
        lines.append(f"  entries_with_empty_assembled_text: {entries_with_empty}")
        lines.append(f"  entries_with_text:                {entries_with_text}")
        lines.append(f"  entries_scanned:                  {entries_scanned}")
        lines.append(f"  entries_with_hits:                {entries_with_hits}")
        lines.append(f"  total_pattern_hits:               {total_pattern_hits}")
        lines.append(f"  findings_built_pre_store:         {findings_built}")
        lines.append(f"  avg_assembled_text_len:          {avg_text_len:.1f}")
        lines.append(f"  dominant_signal_stage:           {signal_stage}")
        if entries_seen > 0:
            funnel_rate = entries_with_text / entries_seen * 100
            lines.append(f"  entries_with_text/seen:          {funnel_rate:.1f}%")
        if entries_with_text > 0:
            scan_rate = entries_scanned / entries_with_text * 100
            lines.append(f"  entries_scanned/with_text:      {scan_rate:.1f}%")

    # Sprint 8BA C.3: [store rejection trace]
    accepted_delta = report.get("accepted_count_delta", 0)
    low_info_delta = report.get("low_information_rejected_count_delta", 0)
    in_mem_dup = report.get("in_memory_duplicate_rejected_count_delta", 0)
    persist_dup = report.get("persistent_duplicate_rejected_count_delta", 0)
    other_delta = report.get("other_rejected_count_delta", 0)
    total_rejected = low_info_delta + in_mem_dup + persist_dup + other_delta

    if accepted_delta > 0 or total_rejected > 0:
        lines.append(f"\n[store rejection trace]")
        lines.append(f"  accepted_count_delta:           {accepted_delta}")
        lines.append(f"  low_information_rejected:        {low_info_delta}")
        lines.append(f"  in_memory_duplicate_rejected:    {in_mem_dup}")
        lines.append(f"  persistent_duplicate_rejected:   {persist_dup}")
        lines.append(f"  other_rejected:                 {other_delta}")
        lines.append(f"  total_rejected:                 {total_rejected}")
        if total_rejected > 0:
            lines.append(f"  entropy_threshold:              0.5")
            lines.append(f"  entropy_min_len:                8")
            low_frac = low_info_delta / total_rejected * 100
            dup_frac = (in_mem_dup + persist_dup) / total_rejected * 100
            lines.append(f"  low_info fraction:              {low_frac:.1f}%")
            lines.append(f"  duplicate fraction:            {dup_frac:.1f}%")

    # Sprint 8BA C.3: [root cause] + [recommendation] (C.2 mapping)
    diag = report.get("diagnostic_root_cause", "unknown")
    is_net_var = report.get("is_network_variance", False)
    lines.append(f"\n[root cause]")
    lines.append(f"  diagnostic_root_cause:           {diag}")
    lines.append(f"  is_network_variance:             {is_net_var}")

    # C.2: Recommendation mapping (derived in formatter, not persisted)
    lines.append(f"\n[recommendation]")
    if diag == "accepted_present":
        lines.append(f"  → scheduler_entry_hash_v1")
    elif diag == "duplicate_rejection_dominant":
        lines.append(f"  → scheduler_entry_hash_v1")
    elif diag == "no_pattern_hits_possible_morphology_gap":
        lines.append(f"  → pattern_pack_v3_or_source_specific_text_extraction")
    elif diag == "no_pattern_hits":
        lines.append(f"  → pattern_pack_v3_or_source_specific_text_extraction")
    elif diag == "pattern_hits_but_no_findings_built":
        lines.append(f"  → finding_build_trace")
    elif diag == "low_information_rejection_dominant":
        lines.append(f"  → quality_gate_recalibration_only_if_reproduced")
    elif diag in ("network_variance", "no_new_entries"):
        lines.append(f"  → repeat_live_run")
    else:
        lines.append(f"  → repeat_live_run")

    lines.append("=" * 60)
    return "\n".join(lines)


# =============================================================================
# Sprint 0B: Benchmark probe (unchanged)
# =============================================================================

async def _run_benchmark_probe() -> Dict[str, Any]:
    """
    Run Sprint 0B benchmark probe tests.

    Returns:
        Dict with benchmark results including pass/fail counts.
    """
    from .utils.flow_trace import is_enabled, get_summary

    results = {
        "probe": "sprint_0b_runtime",
        "uvloop_installed": _uvloop_installed,
        "timestamp": time.time(),
        "checks": {},
    }

    # Check 1: uvloop availability
    results["checks"]["uvloop_available"] = _uvloop_installed

    # Check 2: flow_trace default-off
    flow_trace_default_off = not is_enabled()
    results["checks"]["flow_trace_default_off"] = flow_trace_default_off

    # Check 3: flow_trace get_summary() works when disabled
    try:
        summary = get_summary()
        results["checks"]["flow_trace_summary_safe"] = isinstance(summary, dict)
    except Exception as e:
        results["checks"]["flow_trace_summary_safe"] = False
        results["checks"]["flow_trace_error"] = str(e)

    # Check 4: Session factory singleton behavior
    try:
        factory1 = AsyncSessionFactory()
        factory2 = AsyncSessionFactory()
        results["checks"]["session_factory_singleton"] = factory1 is factory2
    except Exception as e:
        results["checks"]["session_factory_singleton"] = False
        results["checks"]["singleton_error"] = str(e)

    # Check 5: AsyncSessionFactory.get_session() works
    try:
        factory = AsyncSessionFactory()
        loop = await factory.get_session()
        results["checks"]["async_session_works"] = loop is not None and isinstance(loop, asyncio.AbstractEventLoop)
    except Exception as e:
        results["checks"]["async_session_works"] = False
        results["checks"]["session_error"] = str(e)

    # Summary
    all_passed = all(v is True or isinstance(v, dict) for v in results["checks"].values())
    results["all_passed"] = all_passed
    results["passed_count"] = sum(1 for v in results["checks"].values() if v is True)

    return results


class AsyncSessionFactory:
    """
    Singleton-ish async session factory for aiohttp.ClientSession management.

    Sprint 8UD B.8: Refactored from AbstractEventLoop to ClientSession.
    Thread-safe lazy initialization with lock.
    """

    _instance: Optional["AsyncSessionFactory"] = None
    _session: Optional["aiohttp.ClientSession"] = None
    _session_count: int = 0
    _lock: Optional["asyncio.Lock"] = None

    def __new__(cls) -> "AsyncSessionFactory":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_lock(cls) -> asyncio.Lock:
        """Get or create the async lock (thread-safe initialization)."""
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    async def get_session(self) -> "aiohttp.ClientSession":
        """
        Get or create a shared aiohttp.ClientSession.

        Returns:
            Shared ClientSession instance.

        Thread-safe: Uses lock to prevent race conditions during initialization.
        """
        import aiohttp

        async with self.get_lock():
            if self._session is None or self._session.closed:
                connector = aiohttp.TCPConnector(
                    limit=20,
                    ttl_dns_cache=300,
                    use_dns_cache=True,
                )
                timeout = aiohttp.ClientTimeout(total=30)
                self._session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout,
                )
                logger.info("[SESSION] New aiohttp.ClientSession created")
            self._session_count += 1
            return self._session

    async def close_session(self) -> None:
        """Close the current session if exists."""
        async with self.get_lock():
            if self._session is not None and not self._session.closed:
                await self._session.close()
                self._session = None
                logger.info("[SESSION] ClientSession closed")

    @property
    def session_count(self) -> int:
        """Number of sessions created (for debugging/monitoring)."""
        return self._session_count


# =============================================================================
# Sprint 8PC: sprint_mode entrypoint
# =============================================================================

# Sprint 8PC: module-level flag for EMERGENCY state (stops new frontier work)
_sprint_frontier_stopped: bool = False

# Sprint 8TA B.2: Phase timing
_phase_times: dict[str, float] = {}


def _mark_phase(name: str) -> None:
    """Mark phase start time. Called at the beginning of each phase."""
    _phase_times[name] = time.monotonic()
    logger.info(f"[PHASE] {name}")


def _compute_sprint_report_path(sprint_id: str) -> "Path":
    """
    Sprint 8VY §C: Delegates to canonical path owner.

    Canonical owner: paths.get_sprint_report_path()
    Shell no longer holds path computation authority.

    Removal condition: NIKDY — thin delegation seam, not dead code
    """
    from hledac.universal.paths import get_sprint_report_path as _get_path
    return _get_path(sprint_id)


def _render_sprint_report_markdown(
    report: Any,
    scorecard: dict,
    sprint_id: str,
) -> str:
    """
    Sprint 8VJ §B: Delegates to canonical sprint markdown reporter.

    Pure rendering moved to export/sprint_markdown_reporter.py.
    Path computation and file write stay in shell.
    """
    from hledac.universal.export.sprint_markdown_reporter import render_sprint_markdown as _render
    return _render(report, scorecard, sprint_id)


def _export_markdown_report(
    report: Any,
    scorecard: dict,
    sprint_id: str,
) -> Path:
    """
    Sprint 8TC B.4 (refactored 8VY §C): Deleguje rendering na _render_sprint_report_markdown.

    Path computation delegated to paths.get_sprint_report_path() (canonical owner).
    File write stays in shell — orchestration concern.

    Canonical owner: paths.get_sprint_report_path()
    Shell role: orchestration + file write only
    """
    path = _compute_sprint_report_path(sprint_id)
    content = _render_sprint_report_markdown(report, scorecard, sprint_id)
    path.write_text(content, encoding="utf-8")
    logger.info(f"[SPRINT] 📄 Markdown report: {path}")
    return path


async def _print_scorecard_report(
    target: str,
    store: Any,
    sprint_report: Any = None,
) -> None:
    """
    Sprint 8TA B.3: Compute and print sprint scorecard.

    Called at the end of EXPORT phase.
    - findings_per_minute = accepted / (elapsed / 60)
    - ioc_density = ioc_nodes / max(1, accepted)
    - semantic_novelty: 1.0 fallback (no SemanticStore available)
    - source_yield: dict {source_type: count} from per-source counter
    - ghost_global: upsert top IOC entities
    """
    import orjson

    # Get sprint duration from lifecycle
    sprint_id = f"sprint_{int(time.time())}"
    ts = time.time()

    # Compute phase timings dict
    phase_timings: dict[str, float] = {}
    if _phase_times:
        sorted_phases = sorted(_phase_times.items(), key=lambda x: x[1])
        for i, (name, start) in enumerate(sorted_phases):
            if i + 1 < len(sorted_phases):
                end = sorted_phases[i + 1][1]
                phase_timings[name] = round(end - start, 3)
            else:
                phase_timings[name] = 0.0

    # Estimate elapsed from phase timings
    elapsed = sum(phase_timings.values()) if phase_timings else 0.0

    # Get accepted findings from store (duckdb)
    accepted = 0
    ioc_nodes = 0
    source_yield: dict[str, int] = {}
    outlines_used = False

    if store is not None and hasattr(store, "get_dedup_runtime_status"):
        try:
            dedup = store.get_dedup_runtime_status()
            accepted = dedup.get("accepted_count", 0)
        except Exception:
            pass

    # Calculate metrics
    findings_per_minute = accepted / max(1, elapsed / 60.0) if elapsed > 0 else 0.0
    ioc_density = ioc_nodes / max(1, accepted) if accepted > 0 else 0.0
    semantic_novelty = 1.0  # fallback when SemanticStore unavailable

    scorecard_data = {
        "sprint_id": sprint_id,
        "ts": ts,
        "findings_per_minute": round(findings_per_minute, 3),
        "ioc_density": round(ioc_density, 3),
        "semantic_novelty": semantic_novelty,
        "source_yield_json": orjson.dumps(source_yield).decode(),
        "phase_timings_json": orjson.dumps(phase_timings).decode(),
        "outlines_used": outlines_used,
        "accepted_findings": accepted,
        "ioc_nodes": ioc_nodes,
        "synthesis_engine": "unknown",
        # Sprint 8VD §F: Extended scorecard
        "accepted_findings_count": accepted,
        "synthesis_engine_used": "unknown",
        "phase_duration_seconds": phase_timings,
        "cb_open_domains": [],
    }

    # Sprint 8VD §F: Compute peak RSS
    import resource as _resource
    rss_bytes = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
    # macOS: ru_maxrss is in bytes (not KB like on Linux)
    peak_rss_mb = round(rss_bytes / 1024 / 1024, 1)
    scorecard_data["peak_rss_mb"] = peak_rss_mb

    # Sprint 8VB: Circuit breaker state for scorecard
    try:
        from transport.circuit_breaker import get_all_breaker_states
        scorecard_data["cb_open_domains"] = get_all_breaker_states()
    except Exception:
        pass

    # Print structured report
    print("\n" + "=" * 60)
    print("SPRINT 8VD SCORECARD")
    print("=" * 60)
    print(f"  Sprint ID:       {sprint_id}")
    print(f"  Target:           {target[:60]}")
    print(f"  Elapsed:          {elapsed:.1f}s")
    print(f"  Accepted:         {accepted}")
    print(f"  Findings/min:     {findings_per_minute:.2f}")
    print(f"  IOC density:      {ioc_density:.3f}")
    print(f"  Semantic novelty: {semantic_novelty:.3f}")
    print(f"  Outlines used:    {outlines_used}")
    print(f"  Peak RSS (MB):    {peak_rss_mb:.1f}")
    print(f"  Phase timings:    {phase_timings}")
    print("=" * 60 + "\n")

    # Persist to DuckDB
    if store is not None and hasattr(store, "upsert_scorecard"):
        try:
            await store.upsert_scorecard(scorecard_data)
        except Exception as e:
            logger.warning("[SCORECARD] Failed to persist: %s", e)

    # Sprint 8UC B.2.4: Persist research episode
    if store is not None and hasattr(store, "upsert_episode"):
        try:
            import time as _t
            top_findings_list = []
            if sprint_report is not None and hasattr(sprint_report, "findings"):
                top_findings_list = [f.content if hasattr(f, "content") else str(f)
                                     for f in (sprint_report.findings or [])[:5]]
            await store.upsert_episode({
                "sprint_id": sprint_id,
                "query": target,
                "summary": sprint_report.threat_summary if sprint_report and hasattr(sprint_report, "threat_summary") else "",
                "top_findings": top_findings_list,
                "ioc_clusters": [],
                "source_yield": scorecard_data.get("source_yield_json", "{}"),
                "synthesis_engine": scorecard_data.get("synthesis_engine", "unknown"),
                "duration_s": elapsed,
                "ts": _t.time(),
            })
            logger.info(f"[SCORECARD] Research episode saved: {sprint_id}")
        except Exception as e:
            logger.warning("[SCORECARD] Failed to persist episode: %s", e)

    # Sprint 8TC B.4: Markdown report export
    md_path = _export_markdown_report(sprint_report, scorecard_data, sprint_id)
    print(f"Report saved: {md_path}")

    # Sprint 8TF §2: ghost_global upsert (top IOC entities from this sprint)
    # REMOVED: direct graph spelunking (graph.get_nodes()[:100]) — method never existed
    #          on any graph backend, this path was always silently dead.
    # REPLACED WITH: duckdb_store.get_top_entities_for_ghost_global() bounded store seam.
    #                Returns list[tuple] matching upsert_global_entities() signature.
    #                STORE IS NOT GRAPH TRUTH OWNER — seam is a read-only adapter.
    if store is not None and hasattr(store, "get_top_entities_for_ghost_global"):
        try:
            entities = store.get_top_entities_for_ghost_global(n=100)
            if entities and hasattr(store, "upsert_global_entities"):
                n_upserted = await store.upsert_global_entities(entities)
                logger.info("[SCORECARD] ghost_global: %d entities upserted", n_upserted)
        except Exception:
            pass

    # Sprint 8VZ §B: FIRST producer-side cutover — canonical path constructs
    # ExportHandoff(...) directly. scorecard_data is kept for persistence and
    # markdown (duckdb upsert, _export_markdown_report), but is NO LONGER the
    # canonical source for top_nodes in the export handoff.
    #
    # CANONICAL PRODUCER TRUTH (post-8VZ):
    #   ExportHandoff(...) — constructed directly at producer side
    #   top_nodes sourced from store.get_top_seed_nodes() (store-facing seam)
    #
    # COMPAT LEFTOVERS (kept for backward compat / other consumers):
    #   scorecard_data dict — still persisted to DuckDB, still used by markdown
    #   from_windup(scorecard) — COMPAT ONLY, used only by legacy call-sites
    #
    # REMOVAL CONDITIONS SHORTENED by this cutover:
    #   - from_windup(scorecard) now explicitly compat-only — __main__ uses direct ctor
    #   - Two-chained-seams gone: no more windup dict → scorecard dict → ExportHandoff
    #   - scorecard["top_graph_nodes"] no longer the canonical top_nodes source
    #
    # Graph fallback (store.get_top_seed_nodes) is ACCEPTED COMPAT SEAM.
    # REMOVAL CONDITION: ExportHandoff.top_nodes always populated in all windup paths.
    try:
        from export.sprint_exporter import export_sprint as _export_sprint
        from .types import ExportHandoff

        # Sprint 8VZ §B: Construct typed handoff directly — canonical producer truth
        # top_nodes from store seam (DuckPGQGraph-backed store.get_top_seed_nodes)
        _top_nodes: list = []
        if store is not None:
            try:
                if hasattr(store, "get_top_seed_nodes"):
                    _top_nodes = store.get_top_seed_nodes(n=10)
            except Exception:
                pass

        handoff = ExportHandoff(
            sprint_id=sprint_id,
            scorecard=scorecard_data,
            top_nodes=_top_nodes,
            phase_durations=phase_timings,
        )
        export_result = await _export_sprint(store, handoff)
        logger.info("[SCORECARD] Sprint export: JSON=%s, seeds=%s",
                     export_result.get("report_json", ""),
                     export_result.get("seeds_json", ""))
    except Exception as e:
        logger.warning("[SCORECARD] export_sprint() failed (non-fatal): %s", e)


async def _run_sprint_mode(
    target: str,
    duration_s: float = 1800.0,
    install_signal_handlers: bool = False,
) -> None:
    """
    F162C NON-CANONICAL ALTERNATE: This is NOT the canonical sprint owner.
    Sprint 8PC: 30-minute autonomous sprint cycle entrypoint.

    BOOT → WARMUP (5s) → ACTIVE (parallel pipeline runs)
           → WINDUP (T-3min) → EXPORT → TEARDOWN

    Canonical sprint owner is core.__main__.run_sprint().
    This function is a residual/alternate path — prefer canonical owner.

    In ACTIVE:
        - Runs live_feed_pipeline.async_run_default_feed_batch() every 60s
          or until remaining_time <= 3min
        - UMAAlarmDispatcher monitors memory and dispatches callbacks

    Args:
        target: Query string passed to the feed pipeline
        duration_s: Sprint duration in seconds (default 1800 = 30min)
        install_signal_handlers: If True, install SIGINT/SIGTERM handlers
            inside this coroutine (uses the real event loop from asyncio.run())
    """
    from .core.resource_governor import (
        UMAAlarmDispatcher,
        UMA_STATE_CRITICAL,
        UMA_STATE_EMERGENCY,
    )
    from .runtime.sprint_lifecycle import SprintLifecycleManager, SprintPhase
    from .runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

    global _sprint_frontier_stopped, _active_pipeline_iterations
    _sprint_frontier_stopped = False
    _active_pipeline_iterations = 0

    # Sprint 8TA fix: install signal handlers inside asyncio.run() so we get the real loop
    if install_signal_handlers:
        _install_signal_teardown(asyncio.get_running_loop())

    lifecycle = SprintLifecycleManager()
    lifecycle.sprint_duration_s = duration_s

    # Sprint 8VY: SprintScheduler for WARMUP orchestration (DuckPGQ, IOCScorer, ring buffers)
    # Created here so run_warmup() can initialize scheduler state during WARMUP phase.
    scheduler = SprintScheduler(SprintSchedulerConfig(sprint_duration_s=duration_s))

    # B.5: AsyncExitStack for LIFO teardown
    exit_stack: Optional[contextlib.AsyncExitStack] = None
    try:
        exit_stack = contextlib.AsyncExitStack()
        await exit_stack.__aenter__()

        # ---- BOOT ----
        lifecycle.begin_sprint()
        _boot_record("sprint_mode", "BOOT")
        _mark_phase("BOOT")

        # ---- WARMUP (5s) ---- Sprint 8VY: single WARMUP truth via run_warmup()
        # Inline WARMUP block replaced by canonical run_warmup() call.
        # run_warmup() handles: preflight, DuckPGQ, IOCScorer, ring buffers,
        # lifecycle transition (WARMUP→ACTIVE), phase telemetry, and ANE warmup.
        await asyncio.sleep(5.0)
        await run_warmup(scheduler, {}, lifecycle=lifecycle, do_ane_warmup=True)

        # ---- ACTIVE: start UMA monitoring ----
        dispatcher = UMAAlarmDispatcher()

        # CRITICAL callback: reduce concurrency to 1 + clear Metal cache (Sprint 8UF B.2)
        async def _on_critical():
            global _sprint_frontier_stopped
            logger.warning("[SPRINT] UMA CRITICAL — reducing concurrency to 1")
            _sprint_frontier_stopped = True
            try:
                import mlx.core as mx
                mx.metal.clear_cache()
            except Exception:
                pass

        # EMERGENCY callback: stop new frontier work + clear Metal cache + gc.collect() (Sprint 8UF B.2)
        async def _on_emergency():
            global _sprint_frontier_stopped
            logger.critical("[SPRINT] UMA EMERGENCY — stopping new frontier work")
            _sprint_frontier_stopped = True
            try:
                import mlx.core as mx
                mx.metal.clear_cache()
            except Exception:
                pass
            import gc
            gc.collect()

        dispatcher.register_callback(UMA_STATE_CRITICAL, _on_critical)
        dispatcher.register_callback(UMA_STATE_EMERGENCY, _on_emergency)
        await dispatcher.start_monitoring(interval_s=5.0)

        _boot_record("sprint_mode", "ACTIVE")
        _mark_phase("ACTIVE")

        # ---- ACTIVE: pipeline runs every 60s while remaining > 3min ----
        from .pipeline.live_feed_pipeline import async_run_default_feed_batch
        from .pipeline.live_public_pipeline import async_run_live_public_pipeline
        from .knowledge.duckdb_store import create_owned_store

        store_instance = None
        try:
            store_instance = create_owned_store()
            await store_instance.async_initialize()
        except Exception as e:
            logger.warning(f"[SPRINT] Store init failed (continuing without store): {e}")
            store_instance = None

        last_pipeline_time = 0.0

        # Sprint 8SA: Configure bootstrap patterns ONCE before first pipeline run
        from .patterns.pattern_matcher import configure_default_bootstrap_patterns_if_empty
        configure_default_bootstrap_patterns_if_empty()

        # Sprint 8WL: Wire truth-write graph BEFORE active loop so buffered IOC writes
        # are not silent no-op. IOCGraph is lightweight (~10MB Kuzu open, no MLX).
        # Without this, _truth_write_graph is None in ACTIVE → _graph_ingest_findings()
        # never fires. WINDUP block keeps inject_stix_graph for synthesis.
        if store_instance is not None:
            try:
                from .knowledge.ioc_graph import IOCGraph
                ioc_graph = IOCGraph()
                await ioc_graph.initialize()
                store_instance.inject_truth_write_graph(ioc_graph)
                logger.info("[SPRINT 8WL] IOCGraph injected: truth_write_graph (ACTIVE)")
            except Exception as e:
                logger.warning(f"[SPRINT 8WL] IOCGraph init failed (continuing without): {e}")

        while lifecycle.current_phase == SprintPhase.ACTIVE:
            await asyncio.sleep(1.0)

            # Check windup condition (T-3min remaining)
            if lifecycle.remaining_time() <= 180.0:
                lifecycle.request_windup()
                break

            # Sprint 8UF B.2: Skip pipeline if UMA emergency stopped frontier
            if _sprint_frontier_stopped:
                await asyncio.sleep(5)
                # E1-T1: ACTIVE runaway guard — if frontier stopped but >3min remain,
                # force windup to prevent the continue-loop from running forever.
                if lifecycle.remaining_time() > 180.0:
                    logger.warning("[SPRINT] _sprint_frontier_stopped=True with >3min "
                                   "remaining — forcing windup to prevent runaway")
                    lifecycle.request_windup()
                break

            # Run pipelines every 60s — both in parallel via TaskGroup
            now = time.monotonic()
            if now - last_pipeline_time >= 60.0:
                if store_instance is not None:
                    try:
                        async with asyncio.TaskGroup() as tg:
                            tg.create_task(async_run_live_public_pipeline(
                                query=target,
                                store=store_instance,
                                max_results=5,
                            ))
                            tg.create_task(async_run_default_feed_batch(
                                store=store_instance,
                                max_entries_per_feed=10,
                                query_context=target,
                            ))
                        _boot_record("sprint_mode", "pipeline_run_ok")
                    except Exception as e:
                        _boot_record("sprint_mode", "pipeline_run_error", error=str(e))
                    else:
                        _active_pipeline_iterations += 1
                        last_pipeline_time = now

        # ---- WINDUP ----
        lifecycle.request_windup()
        _boot_record("sprint_mode", "WINDUP")
        _mark_phase("WINDUP")

        # Sprint 8VQ: Create IOCGraph truth-store for STIX capability.
        # IOCGraph is the ONLY backend with export_stix_bundle().
        # DuckPGQGraph is analytics/donor — lacks STIX capability.
        # We create it here in WINDUP (after ACTIVE phase collected IOCs)
        # and inject into store for synthesis consumption.
        # Note: inject_truth_write_graph was moved to ACTIVE start (Sprint 8WL) so
        # buffered IOC writes are not silent no-op during ACTIVE phase.
        if store_instance is not None:
            try:
                from .knowledge.ioc_graph import IOCGraph
                ioc_graph = IOCGraph()
                await ioc_graph.initialize()
                # Sprint 8VQ: Dedicated STIX-only slot — independent of analytics graph
                store_instance.inject_stix_graph(ioc_graph)
                logger.info("[SPRINT 8VQ] IOCGraph injected: stix_graph (WINDUP)")
            except Exception as e:
                logger.warning(f"[SPRINT 8VQ] IOCGraph init failed (STIX unavailable): {e}")

        # Sprint 8VB: Circuit Breaker stats
        from transport.circuit_breaker import get_all_breaker_states
        _cb = get_all_breaker_states()
        _open_cb = [d for d, s in _cb.items() if s == "open"]
        logger.info(
            f"[8VB-CB] breakers={len(_cb)} open={len(_open_cb)} "
            f"domains={_open_cb[:5]}"
        )

        # Sprint 8VE B.4: DuckPGQ IOC Graph stats
        _top_iocs = []
        if store_instance is not None:
            try:
                _top_iocs = await store_instance.get_top_findings(limit=10)
            except Exception:
                pass
        # Sprint 8VY §A: Analytics graph stats via store seam (no private-slot access)
        # Previously: getattr(store_instance, "_ioc_graph", None).stats()
        if store_instance is not None:
            gs = store_instance.get_graph_stats() if hasattr(store_instance, "get_graph_stats") else {}
            if gs:
                logger.info(
                    f"[GRAPH] nodes={gs['nodes']} edges={gs['edges']} "
                    f"pgq={gs.get('pgq_available', gs.get('pgq_active'))}"
                )
                if _top_iocs:
                    first_ioc = _top_iocs[0].get("ioc") if isinstance(_top_iocs[0], dict) else None
                    if first_ioc:
                        connected = store_instance.get_connected_iocs(first_ioc, max_hops=2) if hasattr(store_instance, "get_connected_iocs") else []
                        if connected:
                            logger.info(f"[GRAPH] {first_ioc} → {len(connected)} connected nodes")

        # Sprint 8QC + 8TC: E2E synthesis — runs in WINDUP, report captured for EXPORT
        windup_report = None
        if store_instance is not None:
            try:
                windup_report = await _windup_synthesis(
                    target,
                    store_instance,
                    lifecycle,
                )
            except Exception as e:
                logger.warning("[SPRINT] Windup synthesis failed (non-fatal): %s", e)

        # Sprint 8VF §C.3: ANE embedder status log at WINDUP
        try:
            from hledac.universal.brain.ane_embedder import get_ane_embedder
            engine = "ANE-MiniLM" if get_ane_embedder() else "hash-fallback"
            logger.info(f"[ANE] synthesis_engine={engine}")
        except Exception:
            pass

        # Drain existing tasks — don't start new ones
        while lifecycle.current_phase == SprintPhase.WINDUP:
            await asyncio.sleep(1.0)
            if lifecycle.remaining_time() <= 60.0:
                lifecycle.request_export()
                break

        # ---- EXPORT ----
        _boot_record("sprint_mode", "EXPORT")
        _mark_phase("EXPORT")
        logger.info("[SPRINT] Final stats:")
        if store_instance is not None:
            try:
                dedup = store_instance.get_dedup_runtime_status()
                logger.info(f"[SPRINT] Dedup status: {dedup}")
            except Exception:
                pass
        lifecycle.request_export()
        await asyncio.sleep(1.0)  # allow export to settle

        # Sprint 8TA B.3 + 8TC B.4: Compute and persist sprint scorecard + Markdown export
        _mark_phase("DONE")
        await _print_scorecard_report(target, store_instance, sprint_report=windup_report)

        # ---- TEARDOWN ----
        _boot_record("sprint_mode", "TEARDOWN")
        _mark_phase("TEARDOWN")
        await dispatcher.stop()

    except asyncio.CancelledError:
        _boot_record("sprint_mode", "cancelled")
        raise
    finally:
        if exit_stack is not None:
            try:
                await exit_stack.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"[SPRINT] AsyncExitStack unwind error: {e}")


# =============================================================================
# Sprint 8QC: WINDUP Synthesis E2E wiring
# =============================================================================


async def _windup_synthesis(
    query: str,
    store: Any,
    lifecycle: "SprintLifecycleManager",
) -> Any:
    """
    Sprint 8QC E2E: Synthesis in WINDUP phase.

    1. Creates SynthesisRunner with ModelLifecycle
    2. Injects graph (if available from IOCGraph)
    3. Gets top findings from DuckDB store
    4. Calls synthesize_findings (WINDUP-only, force=False)
    5. Exports report to ~/.hledac/reports/{ts}_{slug}_report.json
    6. Closes runner
    """
    from .brain.model_lifecycle import ModelLifecycle
    from .brain.synthesis_runner import SynthesisRunner, export_report

    runner = SynthesisRunner(ModelLifecycle())

    # Sprint 8VQ: Priority 1 — dedicated STIX truth-store graph (IOCGraph/Kuzu)
    # Created in _run_sprint_mode WINDUP block and injected via store.inject_stix_graph()
    try:
        stix_graph = store.get_stix_graph() if hasattr(store, "get_stix_graph") else None
        if stix_graph is not None:
            runner.inject_stix_graph(stix_graph)
        else:
            # Sprint 8VY: Priority 2 — analytics/donor graph via explicit seam
            # Previously: elif hasattr(store, "_ioc_graph") and store._ioc_graph: runner.inject_graph(store._ioc_graph)
            analytics_graph = store.get_analytics_graph_for_synthesis() if hasattr(store, "get_analytics_graph_for_synthesis") else None
            if analytics_graph is not None:
                runner.inject_graph(analytics_graph)
    except Exception:
        pass

    # Sprint 8UC B.2: Inject DuckDB store for episode recall
    runner._duckdb_store = store

    # Sprint 8WD: Inject runtime lifecycle — PREFERRED truth for windup gate
    # runtime/_windup_synthesis() ACTIVE path: lifecycle param is the canonical runtime manager
    if lifecycle is not None:
        runner.inject_lifecycle_adapter(lifecycle)

    # Get top findings from store
    findings: list[dict] = []
    try:
        if hasattr(store, "get_top_findings"):
            findings = await store.get_top_findings(limit=15)
        elif hasattr(store, "get_recent_findings"):
            findings = await store.get_recent_findings(limit=15)
    except Exception as e:
        logger.warning("[WINDUP] Could not fetch findings from store: %s", e)

    if not findings:
        logger.info("[WINDUP] No findings available for synthesis")
        await runner.close()
        return None

    # Run synthesis (WINDUP phase check is inside synthesize_findings)
    report = await runner.synthesize_findings(
        query=query,
        findings=findings,
        force_synthesis=True,  # B.7: explicit force for programmatic call
    )

    # Sprint 8VA D: HypothesisEngine closed loop — generate hypotheses from findings
    if findings and len(findings) > 0:
        try:
            from hledac.universal.brain.hypothesis_engine import HypothesisEngine
            _hyp_engine = HypothesisEngine()
            finding_texts = [f.get("text", "")[:200] for f in findings[:10]]
            hypotheses = _hyp_engine.generate_sprint_hypotheses(
                findings=finding_texts,
                ioc_graph=None,
                max_hypotheses=3,
            )
            # Sprint 8VA D.2: Každá hypotéza → logged (pivot_queue requires SprintScheduler access)
            for i, hyp in enumerate(hypotheses or [], 1):
                hyp_text = hyp if isinstance(hyp, str) else str(hyp)
                logger.info(f"[8VA] Hypothesis {i}: {hyp_text[:80]}")
        except Exception as e:
            logger.debug(f"[8VA] HypothesisEngine skipped: {e}")

    # Sprint 8UC B.2.4: Capture synthesis engine for scorecard
    synthesis_engine = getattr(runner, '_last_synthesis_engine', 'unknown')

    await runner.close()

    if report is not None:
        # Export to JSON
        await export_report(report, query)
        logger.info("[WINDUP] Synthesis complete: %d IOCs, %d threat actors",
                     len(report.ioc_entities), len(report.threat_actors))
    else:
        logger.info("[WINDUP] Synthesis returned None (no model or UMERGENCY)")

    return report


# =============================================================================
# Main entry point
# =============================================================================

def main() -> None:
    """
    Synchronous entry point.

    Sprint 8AI: Boot flow is clearly separated:
    1. Synchronous pre-boot (uvloop, boot guard, signal handlers)
    2. Async runtime via asyncio.run()
    3. AsyncExitStack-backed teardown happens inside async context
    """
    # Configure basic logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Sprint 7C: process masking — rename process for reduced visibility
    try:
        import setproctitle
        setproctitle.setproctitle("kernel_worker")
    except ImportError:
        pass

    # Sprint 8PC: CLI parsing for --sprint flag
    sprint_target: Optional[str] = None
    sprint_duration: float = 1800.0
    if "--sprint" in sys.argv:
        idx = sys.argv.index("--sprint")
        if idx + 1 < len(sys.argv):
            sprint_target = sys.argv[idx + 1]
        if idx + 2 < len(sys.argv) and sys.argv[idx + 2].replace(".", "", 1).isdigit():
            sprint_duration = float(sys.argv[idx + 2])

    # Sprint 8AI: Step 1 — Synchronous pre-boot
    # Run LMDB boot guard (8AG) BEFORE any runtime acquisition
    # This is synchronous and must run outside the event loop
    _boot_record("boot_guard_sync", "starting")
    try:
        removed, reason = _run_boot_guard()
        logger.info(f"[BOOT GUARD] result: removed={removed}, reason={reason}")
        _boot_record("boot_guard_sync", "ok", removed=removed, reason=reason)
    except BootGuardError as e:
        logger.error(f"[BOOT GUARD] Unsafe state detected: {e}")
        _boot_record("boot_guard_sync", "unsafe_abort", error=str(e))
        sys.exit(1)
    except Exception as e:
        # Fail-soft: log but don't abort boot
        logger.warning(f"[BOOT GUARD] Guard error (continuing): {e}")
        _boot_record("boot_guard_sync", "error_soft", error=str(e))

    try:
        if sprint_target is not None:
            # Sprint F150R: Delegate to canonical sprint owner in core/__main__.py
            # No new scheduler, no compat layer — thin delegation only
            from .core.__main__ import run_sprint as _core_run_sprint
            asyncio.run(_core_run_sprint(
                query=sprint_target,
                duration_s=sprint_duration,
            ))
        else:
            # Sprint 8AM C.1: Async runtime with owned resources via _run_public_passive_once
            asyncio.run(_run_public_passive_once(_get_and_clear_signal_flag))
    except KeyboardInterrupt:
        logger.info("[MAIN] Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"[MAIN] Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


# =============================================================================
# Sprint 8VX §D: run_warmup() — moved from runtime/sprint_lifecycle.py
# This is WARMUP-phase orchestration, NOT lifecycle state machine.
# Kept at module level (no SprintScheduler dependency in sprint mode).
# =============================================================================

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .sprint_scheduler import SprintScheduler

_logger = logging.getLogger(__name__)


async def run_warmup(
    scheduler: "SprintScheduler",
    config: dict,
    lifecycle: "Optional[Any]" = None,
    do_ane_warmup: bool = False,
) -> dict:
    """
    F162C NON-CANONICAL RESIDUAL: This function lives in root __main__.py
    (residual/alternate entrypoint) but claims to be the "canonical WARMUP
    orchestration truth" — that claim is misleading. Canonical WARMUP truth
    lives in core.__main__.run_sprint() lifecycle, not here.

    This is a SHARED utility called from:
      - _run_sprint_mode() (alternate sprint hot-path)
      - test_e2e_dry_run.py (test, without lifecycle)
    Prefer the canonical WARMUP orchestration in core lifecycle.

    Args:
        scheduler: SprintScheduler instance (或其 mock)
        config: sprint config dict
        lifecycle: SprintLifecycleManager instance pro lifecycle transitions.
                  Pokud None, přeskočí se lifecycle ops (test compatibility).
        do_ane_warmup: True = provést ANE embedder warmup (pouze v sprint hot-path)
    """
    t_start = time.monotonic()

    # 1. Preflight check
    preflight: dict[str, Any] = {}
    try:
        preflight = await _preflight_check()
    except Exception as e:
        _logger.warning(f"[WARMUP] _preflight_check failed: {e}")

    # 2. None soubor guard
    none_path = __import__("pathlib").Path("None")
    if none_path.exists():
        _logger.error("[P0] Soubor 'None' existuje — spusť git rm --cached None")

    # 3. DuckPGQGraph init + merge předchozích dat
    if not hasattr(scheduler, "_ioc_graph") or scheduler._ioc_graph is None:
        try:
            from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph
            from hledac.universal.paths import SPRINT_STORE_ROOT
            import glob
            scheduler._ioc_graph = DuckPGQGraph()
            prev_glob = str(SPRINT_STORE_ROOT / "*" / "batch_*.parquet")
            if glob.glob(prev_glob):
                count = scheduler._ioc_graph.merge_from_parquet(prev_glob)
                _logger.info(f"[WARMUP] DuckPGQ merged {count} nodes")
        except Exception as e:
            _logger.warning(f"[WARMUP] DuckPGQ init: {e}")
            scheduler._ioc_graph = None

    # 4. IOCScorer lazy init
    if not hasattr(scheduler, "_ioc_scorer") or scheduler._ioc_scorer is None:
        try:
            from hledac.universal.brain.ner_engine import IOCScorer
            scheduler._ioc_scorer = IOCScorer()
        except Exception as e:
            _logger.warning(f"[WARMUP] IOCScorer init: {e}")
            scheduler._ioc_scorer = None

    # 5. Ring buffer a RL state
    if not hasattr(scheduler, "_recent_iocs"):
        scheduler._recent_iocs = []
    if not hasattr(scheduler, "_pivot_rewards"):
        scheduler._pivot_rewards = {}
    if not hasattr(scheduler, "_all_findings"):
        scheduler._all_findings = []

    # 6. Lifecycle WARMUP→ACTIVE transition (pouze když máme lifecycle manager)
    if lifecycle is not None:
        lifecycle.mark_warmup_done()
        _boot_record("sprint_mode", "WARMUP→ACTIVE")
        _mark_phase("WARMUP")

    # 7. ANE embedder warmup (pouze v sprint hot-path, kde je potřeba)
    if do_ane_warmup:
        try:
            from .brain.ane_embedder import ANEEmbedder
            ane = ANEEmbedder()
            await ane.warmup()
        except Exception as e:
            _logger.debug(f"[WARMUP] ANE warmup skipped: {e}")

    return {
        "preflight": preflight,
        "t_warmup_start": t_start,
        "t_warmup_end": time.monotonic(),
    }
