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
import msgspec

import asyncio
import contextlib
import logging
import os
import pathlib
import signal
import sys
import time
import traceback
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv

from hledac.universal.utils.async_helpers import safe_create_task, safe_wait_for, stop_task
from hledac.universal.runtime.logging_setup import configure_logging, get_logger

# Sprint F285: Ensure local modules (utils/, runtime/, etc.) are resolvable when
# hledac is invoked via `uv run hledac` or the generated .venv/bin/hledac entry point.
# The entry point script does not inherit the CWD of the project root, so
# __file__-based resolution is the stable path regardless of invocation method.
_src_root = pathlib.Path(__file__).parent.resolve()
if str(_src_root) not in sys.path:
    sys.path.insert(0, str(_src_root))
del _src_root

# TYPE_CHECKING block: imports only for static analysis (ruff, mypy)
# At runtime these are strings due to `from __future__ import annotations`
if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from runtime.sprint_lifecycle import SprintLifecycleManager

# Sprint 8VC: Exclude legacy/ from Python path to prevent accidental imports
# legacy/ is for reference only — active code must not import from it
sys.path = [p for p in sys.path if not p.endswith("/legacy")]

# Sprint 0B: uvloop MUST be installed before any async operations
# Sprint F266-UVLOOP: canonical uvloop state in runtime/state module
# Sprint Phase4: ulimit -n 4096 for DuckDB FD budget (M1 Air 8GB)
try:
    import resource as _resource

    soft, hard = _resource.getrlimit(_resource.RLIMIT_NOFILE)
    if soft < 4096:
        try:
            _resource.setrlimit(_resource.RLIMIT_NOFILE, (4096, hard))
            logging.debug(f"[BOOT] ulimit -n: {soft}→4096 (hard={hard})")
        except (ValueError, OSError) as _e:
            logging.debug(f"[BOOT] ulimit -n 4096 failed: {_e}")
except ImportError:
    pass  # Not available on Windows

from hledac.universal.runtime.state import mark_uvloop_installed as _mark_uvloop_installed  # noqa: E402

_uvloop_installed = False
try:
    import sys as _sys

    import uvloop

    # Python 3.15+: uvloop.install() triggers AbstractEventLoopPolicy deprecation
    # inside the library itself — skip it and use stdlib asyncio loop (F3.4: 3.14 works)
    if _sys.version_info >= (3, 15):
        logging.warning("[RUNTIME] Python 3.15+ detected, skipping uvloop.install()")
    else:
        import warnings as _lw

        with _lw.catch_warnings():
            _lw.filterwarnings("ignore", message=".*AbstractEventLoopPolicy.*", category=DeprecationWarning)
            uvloop.install()
        _mark_uvloop_installed()  # propagate to runtime/state (session_runtime reads from there)
        _uvloop_installed = True
        logging.info("[RUNTIME] uvloop installed successfully")
except ImportError:
    # uvloop not installed — use stdlib asyncio with kqueue on M1.
    # F3.4: uvloop 0.22+ has Python 3.14+ M1 wheel since June 2026.
    # Install via: uv sync or uv add uvloop
    _uvloop_installed = False


