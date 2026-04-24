"""
Sprint F201A: Smoke Concurrency Contract Tests
==============================================

Tests verify the concurrency contract that smoke_runner.py --smoke relies on:
1. AdaptiveSemaphore.current_limit returns 3 (M1 hard ceiling)
2. FETCH_SEMAPHORE.limit() returns current semaphore limit
3. adjust_fetch_workers(3/25) correctly sets LLM path concurrency
4. No network required — all tests are local

Invariant table:
  F201A-1 | AdaptiveSemaphore() initializes with current_limit=3
  F201A-2 | FETCH_SEMAPHORE.limit() delegates to underlying semaphore._value
  F201A-3 | adjust_fetch_workers(3) sets FETCH_SEMAPHORE limit to 3
  F201A-4 | adjust_fetch_workers(25) sets FETCH_SEMAPHORE limit to 25
  F201A-5 | M1 LLM path: loaded model → fetch limit 3
  F201A-6 | M1 LLM path: released model → fetch limit restored to 25
"""
from __future__ import annotations

import asyncio
import pytest

from hledac.universal import FETCH_SEMAPHORE, AdaptiveSemaphore, adjust_fetch_workers


class TestF201AAdaptiveSemaphoreContract:
    """F201A-1: AdaptiveSemaphore current_limit is the M1 hard ceiling (3)."""

    def test_adaptive_semaphore_default_initial_limit(self):
        """F201A-1: AdaptiveSemaphore() defaults to current_limit=3 (M1 hard ceiling)."""
        sem = AdaptiveSemaphore()
        assert sem.current_limit == 3, f"Expected 3 (M1 hard ceiling), got {sem.current_limit}"

    def test_adaptive_semaphore_current_limit_property(self):
        """F201A-1: current_limit is a property, not a constructor argument."""
        sem = AdaptiveSemaphore()
        # The property exists and returns an integer
        assert isinstance(sem.current_limit, int)


class TestF201AFetchSemaphoreProxy:
    """F201A-2: FETCH_SEMAPHORE.limit() returns current semaphore limit."""

    def test_fetch_semaphore_has_limit_method(self):
        """F201A-2: FETCH_SEMAPHORE is a proxy with limit() method."""
        assert hasattr(FETCH_SEMAPHORE, 'limit'), \
            f"FETCH_SEMAPHORE has no limit() method — got {type(FETCH_SEMAPHORE)}"

    def test_fetch_semaphore_limit_returns_int(self):
        """F201A-2: limit() returns an integer semaphore count."""
        result = FETCH_SEMAPHORE.limit()
        assert isinstance(result, int), f"Expected int, got {type(result)}"
        assert result >= 1, f"Expected positive limit, got {result}"


class TestF201AAdjustFetchWorkers:
    """F201A-3/4: adjust_fetch_workers() correctly sets semaphore limit."""

    @pytest.mark.asyncio
    async def test_adjust_fetch_workers_sets_limit_3(self):
        """F201A-3: adjust_fetch_workers(3) sets limit to 3."""
        await adjust_fetch_workers(3)
        assert FETCH_SEMAPHORE.limit() == 3, f"Expected limit=3, got {FETCH_SEMAPHORE.limit()}"

    @pytest.mark.asyncio
    async def test_adjust_fetch_workers_sets_limit_25(self):
        """F201A-4: adjust_fetch_workers(25) sets limit to 25."""
        await adjust_fetch_workers(25)
        assert FETCH_SEMAPHORE.limit() == 25, f"Expected limit=25, got {FETCH_SEMAPHORE.limit()}"

    @pytest.mark.asyncio
    async def test_adjust_fetch_workers_roundtrip(self):
        """F201A-3/4: roundtrip 3→25→3 maintains correct limits."""
        await adjust_fetch_workers(3)
        assert FETCH_SEMAPHORE.limit() == 3
        await adjust_fetch_workers(25)
        assert FETCH_SEMAPHORE.limit() == 25
        await adjust_fetch_workers(3)
        assert FETCH_SEMAPHORE.limit() == 3


class TestF201ALLMPathConcurrency:
    """F201A-5/6: M1 LLM path enforces fetch limit 3 when model loaded."""

    @pytest.mark.asyncio
    async def test_llm_loaded_path_sets_limit_3(self):
        """F201A-5: Simulate LLM loaded path — fetch limit becomes 3."""
        # Establish baseline at 25
        await adjust_fetch_workers(25)
        assert FETCH_SEMAPHORE.limit() == 25

        # Simulate model load event (model_manager calls adjust_fetch_workers(3))
        await adjust_fetch_workers(3)

        # M1 invariant: when LLM loaded, fetch concurrency must be 3
        assert FETCH_SEMAPHORE.limit() == 3, \
            f"M1 LLM path broken: expected limit=3 when model loaded, got {FETCH_SEMAPHORE.limit()}"

    @pytest.mark.asyncio
    async def test_llm_released_path_restores_limit_25(self):
        """F201A-6: Simulate LLM release — fetch limit restored to 25."""
        # Start at limit 3 (model loaded)
        await adjust_fetch_workers(3)
        assert FETCH_SEMAPHORE.limit() == 3

        # Simulate model release (model_manager calls adjust_fetch_workers(25))
        await adjust_fetch_workers(25)

        # Restore full concurrency after model release
        assert FETCH_SEMAPHORE.limit() == 25, \
            f"M1 LLM release path broken: expected limit=25 after release, got {FETCH_SEMAPHORE.limit()}"

    @pytest.mark.asyncio
    async def test_llm_full_lifecycle(self):
        """F201A-5/6: Full LLM lifecycle: load(3) → release(25)."""
        # Establish clean baseline
        await adjust_fetch_workers(25)
        assert FETCH_SEMAPHORE.limit() == 25

        # Model load
        await adjust_fetch_workers(3)
        assert FETCH_SEMAPHORE.limit() == 3

        # Model release
        await adjust_fetch_workers(25)
        assert FETCH_SEMAPHORE.limit() == 25


class TestF201AImportContract:
    """Root import surface: FETCH_SEMAPHORE, AdaptiveSemaphore, adjust_fetch_workers."""

    def test_root_import_fetches_semphore_proxy(self):
        """Root package exports FETCH_SEMAPHORE (lazy proxy)."""
        from hledac.universal import FETCH_SEMAPHORE
        assert hasattr(FETCH_SEMAPHORE, 'limit'), f"FETCH_SEMAPHORE missing limit() — {type(FETCH_SEMAPHORE)}"

    def test_root_import_adaptive_semaphore(self):
        """Root package exports AdaptiveSemaphore (adaptive concurrency)."""
        from hledac.universal import AdaptiveSemaphore
        sem = AdaptiveSemaphore()
        assert hasattr(sem, 'current_limit')

    def test_root_import_adjust_fetch_workers(self):
        """Root package exports adjust_fetch_workers (async dynamic adjuster)."""
        from hledac.universal import adjust_fetch_workers
        assert callable(adjust_fetch_workers)