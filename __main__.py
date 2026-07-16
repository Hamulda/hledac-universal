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
from typing import TYPE_CHECKING, Annotated, Any

# ISSUE-50: msgspec.Meta validators for msgspec.Struct field validation
# msgspec natively supports Annotated[T, Meta(ge=0, gt=0, le=1.0)] style constraints
# No external dependency needed — uses msgspec built-in Meta class
from msgspec import Meta

from dotenv import load_dotenv

from hledac.universal.utils.async_helpers import _check_gathered, safe_create_task, safe_wait_for, stop_task
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
    if _sys.version_info >= (3, 15):  # pyright: ignore[unreachable]
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
# F350M-R ISSUE #5: BootTelemetryDrainer — async worker (eager_start=True),
#   asyncio.Queue batch flush, aiofiles async write, zero event-loop blocking
# =============================================================================

_BOOT_TELEMETRY_MAX: int = 200
_boot_telemetry: deque[dict[str, Any]] = deque(maxlen=_BOOT_TELEMETRY_MAX)


# ---- BootTelemetryDrainer singleton ----


class _BootTelemetryDrainer:
    """
    Async background worker that drains boot telemetry to disk.

    Design (F350M-R ISSUE #5):
    - asyncio.Queue (maxsize=512) accepts records from any context
    - Single eager_start=True worker batches writes every 2s or 50 records
    - aiofiles for async file I/O — zero event-loop blocking on disk
    - orjson / rust json.compact for serialization
    - Fail-soft: any error drops to pass, never raises
    """

    __slots__ = ("_queue", "_task", "_log_path", "_compact", "_started")

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=512)
        self._task: asyncio.Task[None] | None = None
        self._log_path: pathlib.Path = pathlib.Path.home() / ".hledac" / "logs" / "boot.jsonl"
        self._compact: Callable[[dict[str, Any]], str] | None = None
        self._started: bool = False

    def _get_compact(self) -> Callable[[dict[str, Any]], str]:
        """Lazy-load orjson or rust json.compact (called from worker thread)."""
        if self._compact is None:
            try:
                from core.rust_backend import rust as _rust
                self._compact = lambda d: _rust.json.compact(d)
            except Exception:
                import orjson
                self._compact = lambda d: orjson.dumps(d).decode()
        return self._compact

    async def start(self) -> None:
        """Start the drain worker (idempotent)."""
        if self._started:
            return
        self._started = True
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._task = safe_create_task(
            self._drain_loop(),
            name="boot_telemetry_drain",
            eager_start=True,  # F350M-R: fire immediately — queue worker is hot path
        )

    async def stop(self) -> None:
        """Graceful stop: flush all pending, then cancel worker."""
        if not self._started or self._task is None:
            return
        try:
            await self._queue.join()  # drain all pending
        except Exception:
            pass
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def record(self, step: str, status: str, **kw: Any) -> None:
        """
        Enqueue a boot telemetry record.

        Bounded: if queue is full (512), the record is dropped silently —
        telemetry is best-effort and must never block the caller.
        """
        record = {"step": step, "status": status, "ms": time.time(), **kw}
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(record)

    async def _drain_loop(self) -> None:
        """Background worker: batch-flush queue to disk every 2s or 100 records."""
        batch: list[dict[str, Any]] = []
        drain_count = _BOOT_TELEMETRY_MAX // 2  # 100
        compact = self._get_compact()

        while True:
            try:
                async with asyncio.timeout(2.0):
                    item = await self._queue.get()
                batch.append(item)
            except asyncio.CancelledError:
                # Graceful shutdown: flush remaining and exit
                if batch:
                    await self._flush_batch(batch, compact)
                raise
            except asyncio.TimeoutError:
                if batch:
                    await self._flush_batch(batch, compact)
                    batch.clear()
                continue

            if len(batch) >= drain_count:
                await self._flush_batch(batch, compact)
                batch.clear()

    async def _flush_batch(
        self,
        batch: list[dict[str, Any]],
        compact: Callable[[dict[str, Any]], str],
    ) -> None:
        """Write a batch of records to boot.jsonl using aiofiles + atomic rename."""
        try:
            import aiofiles, tempfile, os

            # Build payload in memory — encode lines individually then join
            lines_out: list[bytes] = []
            for rec in batch:
                line_bytes = compact(rec).encode() if isinstance(compact(rec), str) else compact(rec)
                lines_out.append(line_bytes + b"\n")
            payload = b"".join(lines_out)
            # Atomic write: tempfile + rename
            tmp = tempfile.NamedTemporaryFile(
                mode="wb", dir=self._log_path.parent, delete=False, suffix=".tmp"
            )
            try:
                tmp.write(payload)
                tmp.close()
                with contextlib.suppress(FileNotFoundError):
                    os.replace(tmp.name, self._log_path)
            except Exception:
                contextlib.suppress(FileNotFoundError, OSError)
                raise
            finally:
                try:
                    os.unlink(tmp.name)
                except FileNotFoundError:
                    pass
        except Exception:
            pass  # fail-soft: telemetry is best-effort


# Singleton — created lazily on first async context
_boot_drainer: _BootTelemetryDrainer | None = None


def _get_boot_drainer() -> _BootTelemetryDrainer:
    global _boot_drainer
    if _boot_drainer is None:
        _boot_drainer = _BootTelemetryDrainer()
    return _boot_drainer


