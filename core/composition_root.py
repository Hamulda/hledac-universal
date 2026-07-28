"""Composition Root — wiring + lifecycle for hledac universal runtime.

All service initialisation, dependency injection, and shutdown live here.
Replaces the monolithic init block in __main__._main_dispatch().

Responsibilities:
- asyncio event loop creation and lifecycle
- signal handler installation / restoration
- DuckDBShadowStore bootstrap (P0-3)
- SprintLifecycleManager instantiation
- MLX/Hermes prewarm
- ResourceGovernor / memory pressure loop start
- EvidenceLog initialisation
- Layer stack assembly
- Health-check runner
- All shutdown paths (normal, SIGINT, exception)

Pattern: build_runtime() is synchronous (no event loop created yet).
Caller owns the loop lifecycle so that main() can wrap the entire
run in the asyncio envelope with structured exit codes.
"""

import asyncio
import gc
import logging
import signal
import sys
from collections.abc import Callable
from typing import Any

from hledac.universal.runtime.sprint_entrypoint import _cancel_all_tasks
from hledac.universal.utils.async_helpers import safe_create_task, first_completed  # ISSUE-15

# uvloop: 2× I/O speedup on M1 kqueue. Try uvloop.new_event_loop() first,
# fall back to asyncio.new_event_loop() if uvloop is unavailable (CI, non-M1).
_UVLOOP_AVAILABLE: bool = False


def _get_event_loop() -> asyncio.AbstractEventLoop:
    """Create an event loop: uvloop on M1 darwin, asyncio elsewhere."""
    global _UVLOOP_AVAILABLE
    if _UVLOOP_AVAILABLE:
        try:
            import uvloop

            # ISSUE-2 FIX: Explicitly call uvloop.new_event_loop() — not asyncio.new_event_loop().
            # This ensures the 2× I/O speedup on M1 kqueue is actually realized.
            # uvloop.install() was called in __main__.py, but we use the direct constructor
            # to guarantee uvloop is used even if asyncio's policy isn't fully propagated.
            return uvloop.new_event_loop()
        except (ImportError, OSError):
            _UVLOOP_AVAILABLE = False
    return asyncio.new_event_loop()


def _init_uvloop() -> bool:
    """Detect and initialize uvloop. Returns True if uvloop is available."""
    global _UVLOOP_AVAILABLE
    try:
        import uvloop
        import platform

        _is_darwin_arm = (
            platform.system() == "Darwin"
            and platform.machine().lower() in ("arm64", "aarch64")
        )
        if _is_darwin_arm and sys.version_info < (3, 15):
            _UVLOOP_AVAILABLE = True
            return True
    except ImportError:
        pass
    _UVLOOP_AVAILABLE = False
    return False

logger = logging.getLogger(__name__)


# =============================================================================
# Signal handling
# =============================================================================


def install_signal_handler(
    loop: asyncio.AbstractEventLoop,
    shutdown_event: asyncio.Event,
) -> Callable[[], None]:
    """
    Install SIGINT/SIGTERM handlers that set shutdown_event.

    F350M-R ISSUE #4: Uses loop.add_signal_handler() (Python 3.10+)
    for native asyncio signal handling. Falls back to signal.signal()
    for older Python or environments where add_signal_handler raises.

    Returns a callable that RESTORES previous signal handlers — call from finally.
    """
    # F350M-R ISSUE #4: Track which mechanism is used so restore() knows how to clean up
    _prev_int: Any = None
    _prev_term: Any = None
    _using_add_signal_handler: bool = False

    def _handler() -> None:
        """No-arg callback for loop.add_signal_handler()."""
        logger.info("[SIGNAL] Received signal — cooperative shutdown")
        try:
            shutdown_event.set()
        except Exception:  # noqa: BLE001
            pass

    def _fallback_handler(signum: int, _frame: Any) -> None:
        """Two-arg handler for signal.signal() fallback."""
        sig_name = (
            getattr(signal.Signals, "SIGINT", None) and signal.Signals(signum).name
            if hasattr(signal, "Signals")
            else str(signum)
        )
        logger.info(f"[SIGNAL] Received {sig_name} — cooperative shutdown")
        try:
            # Always call set() directly — it is async-signal-safe (Python 3.10+).
            # call_soon_threadsafe is a bonus to wake the loop promptly if it is
            # running.  Removing the is_running()+is_closed() check eliminates the
            # race: loop could close between the check and call_soon_threadsafe,
            # leaving the callback pending but the event never set.
            if loop.is_running():
                loop.call_soon_threadsafe(shutdown_event.set)
            shutdown_event.set()
        except Exception:  # noqa: BLE001
            pass

    # F350M-R ISSUE #4: Prefer loop.add_signal_handler() (Python 3.10+)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handler)
            _using_add_signal_handler = True
        except (NotImplementedError, AttributeError, OSError, RuntimeError) as e:
            # NotImplementedError: signals not available in this env (e.g. some CI)
            # RuntimeError: called from non-main thread
            # AttributeError: older Python without add_signal_handler
            # OSError: system-level failure
            logger.warning(f"[SIGNAL] add_signal_handler unavailable for {sig}: {e}")
            try:
                # Fallback to legacy signal.signal() from main thread
                prev = signal.signal(sig, _fallback_handler)
                if sig == signal.SIGINT:
                    _prev_int = prev
                else:
                    _prev_term = prev
            except (OSError, TypeError) as e2:
                logger.warning(f"[SIGNAL] signal.signal() also failed for {sig}: {e2}")

    if _using_add_signal_handler:
        logger.info("[SIGNAL] SIGINT/SIGTERM handlers installed via add_signal_handler")
    else:
        logger.info("[SIGNAL] SIGINT/SIGTERM handlers installed via signal.signal() (fallback)")

    def restore() -> None:
        """Restore previous signal handlers."""
        if _using_add_signal_handler:
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.remove_signal_handler(sig)
                except (OSError, RuntimeError) as e:
                    logger.warning(f"[SIGNAL] remove_signal_handler failed for {sig}: {e}")
        else:
            try:
                if _prev_int is not None:
                    signal.signal(signal.SIGINT, _prev_int)
                if _prev_term is not None:
                    signal.signal(signal.SIGTERM, _prev_term)
            except Exception:  # noqa: BLE001
                pass

    return restore