# Sprint F214HELP: Fast --help / -h path — no MLX, no runtime init
# Must be defined BEFORE any heavy module imports (mlx_cache, brain, etc.)
# =============================================================================


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser. Lightweight — imports only argparse/stdlib."""
    import argparse  # local import keeps help path off module-level MLX chain

    parser = argparse.ArgumentParser(
        description="Hledac Universal OSINT Runner",
        add_help=False,  # manually handle -h/--help below
    )
    parser.add_argument("--sprint", metavar="QUERY", help="Run sprint with given query")
    parser.add_argument(
        "--duration",
        type=float,
        default=1800.0,
        metavar="SECS",
        help="Sprint duration in seconds (default: 1800 = 30min)",
    )
    parser.add_argument(
        "--windup-lead",
        type=float,
        default=None,
        help="F285: Override windup lead time in seconds. Default: 30%% of duration (capped at 180s). "
        "Use 30 for M1 Air 8GB sprints to maximize active acquisition window.",
    )
    parser.add_argument(
        "--export-dir",
        default=str(pathlib.Path.home() / ".hledac" / "reports"),
        help="Directory for sprint reports (default: ~/.hledac/reports)",
    )
    parser.add_argument(
        "--vault",
        action="store_true",
        help="F26X+: Enable encrypted vault export (AES-256-ZIP via VaultManager)",
    )
    parser.add_argument(
        "--aggressive",
        action="store_true",
        default=True,
        help="Sprint F195B: Enable aggressive mode with 8s branch budgets (default: ON)",
    )
    parser.add_argument(
        "--no-aggressive",
        dest="aggressive",
        action="store_false",
        help="Disable aggressive mode: stable sequential branches, 30 percent windup",
    )
    parser.add_argument(
        "--deep-probe",
        action="store_true",
        help="Run deep probe research post-sprint",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Enable terminal dashboard during sprint",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="F221-ABORT: Override the pre-flight guard that aborts sprints whose "
        "active-window budget would be below MIN_ACTIVE_WINDOW_S=30s. "
        "Emits a [F221-FORCED] warning instead of exiting with code 2. "
        "Use only for explicit dry-runs / smoke tests where zero evidence is acceptable.",
    )
    parser.add_argument(
        "--acquisition-profile",
        type=str,
        default="default",
        choices=["default", "nonfeed_diagnostic", "deep_osint_m1"],
        help="F216B/F251D: Acquisition runtime profile (default | nonfeed_diagnostic | deep_osint_m1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry-run mode: validate config, check Hermes/UMA/sources, show timing plan. No real discovery.",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Issue #19: Enable M1-safe OTEL profiling via HLEDAC_OTEL_PROFILE=1. "
        "Activates httpx auto-instrumentation (~1MB overhead). "
        "Exports spans to OTLP endpoint (HLEDAC_OTEL_ENDPOINT, default http://localhost:4318). "
        "Use --export-dir to override output directory. "
        "For memory profiling: HLEDAC_OTEL_EXPORTER=duckdb + memray.",
    )
    # Phase 3: flag preset selectors. ``--list-presets`` is handled in
    # main() and exits 0 before any sprint/boot logic runs.
    parser.add_argument(
        "--preset",
        type=str,
        default=None,
        choices=["minimal", "osint", "recon", "research", "full"],
        help=("Phase 3: Apply a flag preset before validation. Existing HLEDAC_ENABLE_* env vars are NOT overwritten."),
    )
    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="Phase 3: Print preset table (name, flag count, RAM est.) and exit 0.",
    )
    # Python 3.14 argparse settings
    try:
        parser.suggest_on_error = True
        parser.color = True
    except AttributeError:
        pass  # older Python — settings are best-effort
    return parser


# =============================================================================

import msgspec  # noqa: E402

logger = get_logger(__name__)

# =============================================================================
# Sprint 8AI: Boot telemetry buffer — bounded deque, LRU evict + JSONL flush
# Issue #19: Boot telemetry buffer roste bez limitu pri dlouhem bootu
# =============================================================================

_BOOT_TELEMETRY_MAX: int = 200
_boot_telemetry: deque[dict[str, Any]] = deque(maxlen=_BOOT_TELEMETRY_MAX)


def _boot_record(step: str, status: str, **kw: Any) -> None:
    """Append a boot telemetry entry. O(1), bounded, fail-safe."""
    record = {"step": step, "status": status, "ms": time.time(), **kw}
    _boot_telemetry.append(record)

    # Flush oldest 50% to disk when buffer fills
    if len(_boot_telemetry) >= _BOOT_TELEMETRY_MAX:
        _drain_boot_telemetry()


def _drain_boot_telemetry() -> None:
    """Drain oldest 50%% to ~/.hledac/logs/boot.jsonl. Fail-soft."""
    try:
        from core.rust_backend import rust as _rust

        _compact = _rust.json.compact
    except Exception:
        import orjson

        def _compact(data: dict) -> str:
            return orjson.dumps(data).decode()

    try:
        log_path = pathlib.Path.home() / ".hledac" / "logs" / "boot.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            drain_count = _BOOT_TELEMETRY_MAX // 2
            for _ in range(drain_count):
                if _boot_telemetry:
                    f.write(_compact(_boot_telemetry.popleft()) + "\n")
    except Exception:
        pass  # fail-soft: telemetry is best-effort


def get_boot_telemetry() -> list[dict[str, Any]]:
    """Return copy of in-memory boot telemetry. O(1) snapshot."""
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
    except (ImportError, AttributeError):
        results["metal"] = False
    try:
        import psutil

        vm = psutil.virtual_memory()
        results["free_ram_mb"] = round(vm.available / 1024 / 1024, 1)
        results["memory_pct"] = vm.percent
    except (ImportError, AttributeError, OSError):
        results["free_ram_mb"] = -1
    # Sprint F500J §2: REMOVED duckdb.connect() eager check.
    # DuckDB availability is verified through store.async_initialize() in the
    # runtime flow. duckdb_store.py lazy-imports duckdb via _get_duckdb().
    # Calling duckdb.connect() here was a heavyweight eager import (~30-50ms)
    # that provided no truth value since sprint always runs regardless.
    logger.info("preflight_check", results=results)
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


def get_runtime_status() -> dict[str, Any]:
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


def _install_signal_teardown(loop: asyncio.AbstractEventLoop) -> None:
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
        logger.info("signal_received", sig_name=sig_name)
        _signal_teardown_flag = True
        loop.call_soon_threadsafe(loop.stop)

    try:
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
        logger.info("signal_handlers_installed")
        global _signal_handlers_installed
        _signal_handlers_installed = True
    except (ValueError, OSError) as e:
        logger.warning("signal_handlers_install_failed", error=str(e))


# =============================================================================
# Sprint 8AI: Boot guard — synchronous, called BEFORE asyncio.run()
# =============================================================================


def _run_boot_guard(lmdb_root: pathlib.Path | None = None) -> tuple[int, str]:
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
            from hledac.universal.paths import LMDB_ROOT as _derived_root  # noqa: N811

            lmdb_root = _derived_root
        except Exception:
            return 0, "lmdb_root_not_configured"

    try:
        from hledac.universal.knowledge.lmdb_boot_guard import (
            BootGuardError as _BootGuardError,
        )
        from hledac.universal.knowledge.lmdb_boot_guard import (
            cleanup_stale_lmdb_lock,
        )
    except (ImportError, AttributeError) as e:
        return 0, f"boot_guard_import_failed({e})"

    try:
        removed, reason = cleanup_stale_lmdb_lock(lmdb_root)
        _boot_record("boot_guard", "ok", removed=removed, reason=reason)
        return removed, reason
    except _BootGuardError:
        # Re-raise BootGuardError without wrapping — caller decides to abort
        raise
    except OSError as e:
        _boot_record("boot_guard", "error", error=str(e))
        return 0, f"boot_guard_error({e})"


class BootGuardError(Exception):
    """Raised when boot guard detects unsafe stale-lock state."""

    pass


# =============================================================================
# Sprint 8AI: AsyncExitStack-backed teardown
# =============================================================================


async def _cancel_orphan_tasks() -> None:
    """F4.4: Delegate to trio-style cancel_scope_drain from async_helpers."""
    from hledac.universal.utils.async_helpers import cancel_scope_drain

    count = await cancel_scope_drain(timeout=5.0, label="orphan_drain")
    if count > 0:
        _boot_record("task_cancellation", f"cancelling_{count}_tasks")
        _boot_record("task_cancellation", f"completed_{count}_tasks")


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

    exit_stack: contextlib.AsyncExitStack | None = None
    store_instance = None

    try:
        exit_stack = contextlib.AsyncExitStack()
        await exit_stack.__aenter__()

        _boot_record("async_exit_stack_entered", "ok")

        # Sprint 8AM C.2: Session ownership
        if owned_session:
            try:
                # Obtain shared session — this is a Lazy singleton
                # We "own" it by registering its async close
                from hledac.universal.network.session_runtime import (
                    async_get_httpx_session,
                    close_aiohttp_session_async,
                )

                @contextlib.asynccontextmanager
                async def _managed_session():
                    """Async context manager for session lifecycle (setup → yield → teardown)."""
                    # Setup: create session
                    try:
                        await async_get_httpx_session()
                    except Exception as e:
                        logger.warning("session_acquisition_failed", context="managed_session", error=str(e))
                        raise  # Re-raise so the sprint doesn't silently continue without a session
                    try:
                        yield
                    finally:
                        # Teardown: close session (LIFO order via AsyncExitStack)
                        try:
                            await close_aiohttp_session_async()
                        except Exception as e:
                            logger.warning("session_close_failed", error=str(e))

                # push_async_exit accepts async context manager coroutine directly (setup/teardown pair)
                exit_stack.push_async_exit(_managed_session())
                _owned_resources["session_owned"] = True
                _boot_record("session_owned", "registered")
            except Exception as e:
                logger.warning("acquire_session_failed", error=str(e))
                _boot_record("session_owned", "failed", error=str(e))

        # Sprint 8AM C.3: Store ownership
        if owned_store and exit_stack is not None:
            try:
                from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

                # Create owned store (uses paths.py RAMDisk SSOT)
                store_instance = DuckDBShadowStore(lazy=False)
                # Async init
                await store_instance.async_initialize()

                @contextlib.asynccontextmanager
                async def _managed_store():
                    """Async context manager for store lifecycle (setup → yield → teardown)."""
                    try:
                        yield
                    finally:
                        # Teardown: close store (LIFO order via AsyncExitStack)
                        if store_instance is not None:
                            await store_instance.aclose()

                # push_async_exit accepts async context manager coroutine directly (setup/teardown pair)
                exit_stack.push_async_exit(_managed_store())
                _owned_resources["store_owned"] = True
                _boot_record("store_owned", "registered")
            except Exception as e:
                logger.warning("acquire_store_failed", error=str(e))
                _boot_record("store_owned", "failed", error=str(e))
                store_instance = None

        logger.info("hledac_initialized")
        logger.info("uvloop_active", installed=_uvloop_installed)

        # Sprint 8AM C.9: Delegation to existing pipelines
        # Import here to avoid module-level side effects
        # Sprint F274: Bootstrap patterns are configured LAZY on first use via
        # find_matches() — no eager call needed. Saves ~50MB + ~200ms startup
        # when pattern matching is never used during a sprint run.
        from hledac.universal.pipeline.live_feed_pipeline import async_run_default_feed_batch
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline

        # P1-C: Initialize Hermes3 engine for PUBLIC lane report generation
        # (boot mode skips full SprintScheduler, so we create engine here)
        # Note: DO NOT close the runner - that would unload the model via _lifecycle.unload()
        # The engine instance is safe to use after runner.close() because DeepHermes3Engine
        # is standalone; we just need to keep the lifecycle alive for model weights.
        hermes_boot_engine = None
        if store_instance is not None:
            try:
                from hledac.universal.brain.model_lifecycle import ModelLifecycle
                from hledac.universal.brain.synthesis_runner import SynthesisRunner

                boot_runner = SynthesisRunner(ModelLifecycle())
                hermes_boot_engine = boot_runner._get_hermes_engine()
                # Engine is now referenced by boot_runner._hermes_engine and our local var
                # We intentionally DO NOT call boot_runner.close() here because that would
                # unload the model via _lifecycle.unload() before pipeline can use it.
                # The pipeline will use the engine directly.
            except Exception as e:
                logger.debug("hermes3_boot_init_skipped", error=str(e))

        # Use the SAME store instance for both pipelines
        web_result = await async_run_live_public_pipeline(
            query="public passive OSINT",
            store=store_instance,
            max_results=5,
            hermes_engine=hermes_boot_engine,
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
        logger.error("fatal_error", error=str(e), exc_info=True)
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
            except (RuntimeError, OSError) as e:
                logger.warning("async_exit_stack_unwind_error", error=str(e))
                _boot_record("async_exit_stack_unwind", "error", error=str(e))

        # P1-C: Unload Hermes3 engine if it was loaded in boot mode
        # (engine is never unloaded by SynthesisRunner.close() path since we
        # intentionally skipped close() to keep model weights alive for pipeline)
        try:
            if hermes_boot_engine is not None and hasattr(hermes_boot_engine, "unload"):
                await safe_wait_for(hermes_boot_engine.unload(), timeout=10.0, label="hermes_boot")
                logger.debug("hermes3_boot_unloaded")
        except (RuntimeError, AttributeError, asyncio.TimeoutError) as e:
            logger.debug("hermes3_boot_unload_skipped", error=str(e))


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
        "_snapshot_cache",
    )

    def __init__(self, interval_s: float = 0.5) -> None:
        self._interval = interval_s
        self._running = False
        self._task: asyncio.Task[Any] | None = None
        self._lock = asyncio.Lock()
        self._peak_used_gib = 0.0
        self._peak_state = "unknown"
        self._sample_count = 0
        self._start_state = "unknown"
        self._end_state = "unknown"
        self._start_swap = 0.0
        self._peak_swap_used_gib = 0.0
        self._snapshot_cache: dict | None = None

    async def start(self) -> None:
        """Start sampler task. Idempotent."""
        if self._running:
            return
        self._running = True
        self._task = safe_create_task(self._sample_loop(), name="main:sampler_loop")

    async def stop(self) -> None:
        """Stop sampler task gracefully."""
        self._running = False
        await stop_task(self._task)
        self._task = None

    def get_snapshot(self) -> dict:
        """
        Return current snapshot. Direct sync read — no lock acquisition.

        Lock only protects the writer (_sample_loop). For a sync reader
        called from arbitrary thread/task, consistent-enough read (worst case:
        peak from mid-update, ~μs of staleness) is acceptable for diagnostics.

        Issue #25 fix: added _start_swap tracking; get_snapshot now returns it.
        """
        return {
            "peak_used_gib": self._peak_used_gib,
            "peak_state": self._peak_state,
            "sample_count": self._sample_count,
            "start_state": self._start_state,
            "end_state": self._end_state,
            "start_swap_gib": self._start_swap,
            "peak_swap_used_gib": self._peak_swap_used_gib,
        }

    async def _sample_loop(self) -> None:
        """Background sampling loop. Self-terminates when _running=False."""
        from hledac.universal.core.resource_governor import sample_uma_status_async

        try:
            while self._running:
                try:
                    status = await sample_uma_status_async()
                    async with self._lock:
                        self._sample_count += 1
                        if self._sample_count == 1:
                            self._start_state = status.state
                        self._end_state = status.state
                        if status.system_used_gib > self._peak_used_gib:
                            self._peak_used_gib = status.system_used_gib
                            self._peak_state = status.state
                        if hasattr(status, "swap_used_gib"):
                            if self._sample_count == 1:
                                self._start_swap = status.swap_used_gib
                            if status.swap_used_gib > self._peak_swap_used_gib:
                                self._peak_swap_used_gib = status.swap_used_gib
                except Exception:  # noqa: BLE001
                    pass  # noqa: BLE001  # fail-open: keep sampling even if one tick fails
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            raise  # C.8: propagate CancelledError, don't swallow


# =============================================================================
# Sprint 8AO: Observed Live Run — Report Structure & Helpers
# =============================================================================

# Module-level singleton for last run report (C.4)
# Issue #11: Changed from dict | None to ObservedRunReport | None
# - Eliminates F290-1 JSON encode/decode roundtrip overhead (~30-50 μs per store)
# - ObservedRunReport is already msgspec.Struct(frozen=True)
# - Direct assignment: no memory waste, no serialization cost
_last_observed_run_report: ObservedRunReport | None = None
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

    DIAGNOSTIC / OBSERVED-RUN ONLY — non-canonical.

    This taxonomy lives in root __main__.py as an *observed-run signal* for
    CLI/banner reporting. It is NOT the canonical runtime-truth owner.

    F180A: Split-brain cleanup — this function was previously described in ways
    that implied it was a canonical owner surface. It is NOT. It is a read-only
    diagnostic label generator for observed runs and benchmark probes only.

    Canonical meaningful/smoke truth is defined in:
        hledac.universal.runtime.sprint_entrypoint._is_meaningful_run()
        hledac.universal.runtime.sprint_entrypoint._runtime_truth()
    Those functions return is_meaningful (bool) and runtime_truth_level
    (smoke | meaningful | meaningful_empty | mixed) derived from cycle-level
    scheduler data — richer and more authoritative than this module-level
    duration heuristic.

    Invariant: classify_runtime_truth() output must NEVER be used as
    canonical_sprint_owner evidence. It is CLI-only diagnostic.

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

    _interpreter_executable = sys.executable
    _interpreter_version = sys.version_info[:2] == (3, 14) and "3.14" or sys.version

    try:
        import ahocorasick as _  # noqa: F401  # ahocorasick

        _ahocorasick_available = True
    except ImportError:
        _ahocorasick_available = False

    # Bootstrap pack truth
    try:
        from hledac.universal.utils.patterns.pattern_matcher import get_default_bootstrap_patterns

        _default_bootstrap_count = len(get_default_bootstrap_patterns())
        _bootstrap_pack_version = 2  # Sprint 8AZ bootstrap pack v2
    except (ImportError, AttributeError):
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


class ObservedRunReport(msgspec.Struct, frozen=True):
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
    batch_error: str | None
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
    batch_error: str | None,
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
    for src in batch_result.sources if batch_result else []:
        per_source_raw.append(
            {
                "feed_url": src.feed_url,
                "label": src.label,
                "origin": src.origin,
                "priority": src.priority,
                "fetched_entries": src.fetched_entries,
                "accepted_findings": src.accepted_findings,
                "stored_findings": src.stored_findings,
                "elapsed_ms": src.elapsed_ms,
                "error": getattr(src, "error", None),
            }
        )

    # Dedup delta
    dedup_delta = {}
    if dedup_surface_available(dedup_before, dedup_after):
        for key in ("persistent_duplicate_count", "quality_duplicate_count", "in_memory_duplicate_count"):
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
        "sources": [{"feed_url": s["feed_url"], "error": s["error"]} for s in error_sources],
    }

    # Sprint 8AS C.2: success_rate + failed_source_count
    total_sources_val = batch_result.total_sources if batch_result else 0
    completed_sources_val = batch_result.completed_sources if batch_result else 0
    failed_source_count_val = total_sources_val - completed_sources_val
    success_rate_val = completed_sources_val / total_sources_val if total_sources_val > 0 else 0.0

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
      - blocker: str | None description if findings are 0
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
        dup_frac = (
            in_memory_duplicate_rejected_count_delta + persistent_duplicate_rejected_count_delta
        ) / total_rejected  # noqa: E501
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
        elif (
            "network" in error.lower()
            or "connection" in error.lower()
            or "dns" in error.lower()
            or "resolve" in error.lower()
            or "http" in error.lower()
            or "ssl" in error.lower()
            or "certificate" in error.lower()
        ):  # noqa: E501
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
        from hledac.universal.utils.patterns.pattern_matcher import get_pattern_matcher

        pm = get_pattern_matcher()
        if hasattr(pm, "pattern_count"):
            return pm.pattern_count()
    except (ImportError, AttributeError):
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
        from hledac.universal.utils.patterns.pattern_matcher import get_pattern_matcher

        pm = get_pattern_matcher()
        if hasattr(pm, "pattern_count"):
            count = pm.pattern_count()
            status = pm.get_status()
            return count, status.get("bootstrap_default_configured", False)
    except (ImportError, AttributeError):
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
        from hledac.universal.utils.patterns.pattern_matcher import (
            configure_default_bootstrap_patterns_if_empty,
            get_pattern_matcher,
        )

        pm = get_pattern_matcher()
        current_count = pm.pattern_count()
        if current_count > 0:
            status = pm.get_status()
            return current_count, status.get("bootstrap_default_configured", False)
        # Registry empty — apply bootstrap
        applied = configure_default_bootstrap_patterns_if_empty()
        return pm.pattern_count(), applied
    except (ImportError, AttributeError):
        return 0, False


# =============================================================================
# Sprint 8AO: Observed Live Run — Main Entry Point
# C.0, C.1, C.2, C.3, C.4
# =============================================================================


def get_last_observed_run_report() -> ObservedRunReport | None:
    """
    Sprint 8AO C.4: Return last observed run report.

    Issue #11: Returns ObservedRunReport directly (was dict | None).
    - Direct access to structured fields (no .get() dict access)
    - msgspec.Struct supports field access: report.accepted_findings
    - callers using .get() dict pattern need updating to field access
    """
    return _last_observed_run_report


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
    lines.append("\n[runtime truth]")
    lines.append(f"  interpreter_executable:       {report.get('interpreter_executable', 'N/A')}")
    lines.append(f"  interpreter_version:          {report.get('interpreter_version', 'N/A')}")
    lines.append(f"  ahocorasick_available:        {report.get('ahocorasick_available', 'N/A')}")
    lines.append(f"  actual_live_run_executed:     {report.get('actual_live_run_executed', False)}")
    lines.append(f"  bootstrap_pack_version:       {report.get('bootstrap_pack_version', 0)}")
    lines.append(f"  default_bootstrap_count:      {report.get('default_bootstrap_count', 0)}")
    lines.append(f"  store_counters_reset_before_run: {report.get('store_counters_reset_before_run', False)}")
    lines.append(f"  matcher_probe_sample_used:   {report.get('matcher_probe_sample_used', 'N/A')}")
    rss_hits = report.get("matcher_probe_rss_hits", ())
    lines.append(f"  matcher_probe_rss_hits:       {len(rss_hits)} hits")

    # Sprint 8BC C.4: [matcher truth] section
    sample_texts = report.get("sample_scanned_texts", ())
    sample_counts = report.get("sample_hit_counts", ())
    sample_labels = report.get("sample_hit_labels_union", ())
    lines.append("\n[matcher truth]")
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
    lines.append("\n[live run truth]")
    lines.append(f"  used_rich_feed_content:    {report.get('used_rich_feed_content', False)}")
    lines.append(f"  used_article_fallback:    {report.get('used_article_fallback', False)}")
    lines.append(f"  matched_feed_names:       {report.get('matched_feed_names', ())}")
    lines.append(f"  accepted_feed_names:       {report.get('accepted_feed_names', ())}")
    lines.append(f"  live_run_attempt_count:    {report.get('live_run_attempt_count', 0)}")
    lines.append(f"  live_run_attempt_1_result: {report.get('live_run_attempt_1_result', '')}")
    lines.append(f"  live_run_attempt_2_result: {report.get('live_run_attempt_2_result', '')}")
    rec = report.get("recommended_next_sprint", "")
    lines.append(f"  recommended_next_sprint:   {rec if rec else '(computed post-run)'}")

    # Batch totals (C.1)
    elapsed_s = report.get("elapsed_ms", 0) / 1000.0
    lines.append("\n[Batch Totals]")
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
            "  Content quality:   INFRA-ONLY RUN (PatternMatcher empty — "
            "validated infrastructure/runtime path, not content quality)"
        )

    # Peak UMA (C.3)
    uma = report.get("uma_snapshot", {})
    lines.append("\n[Peak UMA]")
    lines.append(
        f"  Peak used GiB:    {uma.get('peak_used_gib', 'N/A'):.2f}"
        if isinstance(uma.get("peak_used_gib"), float)
        else f"  Peak used GiB:    {uma.get('peak_used_gib', 'N/A')}"
    )  # noqa: E501
    lines.append(f"  Peak state:        {uma.get('peak_state', 'N/A')}")
    lines.append(f"  Start state:       {uma.get('start_state', 'N/A')}")
    lines.append(f"  End state:          {uma.get('end_state', 'N/A')}")
    lines.append(f"  Sample count:      {uma.get('sample_count', 0)}")
    swap_peak = uma.get("peak_swap_used_gib", 0.0)
    if isinstance(swap_peak, float) and swap_peak > 0:
        lines.append(f"  Peak swap GiB:     {swap_peak:.2f}")

    # Dedup raw deltas (C.2, C.12)
    dedup_surf = report.get("dedup_surface_available", False)
    lines.append("\n[Dedup Raw Deltas]")
    if dedup_surf:
        delta = report.get("dedup_delta", {})
        lines.append("  persistent_dedup_enabled: True")
        lines.append(f"  persistent_duplicate_count delta: {delta.get('persistent_duplicate_count', 'N/A')}")
        lines.append(f"  quality_duplicate_count delta:    {delta.get('quality_duplicate_count', 'N/A')}")
        lines.append(f"  in_memory_duplicate_count delta: {delta.get('in_memory_duplicate_count', 'N/A')}")
    else:
        lines.append("  dedup_surface_available: False (N/A)")

    # Slow-source ranking (C.10)
    slow = report.get("slow_sources", [])
    if slow:
        lines.append("\n[Top Slow Sources (by elapsed_ms desc)]")
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
        lines.append("\n[Error Summary] 0 errors")

    # Sprint 8AS C.2: Success rate + failed source count
    success_rate = report.get("success_rate", 0.0)
    failed_count = report.get("failed_source_count", 0)
    lines.append("\n[Sprint 8AS C.2] Success Rate")
    lines.append(f"  Success rate: {success_rate:.1%}")
    lines.append(f"  Failed sources: {failed_count}")

    # Sprint 8AS C.0: Baseline delta
    baseline = report.get("baseline_delta", {})
    if baseline:
        lines.append("\n[Sprint 8AS C.0] Delta vs 8AO Baseline")
        lines.append(f"  Status: {baseline.get('status', 'N/A')}")
        lines.append(
            f"  Completed sources: {baseline.get('completed_sources', 'N/A')} ({baseline.get('completed_sources_delta', 0):+d})"
        )  # noqa: E501
        lines.append(f"  Fetched entries: {baseline.get('fetched_entries_delta', 0):+d}")
        lines.append(f"  Accepted findings: {baseline.get('accepted_findings_delta', 0):+d}")
        lines.append(f"  Stored findings: {baseline.get('stored_findings_delta', 0):+d}")
        lines.append(
            f"  Failed sources: {baseline.get('failed_source_count', 'N/A')} ({baseline.get('failed_source_count_delta', 0):+d})"
        )  # noqa: E501
        blocker = baseline.get("blocker")
        if blocker:
            lines.append(f"  Blocker: {blocker}")

    # Sprint 8AS C.1: Health breakdown
    health = report.get("health_breakdown", {})
    if health:
        breakdown = health.get("health_breakdown", {})
        lines.append("\n[Sprint 8AS C.1] Feed Health Breakdown")
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
    lines.append("\n[Sprint 8AS C.4] Run Quality")
    if content_validated:
        lines.append(f"  Content validation: ACTIVE (patterns={patterns})")
        lines.append("  Run type: CONTENT-VALIDATED (not infra-only)")
    else:
        lines.append("  Content validation: INFRA-ONLY (patterns=0)")
        lines.append("  Run type: INFRA-ONLY")

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
        lines.append("\n[signal funnel]")
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
        lines.append("\n[store rejection trace]")
        lines.append(f"  accepted_count_delta:           {accepted_delta}")
        lines.append(f"  low_information_rejected:        {low_info_delta}")
        lines.append(f"  in_memory_duplicate_rejected:    {in_mem_dup}")
        lines.append(f"  persistent_duplicate_rejected:   {persist_dup}")
        lines.append(f"  other_rejected:                 {other_delta}")
        lines.append(f"  total_rejected:                 {total_rejected}")
        if total_rejected > 0:
            lines.append("  entropy_threshold:              0.5")
            lines.append("  entropy_min_len:                8")
            low_frac = low_info_delta / total_rejected * 100
            dup_frac = (in_mem_dup + persist_dup) / total_rejected * 100
            lines.append(f"  low_info fraction:              {low_frac:.1f}%")
            lines.append(f"  duplicate fraction:            {dup_frac:.1f}%")

    # Sprint 8BA C.3: [root cause] + [recommendation] (C.2 mapping)
    diag = report.get("diagnostic_root_cause", "unknown")
    is_net_var = report.get("is_network_variance", False)
    lines.append("\n[root cause]")
    lines.append(f"  diagnostic_root_cause:           {diag}")
    lines.append(f"  is_network_variance:             {is_net_var}")

    # C.2: Recommendation mapping (derived in formatter, not persisted)
    lines.append("\n[recommendation]")
    if diag == "accepted_present":
        lines.append("  → scheduler_entry_hash_v1")
    elif diag == "duplicate_rejection_dominant":
        lines.append("  → scheduler_entry_hash_v1")
    elif diag == "no_pattern_hits_possible_morphology_gap":
        lines.append("  → pattern_pack_v3_or_source_specific_text_extraction")
    elif diag == "no_pattern_hits":
        lines.append("  → pattern_pack_v3_or_source_specific_text_extraction")
    elif diag == "pattern_hits_but_no_findings_built":
        lines.append("  → finding_build_trace")
    elif diag == "low_information_rejection_dominant":
        lines.append("  → quality_gate_recalibration_only_if_reproduced")
    elif diag in ("network_variance", "no_new_entries"):
        lines.append("  → repeat_live_run")
    else:
        lines.append("  → repeat_live_run")

    lines.append("=" * 60)
    return "\n".join(lines)


# =============================================================================
# Sprint 0B: Benchmark probe (unchanged)
# =============================================================================


async def _run_benchmark_probe() -> dict[str, Any]:
    """
    Run Sprint 0B benchmark probe tests.

    Returns:
        Dict with benchmark results including pass/fail counts.
    """
    from hledac.universal.utils.flow_trace import get_summary, is_enabled

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
    except Exception as e:  # noqa: BLE001 - probe check, fail-soft
        results["checks"]["flow_trace_summary_safe"] = False
        results["checks"]["flow_trace_error"] = str(e)

    # Summary
    all_passed = all(v is True or isinstance(v, dict) for v in results["checks"].values())
    results["all_passed"] = all_passed
    results["passed_count"] = sum(1 for v in results["checks"].values() if v is True)

    return results


# =============================================================================
# Sprint 8PC: sprint_mode entrypoint
# =============================================================================

# Sprint 8PC: module-level flag for EMERGENCY state (stops new frontier work)
_sprint_frontier_stopped: bool = False

# Sprint 8TA B.2: Phase timing
_phase_times: dict[str, float] = {}

# Sprint F204E: Analyst brief for markdown export (set after scheduler.run completes)
_analyst_brief_for_markdown: dict[str, Any] | None = None


def _mark_phase(name: str) -> None:
    """Mark phase start time. Called at the beginning of each phase."""
    _phase_times[name] = time.monotonic()
    logger.info("phase_change", phase=name)


def _compute_sprint_report_path(sprint_id: str) -> Path:
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
    logger.info("markdown_report_exported", path=str(path))
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
        except (AttributeError, RuntimeError):
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
        # Sprint F265C: Arrow ingest telemetry — surfaces _ARROW_METRICS in scorecard
        # so 678 fallback events are visible in sprint output instead of silent 0-findings
        "arrow_metrics": {},
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
    except (ImportError, AttributeError):
        pass

    # Sprint F204E: Attach analyst brief to scorecard for markdown export
    if _analyst_brief_for_markdown:
        scorecard_data["analyst_brief"] = _analyst_brief_for_markdown

    # Sprint F232C: Build and attach investigation_packet for markdown analyst brief
    try:
        from hledac.universal.export.sprint_exporter import _build_investigation_packet

        if sprint_report and isinstance(sprint_report, dict):
            scorecard_data["investigation_packet"] = _build_investigation_packet(sprint_report)
        elif sprint_report is not None and hasattr(sprint_report, "__dict__"):
            scorecard_data["investigation_packet"] = _build_investigation_packet(sprint_report.__dict__)
    except (ImportError, AttributeError):
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
    # Sprint F265C: Show Arrow ingest metrics in sprint output
    arrow_m = scorecard_data.get("arrow_metrics", {})
    if (
        arrow_m
        and isinstance(arrow_m, dict)
        and any((v or 0) > 0 for v in arrow_m.values() if isinstance(v, (int, float)))
    ):
        arrow_sel = arrow_m.get("arrow_selected", 0)
        arrow_ok = arrow_m.get("arrow_success_count", 0)
        arrow_fb = {k: v for k, v in arrow_m.items() if "fallback" in k or "error" in k}
        print(f"  Arrow ingest:     selected={arrow_sel} ok={arrow_ok}")
        if arrow_fb:
            print(f"  Arrow fallback:   {arrow_fb}")
    print("=" * 60 + "\n")

    # Sprint F265C: Populate arrow_metrics from DuckDB store — surfaces Arrow ingest
    # telemetry (fallback counts, success counts, error breakdown) in sprint scorecard.
    # Without this, 678 Arrow fallbacks were invisible (silent 0-findings in output).
    if store is not None and hasattr(store, "_arrow_metrics"):
        try:
            scorecard_data["arrow_metrics"] = store._arrow_metrics
        except (AttributeError, TypeError):
            pass
    elif store is not None and hasattr(store, "get_arrow_metrics"):
        try:
            from hledac.universal.knowledge.duckdb_store import get_arrow_metrics

            scorecard_data["arrow_metrics"] = get_arrow_metrics()
        except (ImportError, AttributeError):
            pass

    # Persist to DuckDB
    if store is not None and hasattr(store, "upsert_scorecard"):
        try:
            await store.upsert_scorecard(scorecard_data)
        except (RuntimeError, OSError) as e:
            logger.warning("scorecard_persist_failed", error=str(e))

    # Sprint 8UC B.2.4: Persist research episode
    if store is not None and hasattr(store, "upsert_episode"):
        try:
            import time as _t

            top_findings_list = []
            if sprint_report is not None and hasattr(sprint_report, "findings"):
                top_findings_list = [
                    f.content if hasattr(f, "content") else str(f) for f in (sprint_report.findings or [])[:5]
                ]
            await store.upsert_episode(
                {
                    "sprint_id": sprint_id,
                    "query": target,
                    "summary": sprint_report.threat_summary
                    if sprint_report and hasattr(sprint_report, "threat_summary")
                    else "",  # noqa: E501
                    "top_findings": top_findings_list,
                    "ioc_clusters": [],
                    "source_yield": scorecard_data.get("source_yield_json", "{}"),
                    "synthesis_engine": scorecard_data.get("synthesis_engine", "unknown"),
                    "duration_s": elapsed,
                    "ts": _t.time(),
                }
            )
            logger.info("research_episode_saved", sprint_id=sprint_id)
        except (RuntimeError, OSError) as e:
            logger.warning("scorecard_persist_episode_failed", error=str(e))

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
                logger.info("ghost_global_entities_upserted", count=n_upserted)
        except (AttributeError, RuntimeError, OSError):
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
        from hledac.universal.project_types import ExportHandoff

        # Sprint 8VZ §B: Construct typed handoff directly — canonical producer truth
        # top_nodes from store seam (DuckPGQGraph-backed store.get_top_seed_nodes)
        _top_nodes: list = []
        if store is not None:
            try:
                if hasattr(store, "get_top_seed_nodes"):
                    _top_nodes = store.get_top_seed_nodes(n=10)
            except (AttributeError, RuntimeError):
                pass

        handoff = ExportHandoff(
            sprint_id=sprint_id,
            scorecard=scorecard_data,
            top_nodes=_top_nodes,
            phase_durations=phase_timings,
        )
        export_result = await _export_sprint(store, handoff)
        logger.info(
            "sprint_export_complete",
            report_json=export_result.get("report_json", ""),
            seeds_json=export_result.get("seeds_json", ""),
        )
    except (RuntimeError, OSError) as e:
        logger.warning("export_sprint_failed", error=str(e))


async def _windup_synthesis(
    query: str,
    store: Any,
    lifecycle: SprintLifecycleManager,
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
    from hledac.universal.brain.model_lifecycle import ModelLifecycle
    from hledac.universal.brain.synthesis_runner import SynthesisRunner, export_report

    runner = SynthesisRunner(ModelLifecycle())
    # F234: Enable MLX-first context compression for M1 8GB safety
    runner.set_compression_threshold(4000)

    # Sprint 8VQ: Priority 1 — dedicated STIX truth-store graph (IOCGraph/Kuzu)
    # Created in _run_sprint_mode WINDUP block and injected via store.inject_stix_graph()
    try:
        stix_graph = store.get_stix_graph() if hasattr(store, "get_stix_graph") else None
        if stix_graph is not None:
            runner.inject_stix_graph(stix_graph)
        else:
            # Sprint 8VY: Priority 2 — analytics/donor graph via explicit seam
            # Previously: elif hasattr(store, "_ioc_graph") and store._ioc_graph: runner.inject_graph(store._ioc_graph)
            analytics_graph = (
                store.get_analytics_graph_for_synthesis()
                if hasattr(store, "get_analytics_graph_for_synthesis")
                else None
            )  # noqa: E501
            if analytics_graph is not None:
                runner.inject_graph(analytics_graph)
    except (ImportError, AttributeError, RuntimeError):
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
    except (AttributeError, RuntimeError) as e:
        logger.warning("windup_fetch_findings_failed", error=str(e))

    if not findings:
        logger.info("windup_no_findings")
        await runner.close()
        return None

    # Run synthesis (WINDUP phase check is inside synthesize_findings)
    report = await runner.synthesize_findings(
        query=query,
        findings=findings,
        force_synthesis=True,  # B.7: explicit force for programmatic call
    )

    # Sprint 8VA D: HypothesisEngine closed loop — generate hypotheses from findings
    if findings and findings:
        try:
            from hledac.universal.brain.research_hypothesis_engine import HypothesisEngine

            _hyp_engine = HypothesisEngine()
            finding_texts = [f.get("text", "")[:200] for f in findings[:10]]
            hypotheses = await _hyp_engine.generate_sprint_hypotheses(
                findings=finding_texts,
                ioc_graph=None,
                max_hypotheses=3,
            )
            # Sprint 8VA D.2: Každá hypotéza → logged (pivot_queue requires SprintScheduler access)
            for i, hyp in enumerate(hypotheses or [], 1):
                hyp_text = hyp if isinstance(hyp, str) else str(hyp)
                logger.info("hypothesis_generated", index=i, hyp_text=hyp_text[:80])
        except (ImportError, AttributeError, RuntimeError) as e:
            logger.debug("hypothesis_engine_skipped", error=str(e))

    # Sprint 8UC B.2.4: Capture synthesis engine for scorecard
    getattr(runner, "_last_synthesis_engine", "unknown")

    await runner.close()

    if report is not None:
        # Export to JSON
        await export_report(report, query)
        logger.info(
            "windup_synthesis_complete",
            ioc_count=len(report.ioc_entities),
            threat_actor_count=len(report.threat_actors),
        )
    else:
        logger.info("windup_synthesis_returned_none")

    return report


# =============================================================================
# Main entry point
# =============================================================================


def _fatal(exc: BaseException, code: int = 1) -> None:
    """
    Structured fatal-error handler. Logs _MAIN_FATAL with full traceback,
    then exits with a structured exit code.

    Exit code convention (Sprint F350M-R Exit Codes):
        0   = clean success
        1   = runtime error (unexpected)
        2   = config/validation error (e.g. windup_lead guard)
        3   = programmer error / regression (NameError, ImportError, AttributeError)
        130 = SIGINT (KeyboardInterrupt)
    """
    logger.critical("_MAIN_FATAL [exit=%d]: %s", code, exc, traceback=traceback.format_exc())
    sys.exit(code)


def main() -> None:
    """
    Synchronous CLI entry point — delegates to cli.parser.dispatch().

    Boot flow:
        1. Pre-boot: dotenv, logging, setproctitle, OPSEC guard
        2. Parse args via cli.parser.build_parser()
        3. --list-presets short-circuit
        4. --preset application + flag validation
        5. LMDB boot guard
        6. dispatch() → subcommand handler (sprint | pivot | ct)
    """
    # F265ENV: Load .env file before any ENV access
    load_dotenv()

    # Configure structured logging (structlog with stdlib fallback)
    configure_logging()

    # Sprint 7C: process masking
    try:
        import setproctitle

        setproctitle.setproctitle("kernel_worker")
    except ImportError:
        pass

    # F214E: log PID once at boot
    logger.info("boot_pid", pid=os.getpid())

    # F214Q: Remote debug OPSEC guard
    if os.environ.get("PYTHON_DISABLE_REMOTE_DEBUG") != "1":
        if os.environ.get("HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED") == "1":
            sys.exit(
                "HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED=1 but PYTHON_DISABLE_REMOTE_DEBUG not set — "
                "OSINT runtime requires external debugger disabled"
            )
        logger.warning("opsec_remote_debug_active")

    # Import CLI parser from new cli/ package
    from hledac.universal.cli.parser import build_parser, dispatch

    parser = build_parser()

    # Fast --help path — before heavy imports
    if "--help" in sys.argv or "-h" in sys.argv:
        parser.print_help()
        print()
        print("Sprint usage:")
        print("  python -m hledac.universal sprint --sprint 'query'")
        print("  python -m hledac.universal sprint --sprint 'LockBit ransomware' --duration 1800")
        print()
        print("Other commands:")
        print("  python -m hledac.universal pivot --pivot 'ransomware CVE'")
        print("  python -m hledac.universal ct --ct-pivot example.com")
        sys.exit(0)

    args = parser.parse_args()

    # Issue #19: --profile flag → HLEDAC_OTEL_PROFILE=1 + OTLP exporter
    if args.profile:
        os.environ["HLEDAC_OTEL_PROFILE"] = "1"
        if os.environ.get("HLEDAC_OTEL_EXPORTER", "") not in ("otlp", "duckdb", "logfire"):
            os.environ.setdefault("HLEDAC_OTEL_EXPORTER", "otlp")

    # --list-presets short-circuit
    if getattr(args, "list_presets", False):
        try:
            from hledac.universal.utils.flag_presets import list_presets_table

            print(list_presets_table())
        except (ImportError, AttributeError) as exc:
            print(f"flag_presets unavailable: {exc!r}", file=sys.stderr)
        sys.exit(0)

    # --preset application
    preset_name = getattr(args, "preset", None)
    if preset_name:
        try:
            from hledac.universal.utils.flag_presets import apply_preset

            applied = apply_preset(preset_name, overwrite=False)
            logger.info("flag_preset_applied", preset=preset_name, flag_count=len(applied))
        except (ValueError, RuntimeError) as exc:
            logger.error("flag_preset_failed", preset=preset_name, error=str(exc))
            sys.exit(2)

    # Flag combo validation
    try:
        from hledac.universal.utils.flag_registry import validate_flag_combo

        errors, warnings = validate_flag_combo()
        for w in warnings:
            logger.warning("flag_validation_warning", warning=w)
        if errors:
            for e in errors:
                logger.error("flag_conflict", error=e)
            sys.exit(2)
    except (ImportError, AttributeError) as exc:
        logger.warning("flag_validation_internal_error", error=str(exc))

    # Sprint 8AI: LMDB boot guard — runs before any command
    _boot_record("boot_guard_sync", "starting")
    try:
        removed, reason = _run_boot_guard()
        logger.info("boot_guard_result", removed=removed, reason=reason)
        _boot_record("boot_guard_sync", "ok", removed=removed, reason=reason)
    except BootGuardError as e:
        logger.error("boot_guard_unsafe_state", error=str(e))
        _boot_record("boot_guard_sync", "unsafe_abort", error=str(e))
        sys.exit(1)
    except OSError as e:
        logger.warning("boot_guard_error", error=str(e))
        _boot_record("boot_guard_sync", "error_soft", error=str(e))

    # Dispatch to subcommand handler
    try:
        code = dispatch(args)
        sys.exit(code)
    except (NameError, AttributeError, ImportError) as e:
        _fatal(e, code=3)
    except KeyboardInterrupt:
        logger.info("interrupted_by_user")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        _fatal(e, code=1)


if __name__ == "__main__":
    main()


# =============================================================================
# Sprint 8VX §D: run_warmup() — moved from runtime/sprint_lifecycle.py
# This is WARMUP-phase orchestration, NOT lifecycle state machine.
# Kept at module level (no SprintScheduler dependency in sprint mode).
# =============================================================================

import logging  # noqa: E402

TYPE_CHECKING  # noqa: B018

if TYPE_CHECKING:
    from hledac.universal.runtime.sprint_scheduler import SprintScheduler  # noqa: F401

_logger = get_logger(__name__)
