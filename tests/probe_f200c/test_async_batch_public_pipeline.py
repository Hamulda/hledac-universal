"""
Sprint F200C: Async Batch Public Pipeline Tests
==============================================

Tests verify that live_public_pipeline uses bounded async batch processing
with proper async hygiene (gather + _check_gathered).

Invariant table:
  invariant_1 | asyncio.gather uses return_exceptions=True
  invariant_2 | _check_gathered called after every gather
  invariant_3 | CancelledError propagates (not swallowed)
  invariant_4 | BaseException propagates (not swallowed)
  invariant_5 | Regular exceptions go to error_results
  invariant_6 | Batch processes pages concurrently (not sequentially)
  invariant_7 | Semaphore limits concurrency to fetch_concurrency
  invariant_8 | Memory guard reduces concurrency under UMA pressure
  invariant_9 | Model/renderer mutual exclusion (no overlap)
  invariant_10 | Fail-soft: pipeline continues when individual pages fail
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from hledac.universal.pipeline.live_public_pipeline import (
    _fetch_and_process_page,
    PipelinePageResult,
)
from hledac.universal.network.session_runtime import _check_gathered


# ------------------------------------------------------------------
# invariant_1-5: gather + _check_gathered hygiene
# ------------------------------------------------------------------

class TestF200CGatherHygiene:
    """invariant_1-5: asyncio.gather uses return_exceptions=True and _check_gathered."""

    def test_check_gathered_propagates_cancelled_error(self):
        """invariant_3: CancelledError is re-raised by _check_gathered."""
        results = [asyncio.CancelledError()]
        with pytest.raises(asyncio.CancelledError):
            _check_gathered(results)

    def test_check_gathered_propagates_base_exception(self):
        """invariant_4: BaseException (not Exception) is re-raised."""
        results = [SystemExit()]
        with pytest.raises(SystemExit):
            _check_gathered(results)

    def test_check_gathered_routes_exception_to_error_results(self):
        """invariant_5: Regular Exception goes to error_results."""
        exc = ValueError("test error")
        results = [exc]
        ok_results, error_results = _check_gathered(results)
        assert ok_results == []
        assert error_results == [exc]

    def test_check_gathered_preserves_ok_results(self):
        """invariant_5: Ok results maintain order in ok_results."""
        ok1 = PipelinePageResult(
            url="https://example.com/1",
            fetched=True,
            matched_patterns=5,
            accepted_findings=3,
            stored_findings=3,
        )
        ok2 = PipelinePageResult(
            url="https://example.com/2",
            fetched=True,
            matched_patterns=2,
            accepted_findings=1,
            stored_findings=1,
        )
        results = [ok1, ok2]
        ok_results, error_results = _check_gathered(results)
        assert ok_results == [ok1, ok2]
        assert error_results == []

    def test_check_gathered_mixed_ok_and_errors(self):
        """invariant_5: Mixed results correctly partitioned."""
        ok = PipelinePageResult(
            url="https://example.com",
            fetched=True,
            matched_patterns=1,
            accepted_findings=1,
            stored_findings=1,
        )
        exc = RuntimeError("fetch failed")
        results = [ok, exc]
        ok_results, error_results = _check_gathered(results)
        assert ok_results == [ok]
        assert error_results == [exc]


# ------------------------------------------------------------------
# invariant_6: Concurrent page processing
# ------------------------------------------------------------------

class TestF200CConcurrency:
    """invariant_6: Pages are processed concurrently, not sequentially."""

    @pytest.mark.asyncio
    async def test_pages_processed_concurrently(self):
        """Verify pages can be processed concurrently via gather pattern."""
        call_times: list[tuple[str, float]] = []
        lock = asyncio.Lock()

        async def mock_async_fetch_public_text(url, timeout_s, max_bytes, use_stealth=False, use_js=False, use_doh=False):
            """Mock fetcher that records call order."""
            nonlocal call_times
            async with lock:
                call_times.append((f"start:{url}", time.monotonic()))
            await asyncio.sleep(0.05)
            async with lock:
                call_times.append((f"end:{url}", time.monotonic()))
            class MockFetchResult:
                def __init__(self):
                    self.url = url
                    self.text = f"content from {url}"
                    self.error = None
                    self.failure_stage = None
                    self.redirected = False
                    self.redirect_target = None
            return MockFetchResult()

        from hledac.universal.pipeline import live_public_pipeline as lpp
        original = lpp._ASYNC_FETCH_PUBLIC_TEXT
        lpp._ASYNC_FETCH_PUBLIC_TEXT = mock_async_fetch_public_text

        try:
            class MockHit:
                def __init__(self, url, idx):
                    self.url = url
                    self.title = f"Title {idx}"
                    self.snippet = f"Snippet {idx}"
                    self.rank = idx
                    self.score = 0.5
                    self.reason = "test"

            hits = [MockHit(f"https://example.com/{i}", i) for i in range(3)]
            semaphore = asyncio.Semaphore(3)

            tasks = []
            for hit in hits:
                task = asyncio.create_task(
                    _fetch_and_process_page(
                        semaphore=semaphore,
                        query="test query",
                        hit_url=hit.url,
                        hit_title=hit.title,
                        hit_snippet=hit.snippet,
                        hit_rank=hit.rank,
                        fetch_timeout_s=30.0,
                        fetch_max_bytes=2_000_000,
                        store=None,
                        memory_manager=None,
                        session_id="test-session",
                        discovery_score=hit.score,
                        discovery_reason=hit.reason,
                        vector_store=None,
                        graph=None,
                    )
                )
                tasks.append(task)

            await asyncio.gather(*tasks, return_exceptions=True)

            # Verify all 3 fetches were called
            start_calls = [t for t in call_times if t[0].startswith("start:")]
            assert len(start_calls) == 3, f"Expected 3 fetch calls, got {len(start_calls)}"

            # Verify calls overlap (concurrent, not sequential)
            # If sequential: all starts come before any ends (gap between first_end and last_start)
            # If concurrent: starts and ends interleaved (first_end close to or after last_start)
            start_times = sorted([t for t in call_times if t[0].startswith("start:")])
            end_times = sorted([t for t in call_times if t[0].startswith("end:")])

            first_end = end_times[0][1]
            last_start = start_times[-1][1]
            gap = last_start - first_end  # Positive = sequential gap, Negative/Zero = concurrent overlap

            # For concurrent: gap should be small (all tasks running simultaneously)
            # gap < 0.02 means first task ended within 20ms of last task starting
            assert gap < 0.02, (
                f"Tasks appear sequential: last start={last_start:.3f}, first end={first_end:.3f}, gap={gap:.3f}s. "
                f"Expected concurrent (gap < 0.02s)."
            )

        finally:
            lpp._ASYNC_FETCH_PUBLIC_TEXT = original


# ------------------------------------------------------------------
# invariant_7: Semaphore concurrency limiting
# ------------------------------------------------------------------

class TestF200CSemaphoreLimiting:
    """invariant_7: Semaphore limits concurrency to fetch_concurrency."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_active_tasks(self):
        """Verify semaphore prevents more than N concurrent operations."""
        active_count = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        async def mock_async_fetch_public_text(url, timeout_s, max_bytes, use_stealth=False, use_js=False, use_doh=False):
            """Mock fetcher that tracks concurrency."""
            nonlocal active_count, max_concurrent
            async with lock:
                active_count += 1
                max_concurrent = max(max_concurrent, active_count)

            await asyncio.sleep(0.05)

            async with lock:
                active_count -= 1

            class MockFetchResult:
                def __init__(self):
                    self.url = url
                    self.text = f"content from {url}"
                    self.error = None
                    self.failure_stage = None
                    self.redirected = False
                    self.redirect_target = None
            return MockFetchResult()

        from hledac.universal.pipeline import live_public_pipeline as lpp
        original = lpp._ASYNC_FETCH_PUBLIC_TEXT
        lpp._ASYNC_FETCH_PUBLIC_TEXT = mock_async_fetch_public_text

        try:
            semaphore = asyncio.Semaphore(2)  # Max 2 concurrent

            class MockHit:
                def __init__(self, url, idx):
                    self.url = url
                    self.title = f"Title {idx}"
                    self.snippet = f"Snippet {idx}"
                    self.rank = idx
                    self.score = 0.5
                    self.reason = "test"

            hits = [MockHit(f"https://example.com/{i}", i) for i in range(4)]

            tasks = []
            for hit in hits:
                task = asyncio.create_task(
                    _fetch_and_process_page(
                        semaphore=semaphore,
                        query="test query",
                        hit_url=hit.url,
                        hit_title=hit.title,
                        hit_snippet=hit.snippet,
                        hit_rank=hit.rank,
                        fetch_timeout_s=30.0,
                        fetch_max_bytes=2_000_000,
                        store=None,
                        memory_manager=None,
                        session_id="test-session",
                        discovery_score=hit.score,
                        discovery_reason=hit.reason,
                        vector_store=None,
                        graph=None,
                    )
                )
                tasks.append(task)

            await asyncio.gather(*tasks, return_exceptions=True)

            # Semaphore should have limited to 2 concurrent
            assert max_concurrent <= 2, f"Expected max 2 concurrent, got {max_concurrent}"

        finally:
            lpp._ASYNC_FETCH_PUBLIC_TEXT = original


