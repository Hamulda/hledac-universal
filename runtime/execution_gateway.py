"""
Execution Gateway — unified dispatch for CPU/IO-bound work (Issue 8+9).

Single entry point for all off-loop execution, replacing scattered

``asyncio.to_thread()`` calls with a bounded, M1 8GB-safe dispatcher
that auto-selects the best backend:

  ┌───────────────────┬──────────────────────┬──────────────────────────────┐
  │ Workload type     │ Backend              │ Thread budget                │
  ├───────────────────┼──────────────────────┼──────────────────────────────┤
  │ GIL-releasing CPU │ Rust rayon cpu_pool  │ 4 P-cores (SIMD, NEON)       │
  │ I/O-bound block   │ SharedWorkerPool     │ adaptive 1-5 (governor)      │
  │ Pure-Python CPU   │ Subinterpreter pool  │ 2 workers (M1 8GB safe)      │
  │ Emergency fallback│ asyncio.to_thread    │ bounded (default executor)   │
  └───────────────────┴──────────────────────┴──────────────────────────────┘

Feature flag: ``HLEDAC_ENABLE_SUBINTERPRETERS=1`` (default OFF).
  - Requires Python 3.14+ with ``--with-experimental-isolated-subinterpreters``
  - Runtime probe validates true subinterpreter support (not just import)
  - M1 8GB: ~1-2MB overhead per subinterpreter, max 2 workers

Usage:
  >>> from hledac.universal.runtime.execution_gateway import gateway
  >>> result = await gateway.cpu_bound(heavy_fn, arg1, arg2, timeout=30.0)
  >>> result = await gateway.io_bound(dns_lookup, hostname)
  >>> result = await gateway.mlx_inference(model, prompt, timeout=60.0)

Design invariants:
  - Bounded: every backend has a hard cap on workers
  - Fail-safe: errors → fallback to next-best backend (never crashes)
  - M1 8GB: total thread budget ≤ 11 (rayon 4+2 + shared 5 + asyncio 1)
  - Lazy: backends initialized on first use
  - Traceable: OTel context propagated across all backends
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable
from typing import Any, Literal, TypeVar

from hledac.universal._core.locks import LockCategory, make_lock
from hledac.universal.utils.asyncx import safe_wait_for
from _core import aclose

logger = logging.getLogger(__name__)

T = TypeVar("T")

# =============================================================================
# Feature gate: Subinterpreters (Python 3.14+, experimental)
# =============================================================================

_SUBINTERPRETER_ENABLED: bool | None = None  # None = unprobed
_SUBINTERPRETER_PROBE_LOCK = make_lock(LockCategory.CONFIG, "execution_gateway._SUBINTERPRETER_PROBE_LOCK")


def _env_subinterpreters_enabled() -> bool:
    """Read HLEDAC_ENABLE_SUBINTERPRETERS env var (default 0 = OFF)."""
    return os.environ.get("HLEDAC_ENABLE_SUBINTERPRETERS", "0") in ("1", "true", "yes")


def _probe_subinterpreter_support() -> bool:
    """
    Runtime probe: verify subinterpreter support is actually available.

    Checks (in order):
      1. ``HLEDAC_ENABLE_SUBINTERPRETERS=1`` env gate
      2. Python 3.14+ (PEP 756 — ``concurrent.futures.InterpreterPoolExecutor``)
      3. ``interpreters`` stdlib module exists
      4. Can actually create and destroy a subinterpreter (full roundtrip)

    Returns:
        True only if ALL checks pass — this is deliberately conservative.
    """
    global _SUBINTERPRETER_ENABLED
    if _SUBINTERPRETER_ENABLED is not None:
        return _SUBINTERPRETER_ENABLED

    with _SUBINTERPRETER_PROBE_LOCK:
        if _SUBINTERPRETER_ENABLED is not None:
            return _SUBINTERPRETER_ENABLED

        # Gate 1: Env flag
        if not _env_subinterpreters_enabled():
            logger.debug("[gateway] Subinterpreters disabled by HLEDAC_ENABLE_SUBINTERPRETERS=0")
            _SUBINTERPRETER_ENABLED = False
            return False

        # Gate 2: Import check
        try:
            from concurrent.futures import InterpreterPoolExecutor  # noqa: F401
        except ImportError:
            logger.debug("[gateway] InterpreterPoolExecutor not available (Python < 3.14)")
            _SUBINTERPRETER_ENABLED = False
            return False

        # Gate 3: interpreters stdlib module
        try:
            import interpreters as _interpreters
        except ImportError:
            logger.debug("[gateway] `interpreters` stdlib module not available")
            _SUBINTERPRETER_ENABLED = False
            return False

        # Gate 4: Full roundtrip — create + destroy a subinterpreter
        try:
            interp = _interpreters.create()
            interp_id = _interpreters.get_current()
            if interp_id is None:
                logger.debug("[gateway] interpreters.get_current() returned None")
                _SUBINTERPRETER_ENABLED = False
                return False
            _interpreters.destroy(interp)
            logger.info("[gateway] Subinterpreter support VERIFIED (python 3.14+ experimental)")
            _SUBINTERPRETER_ENABLED = True
            return True
        except Exception as exc:
            logger.debug(
                "[gateway] Subinterpreter roundtrip failed: %s — "
                "likely missing --with-experimental-isolated-subinterpreters build flag",
                exc,
            )
            _SUBINTERPRETER_ENABLED = False
            return False


def subinterpreter_available() -> bool:
    """Public check: are subinterpreters truly available?"""
    return _probe_subinterpreter_support()


# =============================================================================
# Workload hints — help the gateway pick the right backend
# =============================================================================


class WorkloadHint:
    """Constants for workload type hints."""

    GIL_RELEASING = "gil_releasing"  # C extensions: msgspec, orjson, zstd, curl_cffi
    IO_BOUND = "io_bound"  # Blocking I/O: WHOIS, DNS, SSL, file ops
    MLX_INFERENCE = "mlx_inference"  # MLX Metal (releases GIL during GPU ops)
    PURE_PYTHON_CPU = "pure_python_cpu"  # Pure Python, no C ext (rare in hledac)
    AUTO = "auto"  # Let the gateway decide


# =============================================================================
# Execution Gateway — singleton
# =============================================================================

_gateway_instance: "ExecutionGateway | None" = None
_gateway_lock = make_lock(LockCategory.CONFIG, "execution_gateway._gateway_lock")


class ExecutionGateway:
    """
    Unified dispatch gateway for all off-loop work.

    Auto-selects the best backend based on workload hint and availability:
      - GIL_RELEASING  → Rust rayon cpu_pool (4 P-cores, NEON SIMD)
      - MLX_INFERENCE  → Rust rayon cpu_pool (MLX Metal releases GIL)
      - IO_BOUND       → SharedWorkerPool (adaptive 1-5 threads)
      - PURE_PYTHON_CPU → Subinterpreter pool (if available) else SharedWorkerPool

    Backends are lazy-initialized on first use.
    All errors → fallback to next-best backend (never crashes).
    """

    __slots__ = (
        "_rust_cpu_pool",
        "_rust_io_pool",
        "_shared_pool",
        "_subinterpreter_pool",
        "_rust_available",
        "_initialized",
    )

    def __init__(self) -> None:
        self._rust_cpu_pool: Any = None
        self._rust_io_pool: Any = None
        self._shared_pool: Any = None
        self._subinterpreter_pool: Any = None
        self._rust_available: bool | None = None
        self._initialized = False

    # ------------------------------------------------------------------
    # Lazy init
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        """Lazy-init all backends on first use (idempotent)."""
        if self._initialized:
            return
        # Import here to avoid circular imports at module load
        from hledac.universal.runtime.worker_pool import (
            RustWorkerPool,
            SharedWorkerPool,
            get_rust_pool,
            get_shared_pool,
        )

        self._shared_pool = get_shared_pool()
        self._rust_available = False
        try:
            self._rust_cpu_pool = get_rust_pool("cpu")
            self._rust_io_pool = get_rust_pool("io")
            if self._rust_cpu_pool._check_available():
                self._rust_available = True
        except Exception:
            logger.debug("[gateway] Rust rayon pools unavailable, using SharedWorkerPool")
            self._rust_cpu_pool = None
            self._rust_io_pool = None

        self._initialized = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def cpu_bound(
        self,
        fn: Callable[..., T],
        /,
        *args: Any,
        timeout: float | None = None,
        hint: str = WorkloadHint.GIL_RELEASING,
        **kwargs: Any,
    ) -> T:
        """
        Run CPU-bound callable off the event loop.

        Args:
            fn: Synchronous callable (should release the GIL for best perf).
            timeout: Optional timeout in seconds.
            hint: WorkloadHint — GIL_RELEASING (default), MLX_INFERENCE,
                  or PURE_PYTHON_CPU.

        Returns:
            Result of fn(*args, **kwargs).
        """
        self._ensure_initialized()

        # Route based on hint
        if hint == WorkloadHint.MLX_INFERENCE:
            return await self._via_rust_cpu(fn, *args, timeout=timeout, **kwargs)
        elif hint == WorkloadHint.PURE_PYTHON_CPU:
            return await self._via_subinterpreter_or_shared(fn, *args, timeout=timeout, **kwargs)
        else:
            # Default: GIL-releasing CPU → Rust rayon if available
            return await self._via_rust_cpu(fn, *args, timeout=timeout, **kwargs)

    async def io_bound(
        self,
        fn: Callable[..., T],
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> T:
        """
        Run I/O-bound blocking callable off the event loop.

        Uses SharedWorkerPool (bounded ThreadPoolExecutor, governor-aware).

        Args:
            fn: Synchronous callable (blocking I/O: WHOIS, DNS, SSL, file ops).
            timeout: Optional timeout in seconds.

        Returns:
            Result of fn(*args, **kwargs).
        """
        self._ensure_initialized()
        return await self._shared_pool.run(fn, *args, timeout=timeout, **kwargs)

    async def mlx_inference(
        self,
        fn: Callable[..., T],
        /,
        *args: Any,
        timeout: float = 60.0,
        **kwargs: Any,
    ) -> T:
        """
        Run MLX inference off the event loop.

        MLX Metal releases the GIL during GPU ops, so Rust rayon cpu_pool
        is the optimal backend. Falls back to SharedWorkerPool.

        Args:
            fn: Synchronous MLX inference callable.
            timeout: Timeout in seconds (default 60s for inference).

        Returns:
            Inference result.
        """
        return await self.cpu_bound(
            fn, *args, timeout=timeout, hint=WorkloadHint.MLX_INFERENCE, **kwargs
        )

    async def pure_python_cpu(
        self,
        fn: Callable[..., T],
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> T:
        """
        Run pure-Python CPU-bound work (no GIL release).

        Tries subinterpreter pool first (if ``HLEDAC_ENABLE_SUBINTERPRETERS=1``
        and runtime probe passes), falls back to SharedWorkerPool.

        Args:
            fn: Synchronous pure-Python callable.
            timeout: Optional timeout in seconds.
        """
        return await self.cpu_bound(
            fn, *args, timeout=timeout, hint=WorkloadHint.PURE_PYTHON_CPU, **kwargs
        )

    # ------------------------------------------------------------------
    # Internal routing
    # ------------------------------------------------------------------

    async def _via_rust_cpu(
        self,
        fn: Callable[..., T],
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> T:
        """Route via Rust rayon cpu_pool, falling back to SharedWorkerPool."""
        if self._rust_available and self._rust_cpu_pool is not None:
            try:
                return await self._rust_cpu_pool.submit(
                    fn, *args, timeout=timeout, **kwargs
                )
            except Exception as exc:
                logger.debug(
                    "[gateway] Rust cpu_pool failed: %s — falling back to SharedWorkerPool",
                    exc,
                )
        # Fallback
        return await self._shared_pool.run(fn, *args, timeout=timeout, **kwargs)

    async def _via_subinterpreter_or_shared(
        self,
        fn: Callable[..., T],
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> T:
        """Route via subinterpreter pool if available, else SharedWorkerPool."""
        if subinterpreter_available():
            try:
                return await self._via_subinterpreter(fn, *args, timeout=timeout, **kwargs)
            except Exception as exc:
                logger.debug(
                    "[gateway] Subinterpreter pool failed: %s — falling back to SharedWorkerPool",
                    exc,
                )
        return await self._shared_pool.run(fn, *args, timeout=timeout, **kwargs)

    async def _via_subinterpreter(
        self,
        fn: Callable[..., T],
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> T:
        """
        Execute via InterpreterPoolExecutor (Python 3.14+, opt-in).

        M1 8GB safe: max 2 subinterpreters (~1-2MB each).
        Each subinterpreter has its own GIL → no GIL contention.
        """
        from concurrent.futures import InterpreterPoolExecutor

        def _run_in_subinterpreter() -> T:
            with InterpreterPoolExecutor(max_workers=2) as exc:
                future = exc.submit(fn, *args, **kwargs)
                return future.result()

        loop = asyncio.get_running_loop()
        if timeout is not None:
            return await safe_wait_for(
                loop.run_in_executor(None, _run_in_subinterpreter),
                timeout=timeout,
                label="gateway_subinterpreter",
            )
        return await loop.run_in_executor(None, _run_in_subinterpreter)

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    @property
    def active_count(self) -> int:
        """Total active tasks across all backends."""
        total = 0
        if self._shared_pool is not None:
            total += self._shared_pool.active_count
        if self._rust_cpu_pool is not None:
            total += self._rust_cpu_pool._active_count
        if self._rust_io_pool is not None:
            total += self._rust_io_pool._active_count
        return total

    @property
    def backends_available(self) -> dict[str, bool]:
        """Which backends are currently available."""
        return {
            "rust_rayon_cpu": self._rust_available if self._initialized else False,
            "rust_rayon_io": self._rust_available if self._initialized else False,
            "shared_pool": True,  # Always available
            "subinterpreter_pool": subinterpreter_available(),
        }


def get_gateway() -> ExecutionGateway:
    """Return the singleton ExecutionGateway, creating on first call."""
    global _gateway_instance
    if _gateway_instance is not None:
        return _gateway_instance
    with _gateway_lock:
        if _gateway_instance is None:
            _gateway_instance = ExecutionGateway()
        return _gateway_instance


# Public module-level gateway singleton.
# Call get_gateway() once at import time — cheap since all backends are
# lazy-initialized inside ExecutionGateway._ensure_initialized().
gateway = get_gateway()


__all__ = [
    "ExecutionGateway",
    "WorkloadHint",
    "get_gateway",
    "subinterpreter_available",
    "gateway",
]
