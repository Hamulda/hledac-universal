"""
mlx_dispatch — Async MLX inference dispatch utilities.

K6 ANALÝZA ZÁVĚR:
    Původní MLXWorkerThread s loop.run_forever() je SPRÁVNÉ řešení.
    Důvod: mlx_lm.generate() je async funkce, nelze ji volat přimo z asyncio.to_thread().

    Klíčové insighty:
    1. MLX Metal GPU ops releasují GIL → main loop je volný během inference
    2. Ale asyncio.to_thread() nemůže spustit async funkci
    3. Proto potřebujeme worker thread s vlastní event loop
    4. run_coroutine_threadsafe() je korektní pattern pro cross-thread dispatch

    MLXWorkerThread overhead:
    - ~MB paměti pro persistent event loop
    - atexit/finalizer complexity
    - shutdown timeout management

    Toto je acceptable trade-off pro správnou async MLX dispatch.

    POTENCIÁLNÍ OPTIMALIZACE (budoucí práce):
    - Pokud by mlx_lm.generate() měl synchronní variantu,
      asyncio.to_thread() by byl lepší volbou.
    - Prozatím: MLXWorkerThread zůstává canonical pattern.

    Issue 8 fix: dispatch_via_to_thread() now routes through the execution
    gateway (Rust rayon cpu_pool) instead of bare asyncio.to_thread().

M1 8GB safe.
"""

from __future__ import annotations

import logging
from typing import Any

from hledac.universal.utils.asyncx import safe_wait_for
from _core import aclose

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S: float = 60.0


async def dispatch_via_worker_thread(
    coro: Any,
    worker_thread: Any,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> Any:
    """
    Dispatch async MLX inference to worker thread.

    Args:
        coro: Async coroutine (e.g., engine.generate(...))
        worker_thread: MLXWorkerThread instance
        timeout: Maximum seconds to wait

    Returns:
        Result of the coroutine

    Raises:
        TimeoutError: Inference timed out
        RuntimeError: Worker unavailable
    """
    return await worker_thread.submit(coro, timeout=timeout)


async def dispatch_via_to_thread(
    fn: Any,
    *args: Any,
    timeout: float = DEFAULT_TIMEOUT_S,
    **kwargs: Any,
) -> Any:
    """
    Dispatch sync MLX inference via the execution gateway.

    Použití pouze pro SYNCHRONNÍ funkce!
    Pro async MLX inference použij dispatch_via_worker_thread().

    MLX Metal releases the GIL during GPU ops, so the gateway routes to
    Rust rayon cpu_pool (4 P-cores, NEON SIMD) for true parallelism.
    Falls back to SharedWorkerPool when Rust extension is unavailable.

    Issue 8 fix: replaced bare asyncio.to_thread() with gateway.mlx_inference().

    Args:
        fn: Synchronní funkce
        *args, **kwargs: Argumenty
        timeout: Timeout v sekundách

    Returns:
        Výsledek fn(*args, **kwargs)
    """
    from hledac.universal.runtime.execution_gateway import gateway

    try:
        return await gateway.mlx_inference(fn, *args, timeout=timeout, **kwargs)
    except TimeoutError as exc:
        raise TimeoutError(f"MLX inference timed out after {timeout}s") from exc


__all__ = [
    "dispatch_via_worker_thread",
    "dispatch_via_to_thread",
    "DEFAULT_TIMEOUT_S",
]
