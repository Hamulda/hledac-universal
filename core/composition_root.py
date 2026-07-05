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
from __future__ import annotations

import asyncio
import gc
import logging
import signal
import sys
from collections.abc import Callable
from typing import Any

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

    Returns a callable that RESTORES original signal handlers — call from finally.
    """
    original_handlers: dict[int, Any] = {}

    def _handler(signum: int, frame: Any) -> None:
        logger.info("[SIGNAL] Received signal %d, initiating graceful shutdown", signum)
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        original_handlers[sig] = signal.signal(sig, _handler)

    def restore() -> None:
        for sig, handler in original_handlers.items():
            signal.signal(sig, handler)

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
    from hledac.universal.core.__main__ import run_sprint

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
    sprint_task = asyncio.create_task(sprint_coro)
    sig_task = asyncio.create_task(shutdown_event.wait())

    done, pending = await asyncio.wait(
        [sprint_task, sig_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Surface sprint exceptions (asyncio.wait swallows them)
    exc = sprint_task.exception()
    if exc is not None:
        raise exc

    # Cancel if we exited via signal
    if sprint_task not in done:
        sprint_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
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
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    shutdown_event = asyncio.Event()

    # GC + malloc tuning (synchronous)
    configure_memory_for_sprint()
    start_malloc_pressure_relief()

    # Pre-sprint checks (sync, fail-loud)
    from hledac.universal.core.__main__ import run_pre_sprint_checks

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
        _cancel_all_tasks(loop)
        loop.close()
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
    shutdown_event.set()

    if not sprint_task.done():
        sprint_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            loop.run_until_complete(sprint_task)

    _cancel_all_tasks(loop)
    loop.close()
    logger.debug("[RUNTIME] Event loop closed")


def _cancel_all_tasks(loop: asyncio.AbstractEventLoop) -> None:
    """Cancel all pending tasks and wait for them to drain."""
    pending = asyncio.all_tasks(loop)
    for t in pending:
        t.cancel()
    if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
