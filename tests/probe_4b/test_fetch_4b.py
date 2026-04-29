"""
Sprint 4B: Fetch Runtime Discipline — Timeout Matrix, Concurrency Matrix, AIMD
================================================================================

Tests verify:
1. Timeout matrix values are correct and actually consumed
2. Concurrency matrix values are correct
3. AIMD increase/decrease works
4. Gather paths log exceptions properly (return_exceptions=True)
5. Clean shutdown has drain
6. No sync requests in async paths
7. Import/boot regression
"""

import asyncio
import pytest
import sys
from collections import deque

# Ensure the module path is correct
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


class TestTimeoutMatrix:
    """Test Sprint 4B: Timeout matrix constants are correct and consumed."""

    def test_timeout_constants_defined(self):
        """Verify timeout matrix constants exist with correct values."""
        from hledac.universal.coordinators.fetch_coordinator import (
            TIMEOUT_CLEARNET_API,
            TIMEOUT_CLEARNET_HTML,
            TIMEOUT_TOR,
            TIMEOUT_I2P,
        )
        assert TIMEOUT_CLEARNET_API == 20.0
        assert TIMEOUT_CLEARNET_HTML == 35.0
        assert TIMEOUT_TOR == 75.0
        assert TIMEOUT_I2P == 150.0

    def test_tor_fetch_uses_timeout_matrix(self):
        """Verify _fetch_with_tor uses TIMEOUT_TOR constant (75s)."""
        import inspect
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        source = inspect.getsource(FetchCoordinator._get_tor_session)
        # The session should be created with timeout=aiohttp.ClientTimeout(total=TIMEOUT_TOR)
        assert 'TIMEOUT_TOR' in source, "TIMEOUT_TOR should be used in _get_tor_session"


