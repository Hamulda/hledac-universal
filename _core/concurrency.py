"""
core/concurrency.py — Canonical Production Concurrency Facade

R12 SOLUTION: Single entry point for all asyncio.Semaphore access.
Replaces the misnamed ``get_semaphore_for_testing`` with a production-grade
``get_semaphore(category)`` that delegates to the unified
ConcurrencyBudgetRegistry singleton.

ARCHITECTURE:
  ┌──────────────────────────┐
  │  core/concurrency.py     │  ← canonical import for ALL production code
  │  get_semaphore(category) │
  └──────────┬───────────────┘
             │ delegates
  ┌──────────▼───────────────┐
  │  ConcurrencyBudgetRegistry│  ← single source of truth
  │  (concurrency_registry)  │     UMA-aware, telemetry, dynamic
  └──────────────────────────┘

USAGE (production — sync-safe, callable from module level):
    from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore

    _SEM = get_semaphore(ConcurrencyCategory.HTTP_LANE)
    async with _SEM:
        await fetch(url)

USAGE (async — UMA-aware dynamic limit):
    from hledac.universal._core.concurrency_registry import get_budget

    sem = await get_budget(ConcurrencyCategory.HTTP_LANE)

M1 8GB UMA:
  — Single semaphore instance per category (no cache duplication)
  — Limits from ConcurrencyBudget (OK/WARN/CRITICAL/EMERGENCY)
  — Thread-safe via ConcurrencyBudgetRegistry's threading.Lock

Python 3.14+ (PEP 789):
  — Semaphore is created lazily on first get_semaphore() call
  — Must be called from within an event loop context for clean creation
  — Module-level usage is tolerated (DeprecationWarning under PEP 789)

Sprint R12 (2026-07-19)
"""

from __future__ import annotations

import asyncio
import logging

from hledac.universal._core.concurrency_registry import (
    ConcurrencyBudgetRegistry,
    ConcurrencyCategory,
    )

logger = logging.getLogger(__name__)

__all__ = [
    "ConcurrencyCategory",
    "get_semaphore",
]


def get_semaphore(category: ConcurrencyCategory) -> asyncio.Semaphore:
    """
    Get cached Semaphore for a concurrency category — production API.

    Delegates to ConcurrencyBudgetRegistry.get_instance().get(category),
    which provides:
      — Unified cache: one Semaphore instance per category across the entire process
      — UMA-aware limits: OK/WARN/CRITICAL/EMERGENCY from ConcurrencyBudget
      — Telemetry: acquire/release/rejected counters
      — Dynamic adjustment: adjust_for_state() can swap semaphores atomically

    Thread-safety:
      ConcurrencyBudgetRegistry.get_instance() uses threading.Lock for
      singleton init. registry.get() uses dict.get() + atomic insert
      (CPython GIL-safe for dict ops).

    Sync-safe:
      Can be called at module level — no ``await`` required. The underlying
      registry is initialized synchronously on first call.

    Args:
        category: ConcurrencyCategory enum member (e.g., HTTP_LANE, DNS_BRUTE).

    Returns:
        asyncio.Semaphore singleton for the category (OK-state limit on
        first creation; may be swapped by adjust_for_state() later).

    Example:
        from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore

        _FETCH_SEM = get_semaphore(ConcurrencyCategory.HTTP_LANE)

        async def fetch_url(url: str) -> str:
            async with _FETCH_SEM:
                return await _do_fetch(url)
    """
    registry = ConcurrencyBudgetRegistry.get_instance()
    return registry.get(category)
