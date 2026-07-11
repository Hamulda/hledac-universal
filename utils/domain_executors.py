"""
domain_executors — Bounded domain-specific executor registry.

Replaces ad-hoc loop.run_in_executor(None, ...) calls with a registry of
sized thread pools tuned for M1 8GB UMA.

M1 8GB PROBLEM
==============
Python 3.14 default executor: min(32, os.cpu_count() + 4) = 32 workers on M1.
With 8 effective cores, 32 workers causes context-switch thrashing.

SOLUTION
========
Domain-specific pools with bounded worker counts:

  nlp      — GLiNER2, fast-langdetect (CPU-simulated, fast)
  vision   — PyMuPDF, vision encoder (I/O + CPU mix)
  embed    — MLX inference (actually Metal, but executor used for sync bridge)
  storage  — DuckDB sync adapter (thread-safe, bounded)
  crypto   — yara-python, Pycryptodome (CPU-bound)

Each pool sized = max(2, min(preset.max_workers, os.cpu_count())) capped at 4 workers.

INTERPRETERPOOLEXECUTOR (Python 3.14+)
======================================
PEP 756: InterpreterPoolExecutor provides true parallelism via subinterpreters.
Each worker has its own GIL — no GIL contention.

  When to use:
    - Heavy pure-Python CPU work (>100ms per task)
    - Large batch chunks (>10K items)
    - Workers pre-warmed with module imports

  When NOT to use:
    - Small/medium tasks (overhead too high)
    - I/O-bound tasks (ThreadPoolExecutor wins)
    - M1 8GB: lower RSS than subinterpreters

For DuckDB + MLX coexistence on M1 8GB, ThreadPoolExecutor (bounded)
is optimal — DuckDB releases GIL in C extension, MLX runs on Metal.
"""

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# CPU cap for M1 8GB: max 4 workers to avoid context-switch thrashing
_M1_CPU_CAP = int(os.environ.get("HLEDAC_DOMAIN_EXECUTOR_WORKERS", "4"))


def _cpu_workers() -> int:
    """Calculate bounded worker count for M1 8GB."""
    cpu_count = os.cpu_count() or 4
    return max(2, min(cpu_count, _M1_CPU_CAP))


@dataclass(frozen=True, slots=True)
class DomainExecutors:
    """
    Bounded executor registry — one pool per domain.

    Replace all loop.run_in_executor(None, sync_fn) calls with:
        await loop.run_in_executor(domain_executors.<domain>, sync_fn)

    Each pool is lazily initialized and shared across the application.
    """

    nlp: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(
            max_workers=_cpu_workers(),
            thread_name_prefix="nlp",
        )
    )
    vision: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(
            max_workers=max(2, _cpu_workers() // 2),
            thread_name_prefix="vision",
        )
    )
    embed: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(
            max_workers=1,  # MLX is Metal, 1 worker sufficient
            thread_name_prefix="embed",
        )
    )
    storage: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(
            max_workers=max(2, _cpu_workers() // 2),
            thread_name_prefix="storage",
        )
    )
    crypto: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(
            max_workers=1,  # yara-python is CPU-bound but fast
            thread_name_prefix="crypto",
        )
    )
    # Fallback for any unmapped domain
    default: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(
            max_workers=_cpu_workers(),
            thread_name_prefix="default",
        )
    )


# Global registry — lazily initialized on first use
_registry: DomainExecutors | None = None
_registry_lock = threading.Lock()


def get_domain_executors() -> DomainExecutors:
    """Get the global domain executors registry (singleton)."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = DomainExecutors()
    return _registry


async def run_in_domain(
    domain: str,
    func: Callable[..., T],
    *args: Any,
) -> T:
    """
    Run a sync function in a domain-specific executor.

    Args:
        domain: One of 'nlp', 'vision', 'embed', 'storage', 'crypto', 'default'
        func: Synchronous function to execute
        *args: Arguments to pass to func

    Returns:
        The result of func(*args)
    """
    executors = get_domain_executors()
    executor = getattr(executors, domain, executors.default)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, lambda: func(*args))


# Convenience aliases for common domains
run_in_nlp = lambda func, *args: run_in_domain("nlp", func, *args)
run_in_vision = lambda func, *args: run_in_domain("vision", func, *args)
run_in_embed = lambda func, *args: run_in_domain("embed", func, *args)
run_in_storage = lambda func, *args: run_in_domain("storage", func, *args)
run_in_crypto = lambda func, *args: run_in_domain("crypto", func, *args)


def shutdown_domain_executors() -> None:
    """
    Gracefully shutdown all domain executors.

    Call on application shutdown (e.g., in atexit handler).
    """
    global _registry
    if _registry is None:
        return

    with _registry_lock:
        if _registry is not None:
            for executor in (
                _registry.nlp,
                _registry.vision,
                _registry.embed,
                _registry.storage,
                _registry.crypto,
                _registry.default,
            ):
                executor.shutdown(wait=False)
            _registry = None