async def _boot_record_async(step: str, status: str, **kw: Any) -> None:
    """
    Append a boot telemetry entry via the async drainer.

    F350M-R ISSUE #5 fix: this replaces the old sync _boot_record() which
    performed blocking disk I/O from inside the event loop.

    The drainer singleton starts automatically on first call.
    """
    d = _get_boot_drainer()
    await d.start()  # idempotent
    await d.record(step, status, **kw)


def _boot_record_sync(step: str, status: str, **kw: Any) -> None:
    """
    Sync wrapper for _boot_record_async — used ONLY from non-async context (main()).

    F350M-R ISSUE #5 fix: wraps the async version in asyncio.to_thread to avoid
    blocking the event loop on sync call sites.
    """
    d = _get_boot_drainer()
    # Ensure drainer is started (queue worker)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — create a new one (sync context, e.g. main())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(d.start())
    except Exception:
        pass
    # Run the record — loop is guaranteed available from above
    try:
        loop.run_until_complete(_boot_record_async(step, status, **kw))
    except Exception:
        pass


def _boot_record(step: str, status: str, **kw: Any) -> None:
    """
    Public entry point — dispatches to async or sync based on context.

    DEPRECATED: async callers should use _boot_record_async() directly.
    """
    try:
        loop = asyncio.get_running_loop()
        import warnings

        warnings.warn(
            "_boot_record called from async context — use await _boot_record_async instead",
            DeprecationWarning,
            stacklevel=2,
        )
        # Create task and store reference to prevent garbage collection
        # F350M-R ISSUE #31: use safe_create_task with eager_start=True (CPU-bound on hot path)
        task = safe_create_task(_boot_record_async(step, status, **kw), eager_start=True)
        # Fire-and-forget but track at least in task list
        task.add_done_callback(lambda _: None)  # silence unhandled exception warnings
    except RuntimeError:
        _boot_record_sync(step, status, **kw)


def get_boot_telemetry() -> list[dict[str, Any]]:
    """Return copy of in-memory boot telemetry. O(1) snapshot."""
    return list(_boot_telemetry)


def clear_boot_telemetry() -> None:
    """Clear boot telemetry. For tests only."""
    _boot_telemetry.clear()


# Sprint 8AI: Status helper — O(1), side-effect free, diagnostic only
# Sprint 8AM C.7: Extended with owned resource tracking

# Sprint 8AM C.7: Owned resource registry (set by _run_public_passive_once)
# ISSUE-9 FIX: msgspec.Struct for type-safe owned resource tracking
class OwnedResources(msgspec.Struct, frozen=True, kw_only=True):
    """Owned resource registry — ISSUE-9: was dict[str, bool]."""

    session_owned: bool = False
    store_owned: bool = False


_owned_resources = OwnedResources()


def get_runtime_status() -> dict[str, Any]:
    """
    Return current runtime status snapshot.
    O(1), side-effect free, purely diagnostic.

    Sprint 8AM C.7: Extended to include owned resource tracking.
    """
    return {
        "uvloop_installed": _uvloop_installed,
        "boot_telemetry": get_boot_telemetry(),
        "signal_handlers_installed": False,  # DEPRECATED: handled in runtime/sprint_entrypoint
        "signal_teardown_flag": False,  # DEPRECATED: handled in runtime/sprint_entrypoint
        # Sprint 8AM C.7 — ISSUE-9: OwnedResources msgspec.Struct
        "session_owned": _owned_resources.session_owned,
        "store_owned": _owned_resources.store_owned,
        "owned_resources": [n for n in ("session_owned", "store_owned") if getattr(_owned_resources, n)],
        "owned_resource_count": (_owned_resources.session_owned + _owned_resources.store_owned),
        "last_error": None,
    }


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
        _boot_record_sync("boot_guard", "ok", removed=removed, reason=reason)
        return removed, reason
    except _BootGuardError:
        # Re-raise BootGuardError without wrapping — caller decides to abort
        raise
    except OSError as e:
        _boot_record_sync("boot_guard", "error", error=str(e))
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
        await _boot_record_async("task_cancellation", f"cancelling_{count}_tasks")
        await _boot_record_async("task_cancellation", f"completed_{count}_tasks")


# =============================================================================
# Sprint 8AM C.1: Owned Runtime Path — Public Passive Once
# =============================================================================


