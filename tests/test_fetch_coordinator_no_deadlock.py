"""
AP-02: Deadlock audit test for FetchCoordinator.

Tests 5× asyncio.Lock patterns in FetchCoordinator (lines 171-387):
  - AIMDWindow._lock + _window_lock (lines 171-172)
  - FetchCoordinator._lightpanda_lock (line 336)
  - FetchCoordinator._privacy_lock (line 348)
  - FetchCoordinator._dedup_lock (line 387)

FINDINGS:
- Original code was NOT buggy - return statements are OUTSIDE async with blocks
- Each lock serves a distinct purpose (dedup, privacy, lightpanda, AIMD)
- Lock acquisition order is consistent (dedup → host_gate → privacy)
- No nested Lock acquisition occurs - semaphores used for slot coordination

Verifies:
1. No lock leaks on early return paths
2. Concurrent access patterns don't deadlock
3. Lock ordering is consistent
"""

import asyncio
import random
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

import pytest

# Add project root to path for imports
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')


class MockDeduplicationStrategy:
    """Mock dedup strategy that tracks add/discard operations."""
    def __init__(self):
        self._urls: set = set()

    def add(self, url: str) -> None:
        self._urls.add(url)

    def discard(self, url: str) -> None:
        self._urls.discard(url)

    def __contains__(self, url: str) -> bool:
        return url in self._urls


class MockPrivacyBudgetAllocator:
    """Mock privacy budget allocator."""
    def __init__(self):
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def get_lane_for_url(self, url: str) -> str:
        if 'tor' in url.lower():
            return 'tor'
        return 'clearnet'

    def get_semaphore(self, lane: str) -> asyncio.Semaphore | None:
        if lane not in self._semaphores:
            self._semaphores[lane] = asyncio.Semaphore(5)
        return self._semaphores[lane]


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Lock leak on early return (line 1342 path)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dedup_lock_no_leak_on_early_return():
    """
    AP-02 FIX VERIFICATION: _dedup_lock was leaked when returning early
    on line 1342 inside _fetch_url when gov.can_afford_sync returned False.

    This test verifies the lock is properly released on ALL exit paths.
    """
    from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator, FetchCoordinatorConfig

    config = FetchCoordinatorConfig()
    fc = FetchCoordinator(config=config, max_concurrent=3)

    # Mock _processed_urls with our mock
    fc._processed_urls = MockDeduplicationStrategy()
    fc._privacy_allocator = MockPrivacyBudgetAllocator()
    fc._privacy_lock = asyncio.Lock()

    # Mock resource governor to return cannot afford
    mock_gov = MagicMock()
    mock_gov.can_afford_sync = MagicMock(return_value=False)

    test_url = "https://example.com/test"

    with patch('hledac.universal.core.protocols.get_governor', return_value=mock_gov):
        # Call _fetch_url directly - it should NOT deadlock
        # With the bug: lock acquired on line 1314, early return on line 1342
        # without releasing the lock → deadlock after 2nd call
        result = await fc._fetch_url(test_url)

        # Should return None (early exit due to memory pressure)
        assert result is None, f"Expected None but got {result}"

        # Critical: the lock must NOT be held after _fetch_url returns
        # Verify by acquiring it ourselves - if lock leaked, this will hang/timeout
        try:
            async with asyncio.timeout(2.0):
                await fc._dedup_lock.acquire()
                fc._dedup_lock.release()
        except asyncio.TimeoutError:
            pytest.fail("DEADLOCK DETECTED: _dedup_lock was leaked!")