class TestConcurrencyMatrix:
    """Test Sprint 4B: Concurrency matrix constants are correct."""

    def test_concurrency_constants_defined(self):
        """Verify concurrency matrix constants exist with correct values."""
        from hledac.universal.coordinators.fetch_coordinator import (
            CONCURRENCY_TOR,
            CONCURRENCY_CLEARNET,
            CONCURRENCY_API,
            CONCURRENCY_GLOBAL_MAX,
        )
        assert CONCURRENCY_TOR == 4
        assert CONCURRENCY_CLEARNET == 12
        assert CONCURRENCY_API == 5
        assert CONCURRENCY_GLOBAL_MAX == 25

    def test_tor_max_sessions_uses_concurrency_matrix(self):
        """Verify _tor_max_sessions is set from CONCURRENCY_TOR."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator, CONCURRENCY_TOR
        fc = FetchCoordinator()
        assert fc._tor_max_sessions == CONCURRENCY_TOR


class TestAIMDController:
    """Test Sprint 4B: AIMD adaptive concurrency controller."""

    def test_aimd_initial_state(self):
        """Verify AIMD starts at CONCURRENCY_CLEARNET."""
        from hledac.universal.coordinators.fetch_coordinator import (
            FetchCoordinator, CONCURRENCY_CLEARNET
        )

        fc = FetchCoordinator()
        assert fc._aimd_concurrency == float(CONCURRENCY_CLEARNET)
        assert fc._aimd_successes == 0
        assert fc._aimd_failures == 0

    def test_aimd_success_increases_window(self):
        """Verify additive increase after AIMD_SUCCESS_THRESHOLD successes."""
        from hledac.universal.coordinators.fetch_coordinator import (
            FetchCoordinator, AIMD_ADDITIVE_INCREMENT,
            AIMD_SUCCESS_THRESHOLD, AIMD_MAX_CONCURRENCY
        )

        fc = FetchCoordinator()
        initial = fc._aimd_concurrency

        # Hit success threshold
        for _ in range(AIMD_SUCCESS_THRESHOLD):
            fc._aimd_release_success()

        # Should have increased by AIMD_ADDITIVE_INCREMENT
        expected = min(initial + AIMD_ADDITIVE_INCREMENT, AIMD_MAX_CONCURRENCY)
        assert fc._aimd_concurrency == expected

    def test_aimd_failure_decreases_window(self):
        """Verify multiplicative decrease on failure."""
        from hledac.universal.coordinators.fetch_coordinator import (
            FetchCoordinator, AIMD_DECREASE_FACTOR, AIMD_MIN_CONCURRENCY
        )

        fc = FetchCoordinator()
        initial = fc._aimd_concurrency

        # Single failure should trigger decrease
        fc._aimd_release_failure()

        expected = max(initial * AIMD_DECREASE_FACTOR, AIMD_MIN_CONCURRENCY)
        assert fc._aimd_concurrency == expected

    def test_aimd_reset_on_failure(self):
        """Verify success counter resets after failure."""
        from hledac.universal.coordinators.fetch_coordinator import (
            FetchCoordinator, AIMD_SUCCESS_THRESHOLD
        )

        fc = FetchCoordinator()
        # Accumulate some successes
        for _ in range(AIMD_SUCCESS_THRESHOLD - 1):
            fc._aimd_release_success()
        assert fc._aimd_successes == AIMD_SUCCESS_THRESHOLD - 1

        # Now a failure
        fc._aimd_release_failure()
        assert fc._aimd_successes == 0

    def test_aimd_respect_max_ceiling(self):
        """Verify AIMD never exceeds AIMD_MAX_CONCURRENCY."""
        from hledac.universal.coordinators.fetch_coordinator import (
            FetchCoordinator, AIMD_MAX_CONCURRENCY, AIMD_SUCCESS_THRESHOLD
        )

        fc = FetchCoordinator()
        fc._aimd_concurrency = float(AIMD_MAX_CONCURRENCY - 1)

        for _ in range(AIMD_SUCCESS_THRESHOLD * 10):
            fc._aimd_release_success()

        assert fc._aimd_concurrency <= AIMD_MAX_CONCURRENCY

    def test_aimd_respect_min_floor(self):
        """Verify AIMD never goes below AIMD_MIN_CONCURRENCY."""
        from hledac.universal.coordinators.fetch_coordinator import (
            FetchCoordinator, AIMD_MIN_CONCURRENCY
        )

        fc = FetchCoordinator()
        fc._aimd_concurrency = float(AIMD_MIN_CONCURRENCY)

        for _ in range(10):
            fc._aimd_release_failure()

        assert fc._aimd_concurrency >= AIMD_MIN_CONCURRENCY

    def test_aimd_acquire_creates_semaphore(self):
        """Verify _aimd_acquire lazily creates semaphore."""
        from hledac.universal.coordinators.fetch_coordinator import (
            FetchCoordinator, CONCURRENCY_CLEARNET
        )

        fc = FetchCoordinator()
        assert fc._aimd_semaphore is None

        # Simulate async context
        async def run():
            window = await fc._aimd_acquire()
            assert window == float(CONCURRENCY_CLEARNET)
            assert fc._aimd_semaphore is not None
            return True

        result = asyncio.run(run())
        assert result

    @pytest.mark.asyncio
    async def test_aimd_telemetry_updated(self):
        """Verify telemetry state is updated on success/failure."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        fc = FetchCoordinator()
        initial_successes = fc._telemetry['total_successes']
        initial_failures = fc._telemetry['total_failures']

        fc._aimd_release_success()
        assert fc._telemetry['total_successes'] == initial_successes + 1

        fc._aimd_release_failure()
        assert fc._telemetry['total_failures'] == initial_failures + 1


class TestGatherHygiene:
    """Test Sprint 4B: Gather paths use return_exceptions=True and log exceptions."""

    def test_deep_research_uses_return_exceptions(self):
        """Verify _maybe_deep_research uses asyncio.gather with return_exceptions=True."""
        import inspect
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        source = inspect.getsource(FetchCoordinator._maybe_deep_research)
        assert 'return_exceptions=True' in source, (
            "_maybe_deep_research must use return_exceptions=True in gather"
        )

    def test_deep_research_logs_exceptions(self):
        """Verify _maybe_deep_research has explicit exception logging."""
        import inspect
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        source = inspect.getsource(FetchCoordinator._maybe_deep_research)
        # Should have logger.debug for failed parts
        assert 'logger.debug' in source, (
            "Gather exceptions should be logged, not silently swallowed"
        )

    def test_preview_fetch_has_exception_handling(self):
        """Verify HTML preview fetch has try/except for hygiene."""
        import inspect
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        source = inspect.getsource(FetchCoordinator._fetch_url)
        # The _async_fetch_preview lambda should be inside try/except
        assert 'except asyncio.TimeoutError' in source or 'except Exception' in source