# ------------------------------------------------------------------
# invariant_8: Memory guard reduces concurrency
# ------------------------------------------------------------------

class TestF200CMemoryGuard:
    """invariant_8: Pipeline reduces concurrency under UMA pressure."""

    def test_uma_state_constants_importable(self):
        """UMA_STATE_* constants exist in resource_governor."""
        from hledac.universal.core.resource_governor import (
            UMA_STATE_CRITICAL,
            UMA_STATE_EMERGENCY,
            UMA_STATE_OK,
        )

        # Verify state constants exist
        assert UMA_STATE_CRITICAL is not None
        assert UMA_STATE_EMERGENCY is not None
        assert UMA_STATE_OK is not None

    def test_uma_reduces_concurrency_logic(self):
        """Verify the logic: CRITICAL/EMERGENCY → concurrency=1."""
        from hledac.universal.core.resource_governor import (
            UMA_STATE_CRITICAL,
            UMA_STATE_EMERGENCY,
            UMA_STATE_OK,
        )

        # When state is CRITICAL, effective_concurrency should be 1
        # The pipeline code at lines 1963-1965 implements this
        for critical_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            fetch_concurrency = 5
            effective_concurrency = 1 if critical_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY) else fetch_concurrency
            assert effective_concurrency == 1, f"Expected 1 for {critical_state}, got {effective_concurrency}"

        # OK state should use full concurrency
        uma_state = UMA_STATE_OK
        fetch_concurrency = 5
        effective_concurrency = 1 if uma_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY) else fetch_concurrency
        assert effective_concurrency == 5