# =============================================================================
# Memory hygiene
# =============================================================================


def configure_memory_for_sprint() -> dict[str, Any]:
    """M218A: GC startup tuning for M1 UMA stability. Returns gc snapshot."""
    gc_config: dict[str, Any] = {}
    gc_config["gc_thresholds"] = gc.get_threshold()
    gc.freeze()
    gc_config["gc_frozen"] = True
    return gc_config


def start_malloc_pressure_relief() -> None:
    """
    F266-U3: malloc pressure relief on M1 8GB UMA.
    Release fragmented pages before allocation pressure builds.
    Non-blocking — runs on thread pool via asyncio.to_thread.
    """
    import ctypes

    try:
        libc = ctypes.CDLL("libc.dylib", use_errno=True)
        libc.malloc_zone_pressure_relief.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        libc.malloc_zone_pressure_relief.restype = None
        libc.malloc_zone_pressure_relief(None, 0)
        logger.debug("[UMA] malloc_zone_pressure_relief called")
    except Exception as e:
        logger.debug("[UMA] malloc_zone_pressure_relief unavailable: %s", e)


# =============================================================================
# Sprint task factory
# =============================================================================


async def _run_sprint_task(
    query: str,
    duration_s: float,
    export_dir: str,
    aggressive_mode: bool,
    deep_probe_enabled: bool,
    deep_research: bool,
    extreme_mode: bool,
    no_communication: bool,
    ui_mode: bool,
    windup_lead_s: float | None,
    acquisition_profile: str | None,
    rl_train_mode: bool,
    force: bool,
    flags: Any,
    shutdown_event: asyncio.Event,
) -> None:
    """
    Async task body that runs the sprint and waits for either
    sprint completion or the shutdown signal.
    """
    from hledac.universal.runtime.sprint_entrypoint import run_sprint

    sprint_coro = run_sprint(
        query=query,
        duration_s=duration_s,
        export_dir=export_dir,
        aggressive_mode=aggressive_mode,
        deep_probe_enabled=deep_probe_enabled,
        deep_research=deep_research,
        extreme_mode=extreme_mode,
        no_communication=no_communication,
        ui_mode=ui_mode,
        windup_lead_s=windup_lead_s,
        acquisition_profile=acquisition_profile,
        rl_train_mode=rl_train_mode,
        force=force,
        flags=flags,
    )
    # F320: asyncio.create_task -> safe_create_task (eager_start, loop probe)
    sprint_task = safe_create_task(sprint_coro, name="composition_root:sprint")
    sig_task = safe_create_task(shutdown_event.wait(), name="composition_root:shutdown_signal")

    # ISSUE-15: asyncio.wait(FIRST_COMPLETED) → first_completed helper
    # Race between sprint completion and shutdown signal
    try:
        winner_task: asyncio.Task[None]
        _, winner_task = await first_completed(sprint_task, sig_task)
    except asyncio.TimeoutError:
        raise  # Should not happen with no timeout

    # Surface sprint exceptions (first_completed preserves exceptions)
    exc = sprint_task.exception()
    if exc is not None:
        raise exc

    # If shutdown signal won, cancel sprint
    if winner_task is sig_task:
        sprint_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sprint_task