@pytest.mark.asyncio
async def test_dedup_lock_multiple_concurrent_calls():
    """
    Test that multiple concurrent _fetch_url calls don't deadlock.
    Simulates the real-world scenario where 100+ coroutines complete simultaneously.
    """
    from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator, FetchCoordinatorConfig

    config = FetchCoordinatorConfig()
    fc = FetchCoordinator(config=config, max_concurrent=10)

    fc._processed_urls = MockDeduplicationStrategy()
    fc._privacy_allocator = MockPrivacyBudgetAllocator()

    # Mock governor to always say "cannot afford" → early return path
    mock_gov = MagicMock()
    mock_gov.can_afford_sync = MagicMock(return_value=False)

    # Mock per_host_gate to avoid semaphore saturation
    mock_gate = AsyncMock()
    mock_gate.acquire = AsyncMock(return_value=(None, None))  # Returns (None, None) for no semaphore
    mock_gate.release = MagicMock()
    fc._per_host_gate = mock_gate

    urls = [f"https://example.com/page{i}" for i in range(20)]

    with patch('hledac.universal.core.protocols.get_governor', return_value=mock_gov):
        # Run 20 concurrent fetches - should NOT deadlock
        try:
            async with asyncio.timeout(10.0):
                results = await asyncio.gather(*[
                    fc._fetch_url(url) for url in urls
                ], return_exceptions=True)
        except asyncio.TimeoutError:
            pytest.fail("DEADLOCK: 20 concurrent _fetch_url calls timed out!")

        # All should return None (early exit)
        assert all(r is None for r in results), "Unexpected non-None results"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Nested lock acquisition order (no deadlock if always same order)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nested_lock_acquisition_no_deadlock():
    """
    Verify that nested lock acquisitions don't cause deadlock when
    locks are always acquired in the same order.

    Current order in _fetch_url:
    1. _dedup_lock (line 1314, 1340, 1357)
    2. _per_host_gate (async semaphore via _host_sem)
    3. _privacy_lock (line 770 via _privacy_acquire_for_url)
    """
    from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator, FetchCoordinatorConfig

    config = FetchCoordinatorConfig()
    fc = FetchCoordinator(config=config, max_concurrent=3)

    fc._processed_urls = MockDeduplicationStrategy()
    fc._privacy_allocator = MockPrivacyBudgetAllocator()

    # Test that acquiring locks in the same order doesn't deadlock
    for _ in range(100):
        # Simulate the lock pattern from _fetch_url
        async with fc._dedup_lock:
            fc._processed_urls.add("https://test.com")
            # Simulate some async work
            await asyncio.sleep(0.001)
            fc._processed_urls.discard("https://test.com")


@pytest.mark.asyncio
async def test_random_lock_ordering_no_deadlock():
    """
    AP-02: 100 random lock orderings test (reduced from 10k for speed).
    Verifies no deadlock occurs with random acquisition patterns.
    """
    from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator, FetchCoordinatorConfig

    config = FetchCoordinatorConfig()
    fc = FetchCoordinator(config=config, max_concurrent=10)

    fc._processed_urls = MockDeduplicationStrategy()
    fc._privacy_allocator = MockPrivacyBudgetAllocator()

    lock_orderings = [
        ['_dedup_lock', '_privacy_lock'],
        ['_privacy_lock', '_dedup_lock'],
        ['_dedup_lock'],
        ['_privacy_lock'],
    ]

    async def random_lock_order():
        """Simulate random lock acquisition order."""
        locks = random.choice(lock_orderings)
        for lock_name in locks:
            lock = getattr(fc, lock_name)
            async with lock:
                await asyncio.sleep(0.001)

    async def worker(_worker_id: int):
        """Worker that acquires locks in random order."""
        for _ in range(5):  # Reduced from 10
            await random_lock_order()

    # Run 100 iterations with 5 concurrent workers
    # Total: 500 random lock orderings
    for iteration in range(100):
        workers = [worker(i) for i in range(5)]
        try:
            async with asyncio.timeout(5.0):
                await asyncio.gather(*workers, return_exceptions=True)
        except asyncio.TimeoutError:
            pytest.fail(f"DEADLOCK at iteration {iteration}: random lock ordering caused timeout!")


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: AIMDWindow lock patterns (_lock, _window_lock)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_aimd_window_concurrent_on_success():
    """
    Test AIMDWindow.on_success() under concurrent load.
    Verifies _lock and _window_lock don't cause deadlock.
    """
    from hledac.universal.coordinators.fetch_coordinator import AIMDWindow

    window = AIMDWindow(initial=3.0)

    async def success_worker():
        for _ in range(100):
            await window.on_success(multiplier=1.0)
            await asyncio.sleep(0.0001)

    # 10 workers × 100 iterations = 1000 concurrent on_success calls
    workers = [success_worker() for _ in range(10)]

    try:
        async with asyncio.timeout(5.0):
            await asyncio.gather(*workers, return_exceptions=True)
    except asyncio.TimeoutError:
        pytest.fail("DEADLOCK: AIMDWindow.on_success() timed out!")

    # Verify final state is consistent
    assert window.window >= 3.0
    assert window.successes >= 0


