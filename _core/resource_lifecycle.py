"""
from _core import aclose
core/resource_lifecycle.py — Centralized Resource Lifecycle Manager

R1 Solution: Single authority for all sprint resource creation and teardown.

PROBLEMS SOLVED:
  1. No single authority knows when all executors, pools, sessions, and
     singletons are truly dead.
  2. Each module creates its own pool/executor and registers its own atexit.
  3. __del__ on C-backed objects is non-deterministic — resources leak on GC.
  4. Winddown orchestrator must manually list every teardown step.

SOLUTION:
  ResourceLifecycleManager as THE single context manager for the entire sprint:
    - Registers ThreadPoolExecutor, ProcessPoolExecutor, asyncio.Semaphore,
      httpx.AsyncClient, DuckDB connections, Rust pool handles
    - At sprint end (or on signal) executes deterministic shutdown in
      FIXED order: inference → fetch → storage → compute
    - Modules get factory methods: get_executor(name) / get_semaphore(name)
      instead of owning their own global instances
    - weakref.finalize replaces __del__ for C-resource objects
    - weakref.WeakSet tracks open resources for leak detection

CUTTING-EDGE (Python 3.14+):
  - contextlib.AsyncExitStack for async resource lifecycle (PEP 711)
  - weakref.WeakSet for GC-safe resource tracking
  - ExceptionGroup for aggregating shutdown errors
  - match/case for resource routing
  - asyncio.TaskGroup for parallel shutdown within layers
  - PEP 789: asyncio.Semaphore created INSIDE event loop context only

M1 8GB UMA CONSTRAINTS:
  - Total thread cap: 24 (soft, warn on exceed)
  - DuckDB: 4 RO + 2 RW connections max
  - httpx sessions: LRU-bounded, max 8 concurrent
  - Metal cache: clear on inference layer shutdown
  - Memory threshold: block new allocations at 5.5 GiB RSS

ARCHITECTURE:
  ┌─────────────────────────────────────────────────────────────┐
  │               ResourceLifecycleManager                       │
  │  (single context manager per sprint)                        │
  ├─────────────────────────────────────────────────────────────┤
  │  Layer 0: INFERENCE  — MLX, CoreML, ANE, Hermes engine     │
  │  Layer 1: FETCH      — httpx sessions, curl_cffi, Tor/I2P  │
  │  Layer 2: STORAGE    — DuckDB, LMDB, LanceDB connections    │
  │  Layer 3: COMPUTE    — ThreadPoolExecutors, ProcessPools    │
  ├─────────────────────────────────────────────────────────────┤
  │  Shutdown: LAYER 0 → 1 → 2 → 3 (deterministic, timed)     │
  │  Signal handlers: SIGINT/SIGTERM → graceful shutdown        │
  │  Leak detection: WeakSet[Any] for unclosed resources        │
  └─────────────────────────────────────────────────────────────┘

USAGE:
  # In sprint_entrypoint.py or SprintSchedulerV2:
  rlm = ResourceLifecycleManager()
  async with rlm:
      # Modules use factory methods:
      executor = rlm.get_executor("duckdb")
      sem = rlm.get_semaphore("http_lane")
      session = await rlm.register_session(httpx.AsyncClient(), "tor")

  # After __aexit__: ALL resources deterministically shut down.
  # Order: inference → fetch → storage → compute

Sprint R1 (2026-07-18)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
import time
import weakref
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import AsyncExitStack, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Final
from collections.abc import Callable

if TYPE_CHECKING:

# MODERN-36 Fix: Import UmaBudget at module level for SSOT constant derivation
from hledac.universal.utils.uma_budget import UmaBudget
from _core._util import aclose

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Constants — M1 8GB UMA bounds
# ═══════════════════════════════════════════════════════════════════════════════

_TOTAL_THREAD_CAP: Final[int] = int(os.environ.get("HLEDAC_TOTAL_THREAD_CAP", "24"))
_TOTAL_PROCESS_CAP: Final[int] = int(os.environ.get("HLEDAC_TOTAL_PROCESS_CAP", "2"))
# MODERN-36 Fix: Derive fallback from UmaBudget SSOT (was literal "5.5")
_RSS_BLOCK_GIB: Final[float] = float(os.environ.get(
    "HLEDAC_RSS_BLOCK_GIB",
    str(UmaBudget.MISSION_PEAK_RSS_GIB)  # 5.5 GiB from SSOT
))
_SHUTDOWN_TIMEOUT_PER_LAYER_S: Final[float] = float(
    os.environ.get("HLEDAC_SHUTDOWN_TIMEOUT_LAYER_S", "10.0")
    )
_SHUTDOWN_TIMEOUT_PER_RESOURCE_S: Final[float] = float(
    os.environ.get("HLEDAC_SHUTDOWN_TIMEOUT_RESOURCE_S", "3.0")
    )

# Per-domain default worker counts (M1 8GB friendly)
_DEFAULT_EXECUTOR_WORKERS: Final[dict[str, int]] = {
    "duckdb": 2,
    "html": 8,
    "embed": 2,
    "infer": 2,
    "crypto": 2,
    "semantic": 2,
    "content": 3,
    "metadata": 2,
    "dns": 2,
    "parallel": 3,
    "nlp": 2,
    "vision": 2,
    "storage": 2,
    "default": 2,
}

# ═══════════════════════════════════════════════════════════════════════════════
# Type definitions
# ═══════════════════════════════════════════════════════════════════════════════

class ShutdownLayer(Enum):
    """Deterministic shutdown order — layers shut down in declaration order."""

    INFERENCE = (0, "inference")  # MLX, CoreML, ANE, Hermes
    FETCH = (1, "fetch")  # httpx, curl_cffi, Tor, I2P
    STORAGE = (2, "storage")  # DuckDB, LMDB, LanceDB
    COMPUTE = (3, "compute")  # ThreadPools, ProcessPools

    def __new__(cls, order: int, label: str):
        obj = object.__new__(cls)
        obj._value_ = order
        obj._order = order
        obj._label = label
        return obj

    @property
    def order(self) -> int:
        return self._order

    @property
    def label(self) -> str:
        return self._label

class ResourceState(Enum):
    REGISTERED = auto()  # Registered but not yet acquired
    ACTIVE = auto()  # In use
    CLOSING = auto()  # Shutdown in progress
    CLOSED = auto()  # Successfully shut down
    ERROR = auto()  # Shutdown failed

@dataclass(slots=True)
class ResourceHandle:
    """Metadata for a single registered resource."""

    name: str
    layer: ShutdownLayer
    kind: str  # "executor", "session", "semaphore", "connection", "rust_pool"
    state: ResourceState = ResourceState.REGISTERED
    registered_at: float = field(default_factory=time.monotonic)
    closed_at: float | None = None
    error: str | None = None

    def mark_closing(self) -> None:
        self.state = ResourceState.CLOSING

    def mark_closed(self) -> None:
        self.state = ResourceState.CLOSED
        self.closed_at = time.monotonic()

    def mark_error(self, error: str) -> None:
        self.state = ResourceState.ERROR
        self.error = error
        self.closed_at = time.monotonic()

# ═══════════════════════════════════════════════════════════════════════════════
# Leak sentinel — detects unregistered resources holding C memory
# ═══════════════════════════════════════════════════════════════════════════════

_LEAK_SENTINEL: weakref.WeakSet[Any] = weakref.WeakSet()
"""Global WeakSet tracking objects that SHOULD have been registered but weren't.

