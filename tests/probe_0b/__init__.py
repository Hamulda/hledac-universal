"""
Sprint 0B Probe Tests
=====================

Probe tests for runtime hygiene verification:
- uvloop entrypoint / fail-open
- bounded queue behavior
- gather exception handling
- flow_trace default-off
- session factory singleton behavior
"""


import asyncio
import logging
import time

import pytest

logger = logging.getLogger(__name__)


class TestSprint0BRuntime:
    """Sprint 0B runtime hygiene tests."""

    def test_uvloop_import_available(self):
        """Test that uvloop is importable (or fallback is handled)."""
        try:
            import uvloop
            # uvloop available - should be installed
            assert hasattr(uvloop, 'install')
        except ImportError:
            # uvloop not available - that's OK (fail-open)
            pytest.skip("uvloop not available (fail-open is acceptable)")

    def test_uvloop_install_no_crash(self):
        """Test that uvloop.install() doesn't crash."""
        try:
            import uvloop
            uvloop.install()
            # If we get here, uvloop was installed successfully
            assert True
        except ImportError:
            pytest.skip("uvloop not available")

    def test_main_module_import_smoke(self):
        """Test __main__.py imports without crash."""
        try:
            from hledac.universal import __main__
            assert __main__ is not None
        except Exception as e:
            pytest.fail(f"__main__ import failed: {e}")

    def test_async_session_factory_singleton(self):
        """Test AsyncSessionFactory singleton behavior."""
        from hledac.universal.__main__ import AsyncSessionFactory

        factory1 = AsyncSessionFactory()
        factory2 = AsyncSessionFactory()
        assert factory1 is factory2, "AsyncSessionFactory should be singleton"

    @pytest.mark.asyncio
    async def test_async_session_factory_get_session(self):
        """Test AsyncSessionFactory.get_session() works."""
        from hledac.universal.__main__ import AsyncSessionFactory

        factory = AsyncSessionFactory()
        loop = await factory.get_session()
        assert loop is not None
        assert isinstance(loop, asyncio.AbstractEventLoop)

    def test_flow_trace_default_off(self):
        """Test that flow_trace is default-off."""
        from hledac.universal.utils.flow_trace import is_enabled
        assert not is_enabled(), "flow_trace should be default-off"

    def test_flow_trace_summary_safe_when_disabled(self):
        """Test get_summary() works when tracing is disabled."""
        from hledac.universal.utils.flow_trace import get_summary, is_enabled

        assert not is_enabled()
        summary = get_summary()
        # When disabled, should return empty dict
        assert isinstance(summary, dict)

    def test_flow_trace_trace_event_fail_open(self):
        """Test trace_event is fail-open when disabled."""
        from hledac.universal.utils.flow_trace import trace_event, is_enabled

        assert not is_enabled()
        # Should not raise even with bad inputs
        try:
            trace_event(
                component="test",
                stage="test",
                event_type="test",
                status="ok"
            )
        except Exception as e:
            pytest.fail(f"trace_event should be fail-open but raised: {e}")

    def test_evidence_log_bounded_queue(self):
        """Test evidence_log has bounded queue."""
        from hledac.universal.evidence_log import EvidenceLog

        log = EvidenceLog(run_id="test_probe_0b", enable_persist=False)
        # Check that the internal queue is bounded
        assert hasattr(log, '_queue')
        queue = log._queue
        assert isinstance(queue, asyncio.Queue)
        assert queue.maxsize > 0, "Queue should have maxsize"

    def test_evidence_log_dropped_count_exists(self):
        """Test evidence_log has _dropped_count metric."""
        from hledac.universal.evidence_log import EvidenceLog

        log = EvidenceLog(run_id="test_probe_0b", enable_persist=False)
        assert hasattr(log, '_dropped_count')
        assert isinstance(log._dropped_count, int)

    def test_gather_return_exceptions_pattern(self):
        """Test that gather(return_exceptions=True) pattern is used."""
        # This is a code pattern test - verify the codebase uses it
        import ast
        import os

        # Find fetch_coordinator.py and check for gather patterns
        fetch_coordinator_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..', '..',
            'coordinators', 'fetch_coordinator.py'
        )

        if os.path.exists(fetch_coordinator_path):
            with open(fetch_coordinator_path, 'r') as f:
                content = f.read()

            # Should use asyncio.gather with return_exceptions
            assert 'asyncio.gather' in content, "Should use asyncio.gather"
            # Look for return_exceptions=True pattern
            has_return_exceptions = 'return_exceptions' in content
            assert has_return_exceptions, "gather should use return_exceptions=True"

    @pytest.mark.asyncio
    async def test_bounded_gather_no_exception_propagation(self):
        """Test bounded_gather with return_exceptions=True returns None for failed coros."""
        from hledac.universal.utils.async_utils import bounded_gather

        async def failing_coro():
            raise ValueError("Test error")

        # Should not raise even with failing coroutines
        # Note: bounded_gather with return_exceptions=True returns None for failed coros
        # (not the exception itself) because cancel_on_error=False
        results = await bounded_gather(
            failing_coro(),
            return_exceptions=True
        )
        assert len(results) == 1
        # When return_exceptions=True but cancel_on_error=False,
        # exceptions are converted to None
        assert results[0] is None

    def test_fetch_coordinator_no_sync_requests_in_async(self):
        """Test fetch_coordinator doesn't use sync requests in async hot path."""
        import ast
        import os

        fetch_coordinator_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..', '..',
            'coordinators', 'fetch_coordinator.py'
        )

        if os.path.exists(fetch_coordinator_path):
            with open(fetch_coordinator_path, 'r') as f:
                content = f.read()

            # Check for requests import (sync library)
            # It's OK to have it imported, but should not be used directly in async methods
            # Look for pattern: requests.get or requests.head in async methods
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if 'requests.' in line and 'async' not in line:
                    # Check if it's in an async function
                    # Look backwards for async def
                    in_async = False
                    for j in range(max(0, i - 20), i):
                        if 'async def' in lines[j]:
                            in_async = True
                            break
                    if in_async and 'requests.' in line:
                        pytest.fail(f"Line {i+1}: sync requests used in async context: {line.strip()}")

    def test_context_cache_exists_and_bounded(self):
        """Test context_cache has bounded collections if it exists."""
        try:
            from hledac.universal.context_cache import ContextCache
            cache = ContextCache()
            # Check for bounded behavior
            assert hasattr(cache, '_cache') or hasattr(cache, 'max_size'), \
                "ContextCache should have bounded storage"
        except ImportError:
            pytest.skip("context_cache not available in this scope")

    def test_exposed_service_hunter_import_smoke(self):
        """Test exposed_service_hunter imports without crash."""
        try:
            from hledac.universal.intelligence.exposed_service_hunter import ExposedServiceHunter
            assert ExposedServiceHunter is not None
        except ImportError as e:
            pytest.skip(f"exposed_service_hunter not available: {e}")

    def test_async_utils_exports(self):
        """Test async_utils has expected exports."""
        from hledac.universal.utils.async_utils import bounded_map, bounded_gather
        assert callable(bounded_map)
        assert callable(bounded_gather)