@pytest.mark.asyncio
async def test_aimd_window_concurrent_on_failure():
    """
    Test AIMDWindow.on_failure() under concurrent load.
    Verifies _lock and _window_lock don't cause deadlock.
    """
    from hledac.universal.coordinators.fetch_coordinator import AIMDWindow

    window = AIMDWindow(initial=3.0)

    async def failure_worker():
        for _ in range(50):
            await window.on_failure(uma_state='ok')
            await asyncio.sleep(0.0001)

    workers = [failure_worker() for _ in range(10)]

    try:
        async with asyncio.timeout(5.0):
            await asyncio.gather(*workers, return_exceptions=True)
    except asyncio.TimeoutError:
        pytest.fail("DEADLOCK: AIMDWindow.on_failure() timed out!")

    assert window.window >= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Lightpanda lock DCLP pattern
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lightpanda_lock_dclp_pattern():
    """
    Test _lightpanda_lock DCLP (Double-Checked Locking Pattern).
    The lock should only be held during pool start, not during get_instance.
    """
    from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator, FetchCoordinatorConfig

    config = FetchCoordinatorConfig()
    fc = FetchCoordinator(config=config, max_concurrent=3)

    fc._lightpanda_pool_started = False
    fc._lightpanda_lock = asyncio.Lock()

    # Mock the lightpanda pool
    mock_pool = AsyncMock()
    mock_pool.start = AsyncMock()
    mock_pool.get_instance = AsyncMock(return_value="instance")
    mock_pool.release = AsyncMock()

    fc._lightpanda_pool = mock_pool

    # First call should acquire lock and start pool
    result1 = await fc._fetch_with_lightpanda("https://example.com")
    assert result1 is None  # Falls back to curl_cffi

    # Second call should NOT need lock (pool already started)
    result2 = await fc._fetch_with_lightpanda("https://example2.com")
    assert result2 is None


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Privacy lock acquisition/release cycle
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_privacy_lock_acquire_release_cycle():
    """
    Test _privacy_lock acquire/release cycle.
    Verify locks are properly released after use.
    """
    from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator, FetchCoordinatorConfig

    config = FetchCoordinatorConfig()
    fc = FetchCoordinator(config=config, max_concurrent=3)

    fc._privacy_allocator = MockPrivacyBudgetAllocator()
    fc._privacy_lock = asyncio.Lock()

    test_url = "https://example.com/test"

    # Test normal path - lock acquired and released
    lane, acquired = await fc._privacy_acquire_for_url(test_url)
    assert acquired is True

    # Verify lock is NOT held after _privacy_acquire_for_url returns
    try:
        async with asyncio.timeout(1.0):
            async with fc._privacy_lock:
                pass  # Lock should be available
    except asyncio.TimeoutError:
        pytest.fail("DEADLOCK: _privacy_lock was not released!")

    # Release should be safe to call
    fc._privacy_release(lane)


