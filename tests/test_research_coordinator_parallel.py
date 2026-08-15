"""
test_research_coordinator_parallel.py
====================================
ISSUE-AP-01 ACCEPTANCE TEST

Verifies that execute_research_plan runs 3+ agents in parallel —
~ max(latency) not sum(latency).

Serial:  3 agents × 500ms = 1500ms total
Parallel: max(500ms, 500ms, 500ms) ≈ 500ms

Invariant: parallel_total ≤ serial_total * 0.6
(60% threshold — empirically distinguishes parallel from serial)
"""
from __future__ import annotations
import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest
from _core import aclose


@pytest.mark.asyncio
async def test_execute_research_plan_parallel_vs_serial():
    """
    ISSUE-AP-01: 3-agent plan runs in ~max(latency), not sum(latency).

    Layout:
        agent_0: 500ms
        agent_1: 500ms
        agent_2: 500ms

    Serial expected: 1500ms
    Parallel expected: ~500ms (max)
    Threshold: parallel_total < serial_total * 0.6  (900ms)
    """
    from coordinators.research_coordinator import UniversalResearchCoordinator

    coordinator = UniversalResearchCoordinator()

    # Track call order and timing
    call_times: list[float] = []

    # Create AsyncMocks that track calls
    mock_academic = AsyncMock(return_value={'source': 'academic', 'results': []})
    mock_archives = AsyncMock(return_value={'source': 'archive', 'results': []})
    mock_crawl = AsyncMock(return_value={'source': 'crawl', 'results': []})

    async def mock_search_academic(self, query: str, sources: list | None = None) -> dict:
        await asyncio.sleep(0.5)  # 500ms
        call_times.append(time.monotonic())
        return {'source': 'academic', 'query': query, 'results': []}

    async def mock_search_archives(self, url: str) -> dict:
        await asyncio.sleep(0.5)
        call_times.append(time.monotonic())
        return {'source': 'archive', 'url': url, 'results': []}

    async def mock_crawl_url(self, url: str, depth: int = 1) -> dict:
        await asyncio.sleep(0.5)
        call_times.append(time.monotonic())
        return {'source': 'crawl', 'url': url, 'depth': depth, 'results': []}

    cls = UniversalResearchCoordinator
    with patch.object(cls, 'search_academic', mock_search_academic), \
         patch.object(cls, 'search_archives', mock_search_archives), \
         patch.object(cls, 'crawl_url', mock_crawl_url):

        plan = {
            'query': 'test query',
            'agents': [
                {'type': 'academic', 'task': 'query0'},
                {'type': 'archive', 'task': 'url1'},
                {'type': 'crawl', 'task': 'url2', 'url': 'http://example.com/3', 'depth': 1},
            ],
        }

        start = time.monotonic()
        results = await coordinator.execute_research_plan(plan)
        elapsed = time.monotonic() - start

    # Verify all 3 agents were called
    assert len(call_times) == 3, f'Expected 3 agent calls, got {len(call_times)}'
    assert len(results) == 3, f'Expected 3 results, got {len(results)}'

    # Verify all succeeded
    for r in results:
        assert r.get('success') is not False, f'Agent failed: {r}'

    # ISSUE-AP-01: Parallel timing check
    # Serial would be ~1500ms (3 × 500ms)
    # Parallel should be ~500ms (max of 3 × 500ms)
    serial_estimate = 0.5 * 3  # 1500ms
    parallel_threshold = serial_estimate * 0.6  # 900ms

    assert elapsed < parallel_threshold, (
        f'ISSUE-AP-01 FAIL: elapsed={elapsed:.3f}s exceeds parallel threshold '
        f'{parallel_threshold:.3f}s — agents may be running serially. '
        f'Serial would be ~{serial_estimate:.1f}s'
    )

    # Verify agents ran concurrently (overlapping in time)
    # In parallel: all call_times should be within ~500ms window
    # In serial: call_times would be ~500ms apart
    time_spread = max(call_times) - min(call_times)

    # Spread should be close to 500ms (all agents finish around same time)
    # Not 1000ms+ (which would indicate serial execution)
    assert time_spread < 0.8, (
        f'ISSUE-AP-01 FAIL: time_spread={time_spread:.3f}s suggests serial '
        f'execution (agents finished 500ms apart instead of together)'
    )


@pytest.mark.asyncio
async def test_execute_research_plan_exception_isolation():
    """
    Verifies that a failing agent does NOT prevent other agents from running.
    """
    from coordinators.research_coordinator import UniversalResearchCoordinator

    coordinator = UniversalResearchCoordinator()
    call_count = 0

    async def mock_search_academic(self, query: str, sources: list | None = None) -> dict:
        nonlocal call_count
        call_count += 1
        return {'source': 'academic', 'results': []}

    async def mock_search_archives(self, url: str) -> dict:
        nonlocal call_count
        call_count += 1
        raise RuntimeError('Archive search failed')

    async def mock_crawl_url(self, url: str, depth: int = 1) -> dict:
        nonlocal call_count
        call_count += 1
        return {'source': 'crawl', 'results': []}

    cls = UniversalResearchCoordinator
    with patch.object(cls, 'search_academic', mock_search_academic), \
         patch.object(cls, 'search_archives', mock_search_archives), \
         patch.object(cls, 'crawl_url', mock_crawl_url):

        plan = {
            'agents': [
                {'type': 'academic', 'task': 'query0'},
                {'type': 'archive', 'task': 'url1'},  # Will fail
                {'type': 'crawl', 'task': 'url2'},
            ],
        }

        results = await coordinator.execute_research_plan(plan)

    # All 3 agents should have been attempted
    assert call_count == 3, f'Expected 3 agents called, got {call_count}'

    # Should have 3 results (even though one failed)
    assert len(results) == 3

    # Verify correct number of results (all 3 attempted)
    assert len(results) == 3, f'Expected 3 results, got {len(results)}: {results}'

    # Verify we have the expected sources
    sources = {r.get('source') for r in results}
    assert 'academic' in sources, f'academic missing from results: {results}'
    assert 'crawl' in sources, f'crawl missing from results: {results}'

    # Academic and crawl succeeded
    academic_result = next((r for r in results if r.get('source') == 'academic'), None)
    crawl_result = next((r for r in results if r.get('source') == 'crawl'), None)
    assert academic_result is not None and academic_result.get('success') is not False
    assert crawl_result is not None and crawl_result.get('success') is not False

    # Archive failed gracefully (may be filtered out by parallel policy='collect')
    archive_result = next((r for r in results if r.get('source') == 'archive'), None)
    if archive_result is not None:
        # If present, should be a failed result
        assert archive_result.get('success') is False
        assert 'error' in archive_result
        assert 'Archive search failed' in archive_result['error']
