"""
Utils Concurrency — Centralized asyncio synchronization primitives
================================================================

Single source of truth for shared asyncio primitives.
Import from here — never from __init__.py for synchronization primitives.

P19: Created to break circular import between __init__.py and public_fetcher.py.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# P3: FETCH_SEMAPHORE — shared semaphore for fetch concurrency control
_FETCH_SEMAPHORE: asyncio.Semaphore | None = None


def get_fetch_semaphore(initial_limit: int = 25) -> asyncio.Semaphore:
    """
    Get or create the shared FETCH_SEMAPHORE.

    This is a lazy singleton — semaphore is created on first call within event loop.

    Args:
        initial_limit: Initial semaphore limit (default 25)

    Returns:
        The shared FETCH_SEMAPHORE instance
    """
    global _FETCH_SEMAPHORE
    if _FETCH_SEMAPHORE is None:
        _FETCH_SEMAPHORE = asyncio.Semaphore(initial_limit)
        logger.debug(f"[FETCH_SEMAPHORE] Created with limit={initial_limit}")
    return _FETCH_SEMAPHORE


# For backward compatibility — module-level binding
# Usage: from utils.concurrency import FETCH_SEMAPHORE
# This creates the semaphore on first access
class _FetchSemaphoreProxy:
    """Proxy object that lazily initializes the semaphore on first attribute access."""

    def __getattr__(self, name: str):
        sem = get_fetch_semaphore()
        return getattr(sem, name)

    def limit(self) -> int:
        """Return current semaphore limit (delegates to underlying semaphore)."""
        return get_fetch_semaphore()._value

    def __repr__(self):
        sem = get_fetch_semaphore()
        return f"FetchSemaphore(limit={sem._value})"


FETCH_SEMAPHORE = _FetchSemaphoreProxy()


async def adjust_fetch_workers(new_limit: int) -> None:
    """
    Backward-compatible alias for production clearnet fetch concurrency.

    Adjusts BOTH _FETCH_SEMAPHORE and _clearnet_semaphore to new_limit.
    """
    global _FETCH_SEMAPHORE, _clearnet_semaphore
    old_fetch = _FETCH_SEMAPHORE._value if _FETCH_SEMAPHORE else 0
    old_clearnet = _clearnet_semaphore._value if _clearnet_semaphore else 0
    _FETCH_SEMAPHORE = asyncio.Semaphore(new_limit)
    _clearnet_semaphore = asyncio.Semaphore(max(1, new_limit))
    logger.info(f"[FETCH_WORKERS] Adjusted fetch {old_fetch}→{new_limit}, clearnet {old_clearnet}→{new_limit}")


# =============================================================================
# F191B: Separate semaphore pools for clearnet vs Tor — no head-of-line blocking
# =============================================================================
# Sprint F191B: clearnet/Tor separate pools prevent Tor latency starving clearnet
# clearnet: 25 concurrent (fast, parallelizable)
# Tor: 5 concurrent (slow by design, circuit setup)
# M1 8GB adaptive: reduce when RAM > 5.5 GB

import psutil

_clearnet_semaphore: asyncio.Semaphore | None = None
_tor_semaphore: asyncio.Semaphore | None = None

CLEARNET_CONCURRENCY: int = 25
TOR_CONCURRENCY: int = 5


def get_clearnet_semaphore() -> asyncio.Semaphore:
    """Get or create the shared clearnet semaphore (lazy singleton)."""
    global _clearnet_semaphore
    if _clearnet_semaphore is None:
        adaptive = get_adaptive_limit()
        _clearnet_semaphore = asyncio.Semaphore(adaptive)
        logger.debug(f"[CLEARNET_SEMAPHORE] Created with limit={adaptive}")
    return _clearnet_semaphore


def get_tor_semaphore() -> asyncio.Semaphore:
    """Get or create the shared Tor semaphore (lazy singleton)."""
    global _tor_semaphore
    if _tor_semaphore is None:
        _tor_semaphore = asyncio.Semaphore(TOR_CONCURRENCY)
        logger.debug(f"[TOR_SEMAPHORE] Created with limit={TOR_CONCURRENCY}")
    return _tor_semaphore


def get_adaptive_limit() -> int:
    """
    Reduce concurrency limit when RAM > 5.5 GB (M1 8GB constraint).

    Returns adaptive clearnet concurrency based on memory pressure:
    - RSS > 5.5 GB: 3 (critical — LLM + orchestrator active)
    - RSS > 4.5 GB: 10 (moderate — LLM loaded)
    - otherwise: CLEARNET_CONCURRENCY (25)
    """
    try:
        rss_gb = psutil.Process().memory_info().rss / 1e9
    except Exception:
        return CLEARNET_CONCURRENCY
    if rss_gb > 5.5:
        return 3
    elif rss_gb > 4.5:
        return 10
    return CLEARNET_CONCURRENCY


async def adjust_clearnet_workers(new_limit: int) -> None:
    """Dynamically adjust clearnet semaphore limit."""
    global _clearnet_semaphore
    old_limit = _clearnet_semaphore._value if _clearnet_semaphore else 0
    _clearnet_semaphore = asyncio.Semaphore(max(1, new_limit))
    logger.info(f"[CLEARNET_WORKERS] Adjusted from {old_limit} to {new_limit}")