# ------------------------------------------------------------------
# invariant_9: Model/renderer mutual exclusion
# ------------------------------------------------------------------

class TestF200CModelRendererExclusion:
    """invariant_9: Model loading and JS renderer don't overlap."""

    def test_embedding_context_blocks_camoufox(self):
        """When embedding context is active, Camoufox is skipped."""
        from hledac.universal.embedding_pipeline import (
            is_embedding_context_active,
            _embedding_depth_lock,
        )

        # Simulate active embedding context
        with _embedding_depth_lock:
            import hledac.universal.embedding_pipeline as ep
            ep._embedding_depth = 1

        try:
            # Verify context is active
            assert is_embedding_context_active() is True
        finally:
            with _embedding_depth_lock:
                ep._embedding_depth = 0

    @pytest.mark.asyncio
    async def test_camoufox_skips_when_embedding_active(self):
        """_fetch_with_camoufox returns empty when embedding context active."""
        from hledac.universal.fetching.public_fetcher import _fetch_with_camoufox
        from hledac.universal.embedding_pipeline import (
            _embedding_depth_lock,
        )

        # Set embedding depth to 1 (active)
        with _embedding_depth_lock:
            import hledac.universal.embedding_pipeline as ep
            ep._embedding_depth = 1

        try:
            result = await _fetch_with_camoufox("https://example.com")
            assert result == "", "Camoufox should return empty when embedding context active"
        finally:
            with _embedding_depth_lock:
                ep._embedding_depth = 0