async def _run_public_passive_once(
    shutdown_event: asyncio.Event,
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
        shutdown_event: asyncio.Event set when shutdown signal received.
            Replaces the old stop_flag() callable pattern.
        owned_session: If True, acquire and own the shared aiohttp session.
        owned_store: If True, create and own a DuckDBShadowStore instance.
    """
    global _owned_resources

    await _boot_record_async("public_passive_once", "entered")

    # Reset owned resources tracking
    # ISSUE-9 FIX: OwnedResources is frozen, must create new instance
    _owned_resources = OwnedResources()

    exit_stack: contextlib.AsyncExitStack | None = None
    store_instance = None

    try:
        exit_stack = contextlib.AsyncExitStack()
        await exit_stack.__aenter__()

        await _boot_record_async("async_exit_stack_entered", "ok")

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
                # ISSUE-9 FIX: frozen struct - reassign, don't mutate
                _owned_resources = OwnedResources(session_owned=True)
                await _boot_record_async("session_owned", "registered")
            except Exception as e:
                logger.warning("acquire_session_failed", error=str(e))
                await _boot_record_async("session_owned", "failed", error=str(e))

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
                # ISSUE-9 FIX: frozen struct - reassign with both flags set
                _owned_resources = OwnedResources(
                    session_owned=_owned_resources.session_owned,
                    store_owned=True,
                )
                await _boot_record_async("store_owned", "registered")
            except Exception as e:
                logger.warning("acquire_store_failed", error=str(e))
                await _boot_record_async("store_owned", "failed", error=str(e))
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
        await _boot_record_async("pipeline_web", "completed", discovered=web_result.discovered)

        feed_result = await async_run_default_feed_batch(
            store=store_instance,
            max_entries_per_feed=10,
            query_context="public passive OSINT",
        )
        await _boot_record_async("pipeline_feed", "completed", sources=feed_result.total_sources)

        # F350M-R ISSUE #4: zero-CPU idle shutdown — await Event.wait() instead of busy-poll
        await shutdown_event.wait()

        await _boot_record_async("public_passive_once", "signal_received")

    except asyncio.CancelledError:
        await _boot_record_async("public_passive_once", "cancelled")
        raise

    except Exception as e:
        await _boot_record_async("public_passive_once", "exception", error=str(e))
        logger.error("fatal_error", error=str(e), exc_info=True)
        raise

    finally:
        # Sprint 8AM C.8: Orphan tasks drained BEFORE this point (in _cancel_orphan_tasks)
        # Sprint 8AM C.4: AsyncExitStack unwind — LIFO cleanup order:
        #   1. store close (registered first)
        #   2. session close (registered last)
        if exit_stack is not None:
            await _boot_record_async("async_exit_stack_unwind", "starting")
            try:
                await exit_stack.__aexit__(None, None, None)
                await _boot_record_async("async_exit_stack_unwind", "completed")
            except (RuntimeError, OSError) as e:
                logger.warning("async_exit_stack_unwind_error", error=str(e))
                await _boot_record_async("async_exit_stack_unwind", "error", error=str(e))

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
# Sprint 8AO: Observed Live Run — Report Structure & Helpers
# =============================================================================

# Issue #13: Bounded LRU cache for run reports — prevents unbounded memory growth
# when feeds return 100+ sources with 10+ entries each (potentially MB per report).
# Max 5 reports retained for time-series comparison; older reports are dropped.
_MAX_OBSERVED_RUN_REPORTS: int = 5


class BoundedReportCache:
    """
    M1 8GB-safe bounded LRU cache for ObservedRunReport.

    Bounded: max _MAX_OBSERVED_RUN_REPORTS entries retained.
    Zero-allocation on hit path (same-pointer return, no copy).
    Thread-safe via asyncio.Lock for the small mutation surface.
    """

    __slots__ = ("_cache", "_lock", "_history_len", "_max_size")

    def __init__(self, max_size: int = _MAX_OBSERVED_RUN_REPORTS) -> None:
        self._cache: dict[str, ObservedRunReport] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._history_len: int = 0
        self._max_size = max_size

    def _evict_lru(self) -> None:
        """Evict oldest entry when cache is full — O(1)."""
        # Remove first inserted key (oldest)
        if self._cache:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]

    def get_last(self) -> ObservedRunReport | None:
        """Return most recent report by started_ts — alias for get_latest()."""
        return self.get_latest()

    def put(self, report: ObservedRunReport) -> None:
        """Store report with timestamp key; evict LRU when at capacity."""
        key = f"{report.started_ts:.3f}"
        if key in self._cache:
            return  # dedup: don't re-insert same timestamp
        if len(self._cache) >= self._max_size:
            self._evict_lru()
        self._cache[key] = report
        self._history_len += 1

    async def put_async(self, report: ObservedRunReport) -> None:
        """Async-safe put — all mutations inside the lock guard."""
        key = f"{report.started_ts:.3f}"
        async with self._lock:
            if key in self._cache:
                return
            if len(self._cache) >= self._max_size:
                self._evict_lru()
            self._cache[key] = report
            self._history_len += 1

    def get_latest(self) -> ObservedRunReport | None:
        """Return most recent report by started_ts — O(n) scan, call sparingly."""
        if not self._cache:
            return None
        return max(self._cache.values(), key=lambda r: r.started_ts)


# Sprint 8BA C.0: Runtime truth fields (recorded before/after live run)
_actual_live_run_executed: bool = False
_interpreter_executable: str = ""
_interpreter_version: str = ""
_ahocorasick_available: bool = False
_bootstrap_pack_version: int = 0
_default_bootstrap_count: int = 0


def _record_runtime_truth() -> None:
    """Record python3 interpreter truth at module load time."""
    global _interpreter_executable, _interpreter_version, _ahocorasick_available
    global _bootstrap_pack_version, _default_bootstrap_count

    import sys

    _interpreter_executable = sys.executable
    _interpreter_version = "3.14" if sys.version_info[:2] == (3, 14) else sys.version  # type: ignore[unreachable]

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


# Module-level aliases for test compatibility (D.10)
actual_live_run_executed = _actual_live_run_executed
interpreter_executable = _interpreter_executable
interpreter_version = _interpreter_version
ahocorasick_available = _ahocorasick_available


# ISSUE-9 FIX: UmaSnapshot msgspec.Struct for typed UMA metrics
class UmaSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    """UMA memory snapshot — ISSUE-9: was raw dict.

    ISSUE-50: Numeric fields validated as non-negative via Annotated.
    """

    peak_used_gib: Annotated[float, Meta(ge=0)] = 0.0
    peak_swap_used_gib: Annotated[float, Meta(ge=0)] = 0.0
    peak_state: str = ""
    start_state: str = ""
    end_state: str = ""
    sample_count: Annotated[int, Meta(ge=0)] = 0


class ObservedRunReport(msgspec.Struct, frozen=True, kw_only=True):
    """
    Structured observability report for a bounded observed feed batch run.

    C.1: All required fields present.
    C.7: content_quality_validated reflects PatternMatcher availability.

    NOTE: slots=True requires msgspec 0.20+ (not yet released as of 2026-07).

    ISSUE-50: Field validation via typing.Annotated + msgspec.Meta validators.
    Use validate_observed_run_report() for strict validation on construction.
    """

    # ISSUE-50: Annotated validators — gt=0, ge=0, interval constraints
    # Unix timestamps must be positive (> 0)
    started_ts: Annotated[float, Meta(gt=0)]
    # finished_ts must be > started_ts — validated via cross_field_validator
    finished_ts: float
    # Elapsed time must be non-negative
    elapsed_ms: Annotated[float, Meta(ge=0)]
    # Source counts must be positive for total, non-negative for completed
    total_sources: Annotated[int, Meta(gt=0)]
    completed_sources: Annotated[int, Meta(ge=0)]
    # Entry/finding counts must be non-negative
    fetched_entries: Annotated[int, Meta(ge=0)]
    accepted_findings: Annotated[int, Meta(ge=0)]
    stored_findings: Annotated[int, Meta(ge=0)]
    batch_error: str | None
    per_source: tuple[dict, ...]
    patterns_configured: Annotated[int, Meta(ge=0)]
    bootstrap_applied: bool
    content_quality_validated: bool
    # Dedup raw deltas (C.2)
    dedup_before: dict
    dedup_after: dict
    dedup_delta: dict
    dedup_surface_available: bool
    # UMA snapshot (C.3) — ISSUE-9: UmaSnapshot msgspec.Struct
    uma_snapshot: UmaSnapshot
    # Slow-source ranking (C.10)
    slow_sources: tuple[dict, ...]
    # Error summary (C.11)
    error_summary: dict
    # Sprint 8AS C.2: Success rate must be 0.0-1.0, failed count non-negative
    success_rate: Annotated[float, Meta(ge=0.0, le=1.0)]
    failed_source_count: Annotated[int, Meta(ge=0)]
    # Sprint 8AS C.0: Baseline delta summary
    baseline_delta: dict
    # Sprint 8AS C.1: Feed health breakdown — ISSUE-9: FeedHealthBreakdown msgspec.Struct
    health_breakdown: FeedHealthBreakdown
    # Sprint 8AU: pre-store signal trace — all counts non-negative
    entries_seen: Annotated[int, Meta(ge=0)]
    entries_with_empty_assembled_text: Annotated[int, Meta(ge=0)]
    entries_with_text: Annotated[int, Meta(ge=0)]
    entries_scanned: Annotated[int, Meta(ge=0)]
    entries_with_hits: Annotated[int, Meta(ge=0)]
    total_pattern_hits: Annotated[int, Meta(ge=0)]
    findings_built_pre_store: Annotated[int, Meta(ge=0)]
    avg_assembled_text_len: Annotated[float, Meta(ge=0)]
    signal_stage: str = "unknown"
    # Sprint 8AV: store rejection delta (can be negative — deltas)
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
    bootstrap_pack_version: Annotated[int, Meta(ge=0)] = 0
    default_bootstrap_count: Annotated[int, Meta(ge=0)] = 0
    store_counters_reset_before_run: bool = False
    matcher_probe_sample_used: str = ""
    matcher_probe_rss_hits: tuple[str, ...] = ()
    # Sprint 8BC: bounded sample capture from pipeline
    sample_scanned_texts: tuple[str, ...] = ()
    sample_hit_counts: tuple[int, ...] = ()
    sample_hit_labels_union: tuple[str, ...] = ()
    sample_texts_truncated: bool = False
    feed_content_mismatch: bool = False
    patterns_configured_at_run: Annotated[int, Meta(ge=0)] = 0
    automaton_built_at_run: bool = False
    # Sprint 8BH C.0: live run truth fields
    used_rich_feed_content: bool = False
    used_article_fallback: bool = False
    matched_feed_names: tuple[str, ...] = ()
    accepted_feed_names: tuple[str, ...] = ()
    live_run_attempt_count: Annotated[int, Meta(ge=0)] = 0
    live_run_attempt_1_result: str = ""
    live_run_attempt_2_result: str = ""
    recommended_next_sprint: str = ""
    # E0-T4: runtime truth taxonomy
    active_pipeline_iterations: Annotated[int, Meta(ge=0)] = 0
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
    active_pipeline_iterations: Annotated[int, Meta(ge=0)] = 0


# ISSUE-50: Validated ObservedRunReport construction
# Use this factory function for strict annotated-types validation
def validate_observed_run_report(data: dict) -> ObservedRunReport:
    """
    Construct ObservedRunReport with strict annotated-types validation.

    Performs two-stage validation:
    1. msgspec.convert with strict=True — enforces Annotated[...] constraints
    2. Cross-field validation — finished_ts > started_ts

    Raises:
        msgspec.ValidationError: If annotated-types constraints violated
        ValueError: If cross-field validation fails

    Usage:
        report = validate_observed_run_report(raw_dict)
    """
    # Stage 1: annotated-types validation via msgspec strict mode
    # This enforces Gt(0), Ge(0), Interval(ge=0.0, le=1.0) constraints
    try:
        report = msgspec.convert(data, ObservedRunReport, strict=True)
    except msgspec.ValidationError as e:
        raise msgspec.ValidationError(f"ObservedRunReport field validation failed: {e}") from e

    # Stage 2: cross-field validation (not expressible via Annotated)
    if not isinstance(report.finished_ts, (int, float)) or not isinstance(report.started_ts, (int, float)):
        raise ValueError(
            f"finished_ts and started_ts must be numeric, "
            f"got finished_ts={type(report.finished_ts).__name__}, "
            f"started_ts={type(report.started_ts).__name__}"
        )
    if report.finished_ts <= report.started_ts:
        raise ValueError(
            f"finished_ts ({report.finished_ts}) must be > started_ts ({report.started_ts})"
        )

    return report


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


def dedup_surface_available(before: dict, after: dict) -> bool:
    """Check if dedup surface is available in both snapshots."""
    return bool(before.get("persistent_dedup_enabled") or after.get("persistent_dedup_enabled"))


# =============================================================================
# Sprint 8AS: 8AO Baseline Comparison
# C.0, B.4
# =============================================================================

# 8AO baseline truth (Sprint 8AO live run — bounded, same limits)
# ISSUE-9 FIX: SprintBaseline msgspec.Struct for typed baseline comparison
class SprintBaseline(msgspec.Struct, frozen=True, kw_only=True):
    """Baseline values from Sprint 8AO live run — ISSUE-9: was dict.

    ISSUE-50: All numeric fields validated as non-negative via Annotated.
    """

    total_sources: Annotated[int, Meta(ge=0)] = 5
    completed_sources: Annotated[int, Meta(ge=0)] = 1
    fetched_entries: Annotated[int, Meta(ge=0)] = 10
    accepted_findings: Annotated[int, Meta(ge=0)] = 0
    stored_findings: Annotated[int, Meta(ge=0)] = 0
    elapsed_ms: Annotated[float, Meta(ge=0)] = 1557.6
    pattern_count: Annotated[int, Meta(ge=0)] = 0  # infra-only
    failed_source_count: Annotated[int, Meta(ge=0)] = 4


_SPRINT_8AO_BASELINE = SprintBaseline()


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
        "completed_sources_delta": current_completed - b.completed_sources,
        "fetched_entries_delta": current_fetched - b.fetched_entries,
        "accepted_findings_delta": current_accepted - b.accepted_findings,
        "stored_findings_delta": current_stored - b.stored_findings,
        "failed_source_count": current_failed,
        "failed_source_count_delta": current_failed - b.failed_source_count,
        "findings_delta": current_accepted - b.accepted_findings,
        "elapsed_ms_delta": current_elapsed - b.elapsed_ms,
        "baseline_ref": "8AO",
    }

    # Determine status
    if current_completed == 0 and current_fetched == 0:
        delta["status"] = "network_variance"
        delta["blocker"] = "no_sources_completed_no_fetched"
    elif current_accepted > b.accepted_findings:
        delta["status"] = "improved"
        delta["blocker"] = None
    elif current_accepted < b.accepted_findings:
        delta["status"] = "regressed"
        delta["blocker"] = None
    else:
        # current_accepted == baseline (0) — could be network or genuine
        if current_completed < b.completed_sources:
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


# ISSUE-9 FIX: FeedHealthBreakdown msgspec.Struct replaces nested dict
class FeedHealthBreakdown(msgspec.Struct, frozen=True, kw_only=True):
    """Feed health classification result — ISSUE-9: was nested dict.

    ISSUE-50: All counts are non-negative via Annotated validators.
    """

    success: Annotated[int, Meta(ge=0)] = 0
    network_error: Annotated[int, Meta(ge=0)] = 0
    parse_error: Annotated[int, Meta(ge=0)] = 0
    entity_recovery_related_error: Annotated[int, Meta(ge=0)] = 0
    timeout_error: Annotated[int, Meta(ge=0)] = 0
    unknown_error: Annotated[int, Meta(ge=0)] = 0


def classify_feed_health(per_source: tuple[dict, ...]) -> FeedHealthBreakdown:
    """
    Sprint 8AS C.1: Classify per-source results into health categories.
    ISSUE-9: Returns FeedHealthBreakdown msgspec.Struct instead of nested dict.
    """
    # ISSUE-9 FIX: Use mutable counters, convert to frozen struct at end
    success = 0
    network_error = 0
    parse_error = 0
    entity_recovery_related_error = 0
    timeout_error = 0
    unknown_error = 0

    for src in per_source:
        error = src.get("error") or ""
        if not error:
            success += 1
        elif "timeout" in error.lower() or "timed out" in error.lower():
            timeout_error += 1
        elif "entity" in error.lower() or "recovery" in error.lower() or "recover" in error.lower():
            entity_recovery_related_error += 1
        elif "parse" in error.lower() or "xml" in error.lower() or "feed" in error.lower() or "html" in error.lower():
            parse_error += 1
        elif (
            "network" in error.lower()
            or "connection" in error.lower()
            or "dns" in error.lower()
            or "resolve" in error.lower()
            or "http" in error.lower()
            or "ssl" in error.lower()
            or "certificate" in error.lower()
        ):  # noqa: E501
            network_error += 1
        else:
            unknown_error += 1

    return FeedHealthBreakdown(
        success=success,
        network_error=network_error,
        parse_error=parse_error,
        entity_recovery_related_error=entity_recovery_related_error,
        timeout_error=timeout_error,
        unknown_error=unknown_error,
    )


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
# Sprint 8PC: sprint_mode entrypoint
# =============================================================================

# Sprint 8PC: module-level flag for EMERGENCY state (stops new frontier work)
_sprint_frontier_stopped: bool = False

# Sprint 8TA B.2: Phase timing (bounded — ISSUE #12)
# deque(maxlen=256) auto-evicts oldest entry when full (~18KB max for 256 entries)
_phase_times: deque[tuple[str, float]] = deque(maxlen=256)

# Sprint F204E: Analyst brief for markdown export (set after scheduler.run completes)
_analyst_brief_for_markdown: dict[str, Any] | None = None


def _mark_phase(name: str) -> None:
    """Mark phase start time. Called at the beginning of each phase."""
    _phase_times.append((name, time.monotonic()))
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
    ISSUE #36 FIX: Parallel metric collection via asyncio.gather.

    Called at the end of EXPORT phase.
    - findings_per_minute = accepted / (elapsed / 60)
    - ioc_density = ioc_nodes / max(1, accepted)
    - semantic_novelty: 1.0 fallback (no SemanticStore available)
    - source_yield: dict {source_type: count} from per-source counter
    - ghost_global: upsert top IOC entities

    ACTUAL PHASE STRUCTURE (ISSUE #36):
      Phase 1 (parallel): asyncio.gather over 5× asyncio.to_thread (dedup, arrow, cb, RSS, ghost)
      Phase 2 (print):    console output, depends on Phase 1
      Phase 3 (sequential): create duckdb_write_tasks (no await yet — fire-and-forget)
      Phase 4 (hybrid):   _export_markdown_report (sequential, needs scorecard_data)
                           THEN _ghost_global_and_wait (parallel: ghost_global + await duckdb writes)
      NOTE: _check_gathered for duckdb writes lives inside Phase 4 (_ghost_global_and_wait),
            so Phase 3 is task-creation only — no separate gather measurement for it.
    """
    import resource as _resource

    # ISSUE-039: Lazy-load Rust json for hot-path serialization (scorecard, telemetry).
    # Prefers rust.json.dumps_compact_bytes (SIMD-accelerated), falls back to orjson.
    def _json_compact(data: dict) -> str:
        try:
            from core.rust_backend import get_accel
            accel = get_accel()
            if accel.is_available:
                result = accel.json.dumps_compact_bytes(data)
                if result:
                    return result.decode("utf-8")
        except Exception:
            pass
        import orjson
        return orjson.dumps(data).decode()

    # Get sprint duration from lifecycle
    sprint_id = f"sprint_{int(time.time())}"
    ts = time.time()

    # Compute phase timings dict
    phase_timings: dict[str, float] = {}
    if _phase_times:
        sorted_phases = sorted(_phase_times, key=lambda x: x[1])
        for i, (name, start) in enumerate(sorted_phases):
            if i + 1 < len(sorted_phases):
                end = sorted_phases[i + 1][1]
                phase_timings[name] = round(end - start, 3)
            else:
                phase_timings[name] = 0.0

    # Estimate elapsed from phase timings
    elapsed = sum(phase_timings.values()) if phase_timings else 0.0

    # Phase 1: Parallel metric collection via asyncio.gather
    # ISSUE #36: All sync blocking I/O runs in thread pool to avoid event-loop blocking.
    # Circuit breaker uses threading.Lock (thread-safe per PMB rule [3205]).

    def _sync_get_dedup(sprint_rep: Any = None) -> tuple[int, int, dict[str, int]]:
        """
        Thread-pool worker: fetch dedup runtime status from DuckDB.

        ISSUE #36-C FIX: source_yield derived from sprint_report.findings
        (per-source accepted count), avoiding the need for store-side tracking.
        """
        accepted_val, ioc_val = 0, 0
        source_yield_val: dict[str, int] = {}
        # ISSUE #36-C: source_yield from sprint_report.findings (sprint_report is
        # available in enclosing scope — pass explicitly so the closure is clear).
        if sprint_rep is not None:
            try:
                findings_iterable = sprint_rep.findings if hasattr(sprint_rep, "findings") else None
                if findings_iterable:
                    for f in findings_iterable:
                        src = getattr(f, "source_type", None) or "unknown"
                        source_yield_val[src] = source_yield_val.get(src, 0) + 1
            except (AttributeError, TypeError, RuntimeError):
                pass
        # Fallback: accepted_count from store if sprint_report not populated yet
        if store is not None and hasattr(store, "get_dedup_runtime_status"):
            try:
                dedup = store.get_dedup_runtime_status()
                # Use store accepted_count only if sprint_report gave no source_yield
                if not source_yield_val:
                    accepted_val = dedup.get("accepted_count", 0)
            except (AttributeError, RuntimeError):
                pass
        return accepted_val, ioc_val, source_yield_val

    def _sync_get_arrow_metrics() -> dict:
        """Thread-pool worker: fetch arrow metrics from store attribute or module func."""
        arrow_val: dict = {}
        if store is not None and hasattr(store, "_arrow_metrics"):
            try:
                arrow_val = store._arrow_metrics  # type: ignore[union-attr]
            except (AttributeError, TypeError):
                pass
        elif store is not None and hasattr(store, "get_arrow_metrics"):
            try:
                from hledac.universal.knowledge.duckdb_store import get_arrow_metrics
                arrow_val = get_arrow_metrics()
            except (ImportError, AttributeError):
                pass
        return arrow_val

    def _sync_get_cb_states() -> dict[str, str]:
        """Thread-pool worker: fetch circuit breaker states (thread-safe via Lock)."""
        cb_states: dict[str, str] = {}
        try:
            from transport.circuit_breaker import get_all_breaker_states
            cb_states = get_all_breaker_states()
        except (ImportError, AttributeError):
            pass
        return cb_states

    def _sync_get_peak_rss() -> float:
        """Thread-pool worker: compute peak RSS (sync resource call)."""
        rss_bytes = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
        # macOS: ru_maxrss is in bytes (not KB like on Linux)
        return round(rss_bytes / 1024 / 1024, 1)

    def _sync_get_ghost_entities() -> list:
        """Thread-pool worker: fetch top entities for ghost_global from DuckDB."""
        entities: list = []
        if store is not None and hasattr(store, "get_top_entities_for_ghost_global"):
            try:
                entities = store.get_top_entities_for_ghost_global(n=100)
            except (AttributeError, RuntimeError, OSError):
                pass
        return entities

    # Issue #36: asyncio.gather with return_exceptions=True for parallel I/O collection.
    # Each _sync_* wrapped in asyncio.to_thread() (thread pool, not blocking event loop).
    gather_results = await asyncio.gather(
        asyncio.to_thread(lambda: _sync_get_dedup(sprint_report)),
        asyncio.to_thread(_sync_get_arrow_metrics),
        asyncio.to_thread(_sync_get_cb_states),
        asyncio.to_thread(_sync_get_peak_rss),
        asyncio.to_thread(_sync_get_ghost_entities),
        return_exceptions=True,
    )
    _check_gathered(list(gather_results), None, "scorecard_phase1")

    # Unpack results with fallback defaults
    dedup_result, arrow_metrics_result, cb_open_domains, peak_rss_mb, ghost_entities = gather_results

    if isinstance(dedup_result, Exception):
        accepted, ioc_nodes, source_yield = 0, 0, {}
    elif isinstance(dedup_result, tuple) and len(dedup_result) == 3:
        accepted, ioc_nodes, source_yield = dedup_result
    else:
        accepted, ioc_nodes, source_yield = 0, 0, {}

    if isinstance(arrow_metrics_result, Exception):
        arrow_metrics_result = {}
    if isinstance(cb_open_domains, Exception):
        cb_open_domains = []
    if isinstance(peak_rss_mb, Exception):
        peak_rss_mb = 0.0
    if isinstance(ghost_entities, Exception):
        ghost_entities = []

    outlines_used = False
    semantic_novelty = 1.0  # fallback when SemanticStore unavailable

    # Calculate derived metrics
    findings_per_minute = accepted / max(1, elapsed / 60.0) if elapsed > 0 else 0.0
    ioc_density = ioc_nodes / max(1, accepted) if accepted > 0 else 0.0

    scorecard_data = {
        "sprint_id": sprint_id,
        "ts": ts,
        "findings_per_minute": round(findings_per_minute, 3),
        "ioc_density": round(ioc_density, 3),
        "semantic_novelty": semantic_novelty,
        "source_yield_json": _json_compact(source_yield or {}),
        "phase_timings_json": _json_compact(phase_timings),
        "outlines_used": outlines_used,
        "accepted_findings": accepted,
        "ioc_nodes": ioc_nodes,
        "synthesis_engine": "unknown",
        # Sprint 8VD §F: Extended scorecard
        "accepted_findings_count": accepted,
        "synthesis_engine_used": "unknown",
        "phase_duration_seconds": phase_timings,
        "cb_open_domains": cb_open_domains or [],
        # Sprint F265C: Arrow ingest telemetry — surfaces _ARROW_METRICS in scorecard
        "arrow_metrics": arrow_metrics_result or {},
        "peak_rss_mb": peak_rss_mb,
    }

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

    # Phase 2: Print structured report (depends on Phase 1 completion)
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

    # Phase 3: Parallel DuckDB writes via asyncio.gather
    # ISSUE #36: upsert_scorecard + upsert_episode run concurrently.
    # ISSUE #36 helpers — fail-safe async wrappers with try/except per duckdb contract.
    async def _safe_upsert_scorecard(
        _store: Any, _data: dict, _sid: str
    ) -> None:
        """Fail-safe upsert_scorecard: logs warning on error, never raises."""
        try:
            await _store.upsert_scorecard(_data)
        except (RuntimeError, OSError) as e:
            logger.warning("scorecard_persist_failed", error=str(e))

    async def _safe_upsert_episode(
        _store: Any, _sid: str, _target: str, _sprint_report: Any, _scorecard: dict, _elapsed: float
    ) -> None:
        """Fail-safe upsert_episode: logs warning on error, never raises."""
        import time as _t
        try:
            _top_findings_list: list[str] = []
            if _sprint_report is not None and hasattr(_sprint_report, "findings"):
                _top_findings_list = [
                    f.content if hasattr(f, "content") else str(f)
                    for f in (_sprint_report.findings or [])[:5]
                ]
            await _store.upsert_episode({
                "sprint_id": _sid,
                "query": _target,
                "summary": (
                    _sprint_report.threat_summary
                    if _sprint_report and hasattr(_sprint_report, "threat_summary")
                    else ""
                ),
                "top_findings": _top_findings_list,
                "ioc_clusters": [],
                "source_yield": _scorecard.get("source_yield_json", "{}"),
                "synthesis_engine": _scorecard.get("synthesis_engine", "unknown"),
                "duration_s": _elapsed,
                "ts": _t.time(),
            })
            logger.info("research_episode_saved", sprint_id=_sid)
        except (RuntimeError, OSError) as e:
            logger.warning("scorecard_persist_episode_failed", error=str(e))

    # Sprint 8TC B.4: Markdown report export (sequential — needs complete scorecard_data)
    # NOTE: DuckDB writes are created AFTER this succeeds — if export fails,
    # duckdb_write_tasks stays [] and no writes are attempted (fail-safe).
    md_path = _export_markdown_report(sprint_report, scorecard_data, sprint_id)
    print(f"Report saved: {md_path}")

    # Phase 3/4: DuckDB write task creation (only after successful export)
    # ISSUE #36: upsert_scorecard + upsert_episode run concurrently in Phase 4.
    # ISSUE #36 helpers — fail-safe async wrappers with try/except per duckdb contract.
    duckdb_write_tasks: list[asyncio.Task] = []
    store_has_upsert_scorecard = store is not None and hasattr(store, "upsert_scorecard")
    store_has_upsert_episode = store is not None and hasattr(store, "upsert_episode")

    if store_has_upsert_scorecard:
        duckdb_write_tasks.append(
            asyncio.create_task(_safe_upsert_scorecard(store, scorecard_data, sprint_id))
        )
    if store_has_upsert_episode:
        duckdb_write_tasks.append(
            asyncio.create_task(_safe_upsert_episode(store, sprint_id, target, sprint_report, scorecard_data, elapsed))
        )

    # Phase 4: Ghost global entities + wait for DuckDB writes in parallel
    # ISSUE #36: ghost_global and DuckDB writes can overlap.
    # ISSUE #36-FIX: bounded_gather with timeout prevents indefinite hang on duckdb stalls.
    async def _ghost_global_and_wait() -> None:
        """Upsert ghost entities in thread pool; await parallel DuckDB writes with timeout."""
        if (
            store is not None
            and hasattr(store, "get_top_entities_for_ghost_global")
            and hasattr(store, "upsert_global_entities")
            and ghost_entities
        ):
            try:
                n_upserted = await store.upsert_global_entities(ghost_entities)
                logger.info("ghost_global_entities_upserted", count=n_upserted)
            except (AttributeError, RuntimeError, OSError):
                pass
        # Await all parallel DuckDB writes with bounded timeout (fail-safe)
        if duckdb_write_tasks:  # skipped only when both upsert_scorecard + upsert_episode are absent
            from utils.async_utils import bounded_gather
            # bounded_gather(*coros) returns list[T], return_exceptions makes T include Exception
            results = await bounded_gather(*duckdb_write_tasks, return_exceptions=True)
            _check_gathered(results, None, "scorecard_phase3_duckdb")

    await _ghost_global_and_wait()

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
    logger.critical("_MAIN_FATAL [exit=%d]: %s\n%s", code, exc, traceback.format_exc())
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
    _boot_record_sync("boot_guard_sync", "starting")
    try:
        removed, reason = _run_boot_guard()
        logger.info("boot_guard_result", removed=removed, reason=reason)
        _boot_record_sync("boot_guard_sync", "ok", removed=removed, reason=reason)
    except BootGuardError as e:
        logger.error("boot_guard_unsafe_state", error=str(e))
        _boot_record_sync("boot_guard_sync", "unsafe_abort", error=str(e))
        sys.exit(1)
    except OSError as e:
        logger.warning("boot_guard_error", error=str(e))
        _boot_record_sync("boot_guard_sync", "error_soft", error=str(e))

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


def _jit_ensure() -> None:
    """
    PEP 744 Tier 2 JIT auto-enable for Python 3.14+.

    Tier 2 JIT (python -X jit) is opt-in per-interpreter invocation.
    This function detects whether:
      - The interpreter supports JIT (sys.jit attribute exists)
      - JIT is NOT yet active (sys.jit is None/falsy)
    When both conditions hold, self-restarts with -X jit appended to sys.argv.

    This is a ONE-TIME cost at process startup (~1-2ms overhead).
    JIT warm-up is async and has zero overhead if no hot loops are hit.

    Safe: nested invocations (jit already active) are a no-op.
    """
    import os
    import sys

    # PEP 744: sys.jit exists when Python was compiled with JIT support.
    # It is None when -X jit was not passed; truthy when JIT is active.
    if not hasattr(sys, "jit"):
        return  # Older Python — no JIT support available

    if sys.jit:  # Already active (truthy = Tier 2 JIT compiled and active)
        return

    # JIT available but not yet active — self-restart with -X jit
    executable = sys.executable
    new_argv = [executable, "-X", "jit"] + sys.argv
    os.execv(executable, new_argv)


if __name__ == "__main__":
    _jit_ensure()
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