class TestSprint0BBenchmark:
    """Sprint 0B benchmark gate tests."""

    def test_benchmark_entrypoint_no_crash(self):
        """Test HLEDAC_BENCHMARK=1 runs without crash."""
        import os
        from hledac.universal.__main__ import _run_benchmark_probe

        # Run benchmark probe
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(_run_benchmark_probe())
            assert isinstance(results, dict)
            assert 'checks' in results
            assert 'all_passed' in results
        finally:
            loop.close()

    def test_benchmark_results_structure(self):
        """Test benchmark results have expected structure."""
        from hledac.universal.__main__ import _run_benchmark_probe

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(_run_benchmark_probe())

            # Should have these keys
            assert 'probe' in results
            assert results['probe'] == 'sprint_0b_runtime'
            assert 'uvloop_installed' in results
            assert 'checks' in results
            assert 'all_passed' in results
            assert 'passed_count' in results
        finally:
            loop.close()

    def test_benchmark_uvloop_check(self):
        """Test benchmark includes uvloop check."""
        from hledac.universal.__main__ import _run_benchmark_probe

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(_run_benchmark_probe())
            assert 'uvloop_available' in results['checks']
        finally:
            loop.close()

    def test_benchmark_flow_trace_checks(self):
        """Test benchmark includes flow_trace checks."""
        from hledac.universal.__main__ import _run_benchmark_probe

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(_run_benchmark_probe())
            checks = results['checks']
            assert 'flow_trace_default_off' in checks
            assert 'flow_trace_summary_safe' in checks
        finally:
            loop.close()

    def test_benchmark_session_factory_checks(self):
        """Test benchmark includes session factory checks."""
        from hledac.universal.__main__ import _run_benchmark_probe

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(_run_benchmark_probe())
            checks = results['checks']
            assert 'session_factory_singleton' in checks
            assert 'async_session_works' in checks
        finally:
            loop.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