class TestCleanShutdown:
    """Test Sprint 4B: Shutdown has proper drain."""

    def test_shutdown_has_drain(self):
        """Verify _do_shutdown includes asyncio.sleep for drain."""
        import inspect
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        source = inspect.getsource(FetchCoordinator._do_shutdown)
        # Should have drain sleep
        assert 'asyncio.sleep' in source, "Shutdown should have drain delay"
        assert '0.25' in source, "Drain should be ~250ms"

    def test_shutdown_closes_tor_sessions(self):
        """Verify _do_shutdown closes Tor sessions."""
        import inspect
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        source = inspect.getsource(FetchCoordinator._do_shutdown)
        assert 'close()' in source


class TestNoSyncInAsync:
    """Test Sprint 4B: No blocking sync calls in async paths."""

    def test_validate_fetch_target_offloads_dns(self):
        """Verify _validate_fetch_target uses async DNS (loop.getaddrinfo or to_thread)."""
        import inspect
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        source = inspect.getsource(FetchCoordinator._validate_fetch_target)
        # Accept either async-native loop.getaddrinfo or thread-based to_thread
        uses_async_dns = 'async_getaddrinfo' in source or 'asyncio.to_thread' in source
        assert uses_async_dns, (
            "DNS resolution must use async_getaddrinfo (loop.getaddrinfo) or asyncio.to_thread"
        )

    def test_resolve_host_ips_is_sync(self):
        """Verify _resolve_host_ips is a regular function (not async)."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        fc = FetchCoordinator()
        # Should be a regular method, not coroutine
        result = fc._resolve_host_ips('example.com')
        assert isinstance(result, list)


class TestImportBoot:
    """Test Sprint 4B: Import/boot regression tests."""

    def test_import_fetch_coordinator(self):
        """Verify fetch_coordinator module imports without errors."""
        from hledac.universal.coordinators import fetch_coordinator
        assert hasattr(fetch_coordinator, 'FetchCoordinator')

    def test_fetch_coordinator_instantiates(self):
        """Verify FetchCoordinator can be instantiated."""
        from hledac.universal.coordinators.fetch_coordinator import (
            FetchCoordinator, FetchCoordinatorConfig
        )

        config = FetchCoordinatorConfig()
        fc = FetchCoordinator(config=config)
        assert fc is not None
        assert isinstance(fc._frontier, deque)

    def test_telemetry_initialized(self):
        """Verify telemetry dict is initialized."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        fc = FetchCoordinator()
        assert 'aimd_concurrency' in fc._telemetry
        assert 'active_fetches' in fc._telemetry
        assert 'total_successes' in fc._telemetry
        assert 'total_failures' in fc._telemetry


class TestStepResultIncludesTelemetry:
    """Test Sprint 4B: Step result includes light telemetry."""

    def test_step_result_has_aimd_window(self):
        """Verify _get_step_result returns aimd_window."""
        import inspect
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        source = inspect.getsource(FetchCoordinator._get_step_result)
        assert 'aimd_window' in source, "_get_step_result should include aimd_window"

    def test_step_result_has_active_fetches(self):
        """Verify _get_step_result returns active_fetches count."""
        import inspect
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        source = inspect.getsource(FetchCoordinator._get_step_result)
        assert 'active_fetches' in source, "_get_step_result should include active_fetches"


class TestAIMDParameters:
    """Test Sprint 4B: AIMD parameters are exposed as module constants."""

    def test_aimd_constants_defined(self):
        """Verify AIMD_* constants all exist."""
        from hledac.universal.coordinators.fetch_coordinator import (
            AIMD_ADDITIVE_INCREMENT,
            AIMD_DECREASE_FACTOR,
            AIMD_MIN_CONCURRENCY,
            AIMD_MAX_CONCURRENCY,
            AIMD_SUCCESS_THRESHOLD,
        )
        assert AIMD_ADDITIVE_INCREMENT == 1
        assert AIMD_DECREASE_FACTOR == 0.75
        assert AIMD_MIN_CONCURRENCY == 1
        assert AIMD_MAX_CONCURRENCY == 25
        assert AIMD_SUCCESS_THRESHOLD == 3


if __name__ == '__main__':
    pytest.main([__file__, '-q'])