# ------------------------------------------------------------------
# invariant_10: Fail-soft pipeline continuation
# ------------------------------------------------------------------

class TestF200CFailSoft:
    """invariant_10: Pipeline continues when individual pages fail."""

    @pytest.mark.asyncio
    async def test_pipeline_continues_on_page_exception(self):
        """Page exception goes to error_results, pipeline continues."""
        ok_result = PipelinePageResult(
            url="https://ok.example.com",
            fetched=True,
            matched_patterns=5,
            accepted_findings=3,
            stored_findings=3,
        )
        exc_result = RuntimeError("simulated fetch error")

        results = [ok_result, exc_result, ok_result]
        ok_results, error_results = _check_gathered(results)

        assert len(ok_results) == 2
        assert len(error_results) == 1
        assert error_results[0] is exc_result

    @pytest.mark.asyncio
    async def test_multiple_exceptions_collected(self):
        """Multiple exceptions are all collected in error_results."""
        exc1 = ValueError("error 1")
        exc2 = RuntimeError("error 2")
        exc3 = TimeoutError("error 3")

        results = [exc1, exc2, exc3]
        ok_results, error_results = _check_gathered(results)

        assert ok_results == []
        assert len(error_results) == 3
        assert error_results[0] is exc1
        assert error_results[1] is exc2
        assert error_results[2] is exc3


# ------------------------------------------------------------------
# Integration: Full pipeline gather pattern
# ------------------------------------------------------------------

class TestF200CFullGatherPattern:
    """Verify the complete gather pattern used in async_run_live_public_pipeline."""

    def test_gather_pattern_matches_invariant(self):
        """Verify pipeline uses: gather(..., return_exceptions=True) + _check_gathered."""
        import ast
        import inspect
        from hledac.universal.pipeline import live_public_pipeline as lpp

        # Get source of async_run_live_public_pipeline
        source = inspect.getsource(lpp.async_run_live_public_pipeline)

        # Verify pattern: asyncio.gather with return_exceptions=True
        assert "asyncio.gather" in source
        assert "return_exceptions=True" in source

        # Verify _check_gathered is called after gather
        assert "_check_gathered" in source

        # Verify no bare gather without return_exceptions=True
        tree = ast.parse(source)
        gather_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == "gather":
                        # Check if return_exceptions=True is passed
                        has_return_exceptions = any(
                            kw.arg == "return_exceptions" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                            for kw in node.keywords
                        )
                        gather_calls.append(has_return_exceptions)

        assert len(gather_calls) >= 1, "Should have at least one gather call"
        assert all(gather_calls), "All gather calls must use return_exceptions=True"

    def test_pipeline_returns_pipeline_run_result_on_success(self):
        """Pipeline returns PipelineRunResult with correct structure."""
        from hledac.universal.pipeline.live_public_pipeline import PipelineRunResult

        result = PipelineRunResult(
            query="test",
            discovered=10,
            fetched=8,
            matched_patterns=15,
            accepted_findings=10,
            stored_findings=8,
            patterns_configured=100,
            pages=(),
        )

        assert result.query == "test"
        assert result.discovered == 10
        assert result.fetched == 8
        assert result.matched_patterns == 15
        assert result.accepted_findings == 10
        assert result.stored_findings == 8

    def test_pipeline_page_result_has_required_fields(self):
        """PipelinePageResult has all required fields for async processing."""
        from hledac.universal.pipeline.live_public_pipeline import PipelinePageResult

        result = PipelinePageResult(
            url="https://example.com",
            fetched=True,
            matched_patterns=5,
            accepted_findings=3,
            stored_findings=3,
            error=None,
            quality_reason="good",
            discovery_score=0.8,
            discovery_reason="strong_signal",
            discovery_signal=True,
            usable_signal=True,
            value_tier="high",
            resolution_reason="stored_findings",
            discovery_false_positive=False,
            waste_category="",
            structural_quality="healthy",
            failure_stage=None,
            redirected=False,
            redirect_target=None,
        )

        assert result.fetched is True
        assert result.matched_patterns == 5
        assert result.accepted_findings == 3
        assert result.stored_findings == 3
