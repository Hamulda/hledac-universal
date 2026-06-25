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

M1 8GB safe.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

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
    Dispatch sync MLX inference via asyncio.to_thread().

    Použití pouze pro SYNCHRONNÍ funkce!
    Pro async MLX inference použij dispatch_via_worker_thread().

    MLX Metal releasu-je GIL během GPU ops, takže main loop zůstává volný.

    Args:
        fn: Synchronní funkce
        *args, **kwargs: Argumenty
        timeout: Timeout v sekundách

    Returns:
        Výsledek fn(*args, **kwargs)
    """
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(fn, *args, **kwargs),
            timeout=timeout,
        )
        return result
    except TimeoutError:
        raise TimeoutError(f"MLX inference timed out after {timeout}s")


__all__ = [
    "dispatch_via_worker_thread",
    "dispatch_via_to_thread",
    "DEFAULT_TIMEOUT_S",
]