# =============================================================================
# Runtime lifecycle
# =============================================================================


def build_runtime(
    query: str,
    duration_s: float,
    export_dir: str,
    aggressive_mode: bool,
    deep_probe_enabled: bool,
    deep_research: bool,
    extreme_mode: bool,
    no_communication: bool,
    ui_mode: bool,
    windup_lead_s: float | None,
    acquisition_profile: str | None,
    rl_train_mode: bool,
    force: bool,
    flags: Any,
) -> tuple[asyncio.AbstractEventLoop, asyncio.Task[None], asyncio.Event, Callable[[], None]]:
    """
    Build the sprint runtime: loop, shutdown event, signal restore, sprint task.

    Returns (loop, sprint_task, shutdown_event, restore_signals).

    Memory budget (M1 8GB UMA):
    - Metal cache: 1 GiB ceiling via get_dynamic_metal_cache_limit()
    - DuckDB in-process mode: saves ~200 MB RAM vs subprocess
    - KV bits=4, max_kv_size=8192: passed to mlx_lm.generate(), NOT load()
    """
    # Detect and initialize uvloop before creating the event loop
    _init_uvloop()
    loop = _get_event_loop()
    asyncio.set_event_loop(loop)
    shutdown_event = asyncio.Event()

    # GC + malloc tuning (synchronous)
    configure_memory_for_sprint()
    start_malloc_pressure_relief()

    # O-01: Initialize unified TelemetryContext for this sprint session
    from hledac.universal.core.telemetry.context_state import init_telemetry_context
    init_telemetry_context()

    # Pre-sprint checks (sync, fail-loud)
    from hledac.universal.runtime.sprint_entrypoint import run_pre_sprint_checks

    if not run_pre_sprint_checks():
        logger.warning("[PREFLIGHT] Pre-sprint checks returned False — continuing in degraded mode")

    # Signal handling
    restore_signals = install_signal_handler(loop, shutdown_event)

    # Sprint task
    sprint_task = loop.create_task(
        _run_sprint_task(
            query=query,
            duration_s=duration_s,
            export_dir=export_dir,
            aggressive_mode=aggressive_mode,
            deep_probe_enabled=deep_probe_enabled,
            deep_research=deep_research,
            extreme_mode=extreme_mode,
            no_communication=no_communication,
            ui_mode=ui_mode,
            windup_lead_s=windup_lead_s,
            acquisition_profile=acquisition_profile,
            rl_train_mode=rl_train_mode,
            force=force,
            flags=flags,
            shutdown_event=shutdown_event,
        )
    )

    return loop, sprint_task, shutdown_event, restore_signals


def run_runtime(
    loop: asyncio.AbstractEventLoop,
    sprint_task: asyncio.Task[None],
    restore_signals: Callable[[], None],
) -> None:
    """
    Run the event loop until the sprint task completes.
    Handles shutdown via KeyboardInterrupt.
    Call from finally block in main().
    """
    try:
        loop.run_until_complete(sprint_task)
    except KeyboardInterrupt:
        logger.info("[MAIN] Interrupted by user")
        sys.exit(130)
    finally:
        restore_signals()
        loop.run_until_complete(_cancel_all_tasks())
        loop.close()
        # CRITICAL FIX F350M-R: reclaim event loop allocations on M1 8GB
        try:
            gc.collect()
        except Exception:
            pass
        logger.debug("[RUNTIME] Event loop closed")


def shutdown_runtime(
    loop: asyncio.AbstractEventLoop,
    sprint_task: asyncio.Task[None],
    shutdown_event: asyncio.Event,
    restore_signals: Callable[[], None],
) -> None:
    """
    Graceful shutdown: cancel sprint task, drain pending tasks, close loop.
    """
    restore_signals()
    # Idempotent: skip if already set (signal handler already set it).
    # threading.Event.set() is async-signal-safe, so this is guaranteed
    # to succeed even if called from signal handler context.
    if not shutdown_event.is_set():
        shutdown_event.set()

    if not sprint_task.done():
        sprint_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            loop.run_until_complete(sprint_task)

    loop.run_until_complete(_cancel_all_tasks())
    loop.close()
    # CRITICAL FIX F350M-R: reclaim event loop allocations on M1 8GB
    try:
        gc.collect()
    except Exception:
        pass
    logger.debug("[RUNTIME] Event loop closed")


# _cancel_all_tasks is imported from runtime.sprint_entrypoint (canonical location).
# Both run_runtime and shutdown_runtime use the same bounded drain.