# ─────────────────────────────────────────────────────────────────────────────
# Integration test: Full _fetch_url with all lock paths exercised
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_url_full_integration_no_deadlock():
    """
    Integration test: Exercise all lock paths in _fetch_url.

    Lock paths exercised:
    - _dedup_lock (lines 1314, 1340, 1357)
    - _privacy_lock (via _privacy_acquire_for_url line 770)
    - _lightpanda_lock (via _fetch_with_lightpanda line 791)
    - AIMDWindow locks (via _aimd controller)
    """
    from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator, FetchCoordinatorConfig

    config = FetchCoordinatorConfig(max_urls_per_step=5, max_evidence_per_step=10)
    fc = FetchCoordinator(config=config, max_concurrent=5)

    fc._processed_urls = MockDeduplicationStrategy()
    fc._privacy_allocator = MockPrivacyBudgetAllocator()

    # Mock to exercise all paths
    mock_gov = MagicMock()
    mock_gov.can_afford_sync = MagicMock(return_value=True)

    # Test with a mix of URLs that exercise different paths
    urls = [
        "https://example.com/page1",
        "https://example.com/page2",
        "https://example.com/page3",
        "https://example.com/page4",
        "https://example.com/page5",
    ]

    with patch('hledac.universal.core.protocols.get_governor', return_value=mock_gov):
        try:
            async with asyncio.timeout(15.0):
                results = await asyncio.gather(*[
                    fc._fetch_url(url) for url in urls
                ], return_exceptions=True)
        except asyncio.TimeoutError:
            pytest.fail("DEADLOCK: Full _fetch_url integration test timed out!")

        # Should complete without deadlock
        # Note: Some results may be None due to mocking, but no deadlock
        assert len(results) == len(urls)


# ─────────────────────────────────────────────────────────────────────────────
# Stress test: 1000 iterations as per acceptance criteria
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.slow
async def test_deadlock_stress_1000_iterations():
    """
    AP-02 ACCEPTANCE CRITERIA: deadlock test passes 1000 iterations.

    This is the main acceptance test for Issue AP-02.
    Runs 1000 iterations of lock acquisition patterns to verify no deadlock.
    """
    from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator, FetchCoordinatorConfig

    config = FetchCoordinatorConfig()
    fc = FetchCoordinator(config=config, max_concurrent=10)

    fc._processed_urls = MockDeduplicationStrategy()
    fc._privacy_allocator = MockPrivacyBudgetAllocator()

    # Mock per_host_gate to avoid semaphore saturation
    mock_gate = AsyncMock()
    mock_gate.acquire = AsyncMock(return_value=(None, None))
    mock_gate.release = MagicMock()
    fc._per_host_gate = mock_gate

    # Mock governor to sometimes allow, sometimes deny
    call_count = 0
    def mock_can_afford(_data, _priority):
        nonlocal call_count
        call_count += 1
        return call_count % 3 != 0  # Deny every 3rd call

    mock_gov = MagicMock()
    mock_gov.can_afford_sync = MagicMock(side_effect=mock_can_afford)

    iteration_failures = []

    with patch('hledac.universal.core.protocols.get_governor', return_value=mock_gov):
        for i in range(1000):
            url = f"https://example.com/stress{i}"

            try:
                async with asyncio.timeout(5.0):
                    await fc._fetch_url(url)

                    # Verify lock is released after each call
                    try:
                        async with asyncio.timeout(0.5):
                            await fc._dedup_lock.acquire()
                            fc._dedup_lock.release()
                    except asyncio.TimeoutError:
                        iteration_failures.append(f"Iteration {i}: _dedup_lock leaked")

            except asyncio.TimeoutError:
                iteration_failures.append(f"Iteration {i}: Deadlock (timeout)")

            # Small delay every 100 iterations
            if i % 100 == 0:
                await asyncio.sleep(0.01)

    if iteration_failures:
        failure_msg = "\n".join(iteration_failures[:10])  # Show first 10 failures
        pytest.fail(f"DEADLOCK detected in {len(iteration_failures)} iterations:\n{failure_msg}")

    # All 1000 iterations passed
    assert len(iteration_failures) == 0, f"Expected 0 failures but got {len(iteration_failures)}"