When a ResourceLifecycleManager is active (via ContextVar), any DuckDB connection,
httpx session, or executor created outside the manager's factory methods is added
here for post-mortem leak detection.
"""

_current_rlm: ContextVar[ResourceLifecycleManager | None] = ContextVar(
    "current_rlm", default=None
    )

def get_current_rlm() -> ResourceLifecycleManager | None:
    """Get the currently active ResourceLifecycleManager, if any."""
    return _current_rlm.get()

# ═══════════════════════════════════════════════════════════════════════════════
# ResourceLifecycleManager
# ═══════════════════════════════════════════════════════════════════════════════

class ResourceLifecycleManager:
    """Centralized lifecycle manager for all sprint resources.

    THE single context manager for the entire sprint. All modules MUST use this
    instead of creating their own executors, sessions, or semaphores.

    Registration:
        rlm = ResourceLifecycleManager()
        executor = rlm.get_executor("duckdb")
        semaphore = rlm.get_semaphore("http_lane", limit=8)
        session = await rlm.register_session(client, name="tor_session")

    Shutdown (deterministic):
        Layer 0: INFERENCE  — MLX Metal cache, CoreML unload, ANE context
        Layer 1: FETCH      — httpx.AsyncClient.aclose(), curl_cffi sessions
        Layer 2: STORAGE    — DuckDB conn.close(), LMDB env.close()
        Layer 3: COMPUTE    — ThreadPoolExecutor.shutdown(), ProcessPool shutdown
    """

    __slots__ = (
        "_lock",
        "_exit_stack",
        "_resources",
        "_executors",
        "_semaphores",
        "_sessions",
        "_rust_handles",
        "_duckdb_connections",
        "_finalizers",
        "_total_workers",
        "_rss_block_triggered",
        "_shutting_down",
        "_signal_handlers",
        "_stats",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._exit_stack: AsyncExitStack | None = None
        self._resources: dict[str, ResourceHandle] = {}
        self._executors: dict[str, ThreadPoolExecutor] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._sessions: dict[str, Any] = {}  # httpx.AsyncClient instances
        self._rust_handles: list[Any] = []  # Rust pool JoinHandle wrappers
        self._duckdb_connections: list[Any] = []  # raw duckdb connections
        self._finalizers: list[weakref.finalize] = []
        self._total_workers: int = 0
        self._rss_block_triggered: bool = False
        self._shutting_down: bool = False
        self._signal_handlers: dict[int, Any] = {}
        self._stats: dict[str, int] = {
            "executors_created": 0,
            "semaphores_created": 0,
            "sessions_registered": 0,
            "connections_registered": 0,
            "finalizers_registered": 0,
            "shutdown_errors": 0,
        }

    # ── Context Manager Protocol ───────────────────────────────────────────

    async def __aenter__(self) -> ResourceLifecycleManager:
        """Enter sprint context — install signal handlers, set ContextVar."""
        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()
        self._install_signal_handlers()
        _current_rlm.set(self)
        logger.debug("[RLM] Sprint context entered — signal handlers installed")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        """Exit sprint context — deterministic shutdown with special-case hooks.

        Order: INFERENCE (MLX cache) → FETCH (httpx) → STORAGE (DuckDB) → COMPUTE (pools)
        """
        self._shutting_down = True
        _current_rlm.set(None)
        self._uninstall_signal_handlers()

        errors: list[Exception] = []

        # Layer 0: INFERENCE — MLX Metal cache, CoreML unload, ANE context
        try:
            await self._shutdown_inference_layer()
            await self._shutdown_layer(ShutdownLayer.INFERENCE)
        except ExceptionGroup as eg:
            errors.extend(eg.exceptions)
        except Exception as e:
            errors.append(e)
            logger.error("[RLM] INFERENCE layer shutdown error: %s", e)

        # Layer 1: FETCH — httpx.AsyncClient.aclose(), curl_cffi sessions
        try:
            await self._shutdown_layer(ShutdownLayer.FETCH)
        except ExceptionGroup as eg:
            errors.extend(eg.exceptions)
        except Exception as e:
            errors.append(e)
            logger.error("[RLM] FETCH layer shutdown error: %s", e)

        # Layer 2: STORAGE — DuckDB connections, LMDB envs, LanceDB
        try:
            await self._shutdown_storage_connections()
            await self._shutdown_layer(ShutdownLayer.STORAGE)
        except ExceptionGroup as eg:
            errors.extend(eg.exceptions)
        except Exception as e:
            errors.append(e)
            logger.error("[RLM] STORAGE layer shutdown error: %s", e)

        # Layer 3: COMPUTE — ThreadPoolExecutors, Rust handles, ProcessPools
        try:
            await self._shutdown_rust_handles()
            await self._shutdown_layer(ShutdownLayer.COMPUTE)
        except ExceptionGroup as eg:
            errors.extend(eg.exceptions)
        except Exception as e:
            errors.append(e)
            logger.error("[RLM] COMPUTE layer shutdown error: %s", e)

        # Run weakref.finalize callbacks (C resource cleanup)
        await self._run_finalizers()

        # Close the exit stack itself
        if self._exit_stack is not None:
            try:
                await self._exit_stack.__aexit__(None, None, None)
            except Exception as e:
                errors.append(e)
            self._exit_stack = None

        self._log_shutdown_summary(errors)

        # Re-raise if there were errors (using ExceptionGroup for Python 3.14+)
        if errors and exc_val is None:
            raise ExceptionGroup(
                f"[RLM] {len(errors)} shutdown error(s)",
                errors,
    )

        # Don't suppress original exception if one was propagating
        return False

    # ── Registration API ───────────────────────────────────────────────────

    def get_executor(
        self,
        name: str,
        max_workers: int | None = None,
        *,
        thread_name_prefix: str | None = None,
    ) -> ThreadPoolExecutor:
        """Get or create a named ThreadPoolExecutor.

        Args:
            name: Unique name (e.g., "duckdb", "html", "embed").
            max_workers: Override default worker count. Falls back to
                         _DEFAULT_EXECUTOR_WORKERS[name] or 2.
            thread_name_prefix: Thread name prefix (default: f"hledac-{name}").

        Returns:
            Shared ThreadPoolExecutor — same instance for same name.

        Raises:
            RuntimeError: If RSS exceeds RSS_BLOCK_GIB and new allocations blocked.
        """
        if name in self._executors:
            return self._executors[name]

        with self._lock:
            if name in self._executors:
                return self._executors[name]

            self._check_rss_block()

            workers = max_workers or _DEFAULT_EXECUTOR_WORKERS.get(
                name, _DEFAULT_EXECUTOR_WORKERS["default"]
    )
            workers = self._clamp_workers(workers)

            prefix = thread_name_prefix or f"hledac-{name}"
            executor = ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix=prefix,
    )
            self._executors[name] = executor
            self._total_workers += workers
            self._stats["executors_created"] += 1

            self._register_resource(
                name=name,
                layer=ShutdownLayer.COMPUTE,
                kind="executor",
    )

            logger.debug(
                "[RLM] Created executor '%s' with %d workers (total=%d)",
                name,
                workers,
                self._total_workers,
    )
            return executor

    def get_process_pool(
        self,
        name: str,
        max_workers: int | None = None,
    ) -> ProcessPoolExecutor:
        """Get or create a named ProcessPoolExecutor.

        M1 8GB: ProcessPoolExecutor is expensive (~50-100 MB per worker).
        Use sparingly — prefer ThreadPoolExecutor for GIL-releasing C extensions.

        Args:
            name: Unique name.
            max_workers: Default 2 on M1 8GB, clamped to _TOTAL_PROCESS_CAP.

        Returns:
            Shared ProcessPoolExecutor.
        """
        if name in self._executors:
            existing = self._executors[name]
            if isinstance(existing, ProcessPoolExecutor):
                return existing

        with self._lock:
            if name in self._executors:
                existing = self._executors[name]
                if isinstance(existing, ProcessPoolExecutor):
                    return existing

            self._check_rss_block()

            workers = max_workers or 2
            workers = min(workers, _TOTAL_PROCESS_CAP)

            pool = ProcessPoolExecutor(
                max_workers=workers,
    )
            self._executors[name] = pool  # type: ignore[assignment]
            self._stats["executors_created"] += 1

            self._register_resource(
                name=name,
                layer=ShutdownLayer.COMPUTE,
                kind="process_pool",
    )

            logger.debug(
                "[RLM] Created process pool '%s' with %d workers",
                name,
                workers,
    )
            return pool

    def get_semaphore(
        self,
        name: str,
        limit: int,
    ) -> asyncio.Semaphore:
        """Get or create a named asyncio.Semaphore.

        PEP 789 (Python 3.14+): Semaphore MUST be created inside the event loop
        context. Call this from an async function.

        Args:
            name: Unique name (e.g., "http_lane", "bgp_query").
            limit: Concurrency limit.

        Returns:
            Shared asyncio.Semaphore.
        """
        if name in self._semaphores:
            return self._semaphores[name]

        with self._lock:
            if name in self._semaphores:
                return self._semaphores[name]

            sem = asyncio.Semaphore(limit)
            self._semaphores[name] = sem
            self._stats["semaphores_created"] += 1

            self._register_resource(
                name=name,
                layer=ShutdownLayer.FETCH,
                kind="semaphore",
    )

            logger.debug("[RLM] Created semaphore '%s' with limit=%d", name, limit)
            return sem

    async def register_session(
        self,
        session: Any,
        name: str,
        *,
        layer: ShutdownLayer = ShutdownLayer.FETCH,
    ) -> Any:
        """Register an httpx.AsyncClient (or compatible) for managed lifecycle.

        The session WILL be aclose()'d during shutdown. Do NOT aclose() it
        manually after registration.

        Args:
            session: httpx.AsyncClient or compatible async context manager.
            name: Unique name.
            layer: Which shutdown layer (default: FETCH).

        Returns:
            The session (for chaining).
        """
        if name in self._sessions:
            logger.warning(
                "[RLM] Session '%s' already registered — returning existing", name
    )
            return self._sessions[name]

        with self._lock:
            if name in self._sessions:
                return self._sessions[name]

            self._sessions[name] = session
            self._stats["sessions_registered"] += 1

            self._register_resource(
                name=name,
                layer=layer,
                kind="session",
    )

            logger.debug("[RLM] Registered session '%s'", name)
            return session

    def register_duckdb_connection(
        self,
        conn: Any,
        name: str,
    ) -> Any:
        """Register a DuckDB connection for managed lifecycle.

        The connection WILL be close()'d during shutdown.

        Args:
            conn: duckdb.DuckDBPyConnection.
            name: Unique name.

        Returns:
            The connection (for chaining).
        """
        with self._lock:
            self._duckdb_connections.append(conn)
            self._stats["connections_registered"] += 1

            self._register_resource(
                name=name,
                layer=ShutdownLayer.STORAGE,
                kind="duckdb_connection",
    )

            logger.debug("[RLM] Registered DuckDB connection '%s'", name)
            return conn

    def register_rust_handle(self, handle: Any, name: str) -> Any:
        """Register a Rust pool JoinHandle for managed lifecycle.

        Args:
            handle: Rust JoinHandle wrapper (from worker_pool.RustWorkerPool).
            name: Unique name.

        Returns:
            The handle (for chaining).
        """
        with self._lock:
            self._rust_handles.append(handle)
            self._register_resource(
                name=name,
                layer=ShutdownLayer.COMPUTE,
                kind="rust_pool",
    )
            return handle

    def register_finalizer(
        self,
        obj: Any,
        cleanup_fn: Callable[[], None],
        name: str,
    ) -> weakref.finalize:
        """Register a weakref.finalize for C-resource cleanup.

        REPLACES __del__ for objects holding C resources (DuckDB, LMDB, MLX).
        weakref.finalize is guaranteed to run at interpreter exit, unlike __del__
        which is non-deterministic in Python 3.14+ (PEP 711 refcounting changes).

        Args:
            obj: The object to track.
            cleanup_fn: Called when obj is garbage collected or at shutdown.
            name: Human-readable name for logging.

        Returns:
            weakref.finalize instance (can call .detach() to cancel).
        """
        fin = weakref.finalize(obj, self._finalizer_callback, cleanup_fn, name)
        with self._lock:
            self._finalizers.append(fin)
            self._stats["finalizers_registered"] += 1
        logger.debug("[RLM] Registered finalizer '%s'", name)
        return fin

    def _finalizer_callback(
        self, cleanup_fn: Callable[[], None], name: str
    ) -> None:
        """Wrapper for finalizer callbacks — logs and prevents double-cleanup."""
        if self._shutting_down:
            return  # Already handled by layer shutdown
        try:
            logger.debug("[RLM] Running finalizer '%s'", name)
            cleanup_fn()
        except Exception as e:
            logger.error("[RLM] Finalizer '%s' error: %s", name, e)

    # ── Shutdown Internals ─────────────────────────────────────────────────

    async def _shutdown_layer(self, layer: ShutdownLayer) -> None:
        """Shutdown all resources in a layer with timeout per resource."""
        resources = [
            (name, handle)
            for name, handle in self._resources.items()
            if handle.layer == layer and handle.state == ResourceState.REGISTERED
        ]

        if not resources:
            return

        logger.info(
            "[RLM] Shutting down layer %s (%d resources)...",
            layer.label,
            len(resources),
    )

        errors: list[Exception] = []

        for name, handle in resources:
            handle.mark_closing()
            try:
                async with asyncio.timeout(_SHUTDOWN_TIMEOUT_PER_RESOURCE_S):
                    await self._shutdown_resource(name, handle)
                handle.mark_closed()
                logger.debug("[RLM] ✓ %s (%s) — closed", name, handle.kind)
            except asyncio.TimeoutError:
                err_msg = f"Timeout shutting down {name} ({handle.kind})"
                handle.mark_error(err_msg)
                errors.append(TimeoutError(err_msg))
                logger.warning("[RLM] ⚠ %s", err_msg)
            except Exception as e:
                err_msg = f"Error shutting down {name} ({handle.kind}): {e}"
                handle.mark_error(err_msg)
                errors.append(e)
                logger.error("[RLM] ✗ %s", err_msg)

        if errors:
            self._stats["shutdown_errors"] += len(errors)
            raise ExceptionGroup(
                f"[RLM] Layer {layer.label} shutdown: {len(errors)} error(s)",
                errors,
    )

    async def _shutdown_resource(self, name: str, handle: ResourceHandle) -> None:
        """Shutdown a single resource by kind."""
        match handle.kind:
            case "executor":
                executor = self._executors.pop(name, None)
                if executor is not None:
                    # ThreadPoolExecutor.shutdown with graceful wait
                    await asyncio.to_thread(executor.shutdown, wait=True)
            case "process_pool":
                pool = self._executors.pop(name, None)
                if pool is not None:
                    await asyncio.to_thread(pool.shutdown, wait=True)
            case "session":
                session = self._sessions.pop(name, None)
                if session is not None and hasattr(session, "aclose"):
                    await session.aclose()
            case "semaphore":
                # Semaphores don't need explicit close — just remove from registry
                self._semaphores.pop(name, None)
            case "duckdb_connection":
                # Handled in bulk by _shutdown_duckdb_connections
                pass
            case "rust_pool":
                # Handled in bulk by _shutdown_rust_handles
                pass
            case _:
                logger.debug("[RLM] Unknown resource kind '%s' for '%s'", handle.kind, name)

    async def _shutdown_inference_layer(self) -> None:
        """Shutdown MLX/CoreML/ANE — clear Metal cache, unload models."""
        try:
            import mlx.core as mx

            # Evaluate pending operations before clearing cache
            mx.eval([])
            mx.metal.clear_cache()
            logger.debug("[RLM] MLX Metal cache cleared")
        except ImportError:  # noqa: BLE001
            pass
        except Exception as e:
            logger.warning("[RLM] MLX cleanup error (non-fatal): %s", e)

    async def _shutdown_storage_connections(self) -> None:
        """Close all registered DuckDB connections."""
        for conn in self._duckdb_connections:
            try:
                await asyncio.to_thread(conn.close)
            except Exception as e:
                logger.warning("[RLM] DuckDB close error (non-fatal): %s", e)
        self._duckdb_connections.clear()

    async def _shutdown_rust_handles(self) -> None:
        """Shutdown all registered Rust pool handles."""
        for handle in self._rust_handles:
            try:
                if hasattr(handle, "shutdown"):
                    await asyncio.to_thread(handle.shutdown)
            except Exception as e:
                logger.warning("[RLM] Rust handle shutdown error (non-fatal): %s", e)
        self._rust_handles.clear()

    async def _run_finalizers(self) -> None:
        """Run all registered weakref.finalize callbacks."""
        for fin in self._finalizers:
            try:
                if fin.alive:
                    fin()
            except Exception as e:
                logger.warning("[RLM] Finalizer error (non-fatal): %s", e)
        self._finalizers.clear()

    def _install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers for graceful shutdown."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No event loop — nothing to install

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                old = signal.getsignal(sig)
                self._signal_handlers[sig] = old
                loop.add_signal_handler(sig, self._signal_handler, sig)
                logger.debug("[RLM] Installed signal handler for %s", sig.name)
            except (ValueError, OSError) as e:
                logger.debug(
                    "[RLM] Cannot install signal handler for %s: %s", sig.name, e
    )

    def _uninstall_signal_handlers(self) -> None:
        """Restore original signal handlers."""
        for sig, old_handler in self._signal_handlers.items():
            try:
                signal.signal(sig, old_handler)
            except Exception:  # noqa: BLE001
                pass
        self._signal_handlers.clear()

    def _signal_handler(self, signum: int) -> None:
        """Handle SIGINT/SIGTERM — set shutting_down flag for graceful exit."""
        sig_name = signal.Signals(signum).name
        logger.warning("[RLM] Received %s — initiating graceful shutdown", sig_name)
        self._shutting_down = True

    # ── Memory Pressure ────────────────────────────────────────────────────

    def _check_rss_block(self) -> None:
        """Check if RSS exceeds block threshold and reject new allocations.

        On M1 8GB, when RSS exceeds 5.5 GiB, we block new executor/process
        pool creation to prevent the OOM killer.
        """
        if self._rss_block_triggered:
            raise RuntimeError(
                f"[RLM] RSS block active — RSS exceeds {_RSS_BLOCK_GIB} GiB. "
                "No new executors/pools can be created."
    )

        try:
            import psutil

            proc = psutil.Process()
            mem = proc.memory_info()
            rss_gib = mem.rss / (1024**3)
            if rss_gib > _RSS_BLOCK_GIB:
                self._rss_block_triggered = True
                raise RuntimeError(
                    f"[RLM] RSS ({rss_gib:.1f} GiB) exceeds block threshold "
                    f"({_RSS_BLOCK_GIB} GiB). Blocking new allocations."
    )
        except ImportError:  # noqa: BLE001
            pass  # psutil not available — skip check
        except RuntimeError:
            raise
        except Exception:  # noqa: BLE001
            pass

    def is_memory_pressured(self) -> bool:
        """Check if system is under memory pressure."""
        try:
            import psutil

            mem = psutil.virtual_memory()
            return mem.percent > 85
        except ImportError:
            return False

    # ── Helpers ────────────────────────────────────────────────────────────

    def _clamp_workers(self, workers: int) -> int:
        """Clamp worker count to total thread cap."""
        if self._total_workers + workers > _TOTAL_THREAD_CAP:
            clamped = max(1, _TOTAL_THREAD_CAP - self._total_workers)
            logger.warning(
                "[RLM] Thread cap reached (%d/%d) — clamping workers from %d to %d. "
                "Set HLEDAC_TOTAL_THREAD_CAP env var to increase.",
                self._total_workers,
                _TOTAL_THREAD_CAP,
                workers,
                clamped,
    )
            return clamped
        return workers

    def _register_resource(
        self, name: str, layer: ShutdownLayer, kind: str
    ) -> ResourceHandle:
        """Create and store a ResourceHandle."""
        handle = ResourceHandle(name=name, layer=layer, kind=kind)
        self._resources[name] = handle
        return handle

    def _log_shutdown_summary(self, errors: list[Exception]) -> None:
        """Log a summary of the shutdown."""
        closed_count = sum(
            1
            for h in self._resources.values()
            if h.state == ResourceState.CLOSED
    )
        error_count = sum(
            1
            for h in self._resources.values()
            if h.state == ResourceState.ERROR
    )
        remaining = sum(
            1
            for h in self._resources.values()
            if h.state in (ResourceState.REGISTERED, ResourceState.ACTIVE, ResourceState.CLOSING)
    )

        if closed_count > 0 or error_count > 0:
            logger.info(
                "[RLM] Shutdown summary: %d closed, %d errors, %d remaining, "
                "%d unhandled errors",
                closed_count,
                error_count,
                remaining,
                len(errors),
    )

        if error_count > 0:
            for name, handle in self._resources.items():
                if handle.state == ResourceState.ERROR and handle.error:
                    logger.warning(
                        "[RLM] Resource '%s' (%s): %s",
                        name,
                        handle.kind,
                        handle.error,
    )

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def is_shutting_down(self) -> bool:
        """Check if shutdown has been initiated."""
        return self._shutting_down

    @property
    def stats(self) -> dict[str, int]:
        """Get registration statistics."""
        return dict(self._stats)

    def get_registered_resources(self) -> list[ResourceHandle]:
        """Get list of all registered resources (for telemetry)."""
        return list(self._resources.values())

    def get_executor_names(self) -> list[str]:
        """Get list of registered executor names."""
        return list(self._executors.keys())

    def get_session_names(self) -> list[str]:
        """Get list of registered session names."""
        return list(self._sessions.keys())

    def get_connection_count(self) -> int:
        """Get count of registered DuckDB connections."""
        return len(self._duckdb_connections)

    def get_total_workers(self) -> int:
        """Get total worker count across all executors."""
        return self._total_workers

    async def force_shutdown(self, timeout_s: float = 15.0) -> None:
        """Emergency force-shutdown — skips graceful close, just terminates.

        Use only when normal shutdown hangs. Sends shutdown(wait=False) to all
        executors and clears registries.
        """
        logger.warning("[RLM] Force shutdown initiated (timeout=%ss)", timeout_s)
        self._shutting_down = True

        # Force-close all sessions
        for name, session in list(self._sessions.items()):
            try:
                if hasattr(session, "aclose"):
                    async with asyncio.timeout(1.0):
                        await session.aclose()
            except Exception:  # noqa: BLE001
                pass
        self._sessions.clear()

        # Force-shutdown all executors (no wait)
        for name, executor in list(self._executors.items()):
            try:
                executor.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
        self._executors.clear()

        # Close DuckDB connections
        for conn in self._duckdb_connections:
            try:
                await asyncio.to_thread(conn.close)
            except Exception:  # noqa: BLE001
                pass
        self._duckdb_connections.clear()

        await self._run_finalizers()

        self._stats["shutdown_errors"] += 1
        logger.info("[RLM] Force shutdown complete")

# ═══════════════════════════════════════════════════════════════════════════════
# Module-level convenience — get the currently active RLM
# ═══════════════════════════════════════════════════════════════════════════════

def require_rlm() -> ResourceLifecycleManager:
    """Get the active RLM or raise RuntimeError.

    Use this in modules that MUST be called within a sprint context.
    """
    rlm = _current_rlm.get()
    if rlm is None:
        raise RuntimeError(
            "No active ResourceLifecycleManager — call within 'async with ResourceLifecycleManager():'"
    )
    return rlm

# ═══════════════════════════════════════════════════════════════════════════════
# Sentinel mixin — for objects that want automatic leak detection
# ═══════════════════════════════════════════════════════════════════════════════

class TrackedResource:
    """Mixin for objects that should be tracked by a ResourceLifecycleManager.

    When __init__ is called, if a ResourceLifecycleManager is active, the
    object is automatically added to the manager's WeakSet for leak detection.

    Usage:
        class MyDuckDBWrapper(TrackedResource):
            def __init__(self, db_path: str) -> None:
                super().__init__()
                self._conn = duckdb.connect(db_path)
    """

    __slots__ = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _original_init = cls.__init__

        def _tracked_init(self: Any, *args: Any, **kwargs: Any) -> None:
            _original_init(self, *args, **kwargs)
            try:
                _LEAK_SENTINEL.add(self)
                rlm = _current_rlm.get()
                if rlm is not None:
                    logger.debug(
                        "[RLM] Auto-tracked %s.%s",
                        type(self).__module__,
                        type(self).__qualname__,
    )
            except Exception:  # noqa: BLE001
                pass

        cls.__init__ = _tracked_init  # type: ignore[method-assign]

# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = [
    "ResourceLifecycleManager",
    "ShutdownLayer",
    "ResourceState",
    "ResourceHandle",
    "TrackedResource",
    "get_current_rlm",
    "require_rlm",
    "_LEAK_SENTINEL",
]
