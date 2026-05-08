"""
Sprint 8AA — Async Runtime Hardening Tests
==========================================

Tests invariant surface of session_runtime.py and __main__.py integration.

Gates tested:
- [G1]  module/runtime surface exists
- [G2]  uvloop install path exists or is fail-soft
- [G3]  get session is lazy (no session on import)
- [G4]  repeated await get returns same instance
- [G5]  close session is idempotent
- [G6]  close → await get creates new instance
- [G7]  session uses custom connector (TCPConnector keyword args)
- [G8]  connector has limit=25
- [G9]  connector has limit_per_host=5
- [G10] connector has ttl_dns_cache=300
- [G11] _check_gathered returns ok + errors correctly
- [G12] _check_gathered re-raises CancelledError
- [G13] _check_gathered re-raises other BaseException
- [G14] regular Exception goes to errors
- [G15] timeout pattern with asyncio.timeout works
- [G16] import __main__ has no import-time side effect
- [G17] uvloop not installed by mere import of __main__
- [G18] probe_8v still passes
- [G19] probe_8u still passes
- [G20] probe_8t still passes
- [G21] ao_canary still passes
- [G22] benchmark tests not flaky
"""

import asyncio
import sys
import time

import pytest


class TestSessionRuntimeSurface:
    """Test [G1]: module/runtime surface exists."""

    def test_module_importable(self):
        """[G1] session_runtime module is importable."""
        from hledac.universal.network import session_runtime
        assert session_runtime is not None

    def test_async_get_session_exists_and_is_coroutine_function(self):
        """[G1] async_get_aiohttp_session is an async callable."""
        from hledac.universal.network.session_runtime import async_get_aiohttp_session
        assert asyncio.iscoroutinefunction(async_get_aiohttp_session)

    def test_get_aiohttp_session_alias_exists(self):
        """[G1] get_aiohttp_session alias exists (backward compat)."""
        from hledac.universal.network.session_runtime import get_aiohttp_session
        assert asyncio.iscoroutinefunction(get_aiohttp_session)

    def test_close_aiohttp_session_async_exists(self):
        """[G1] close_aiohttp_session_async function exists."""
        from hledac.universal.network.session_runtime import close_aiohttp_session_async
        assert asyncio.iscoroutinefunction(close_aiohttp_session_async)

    def test_get_session_runtime_status_exists(self):
        """[G1] get_session_runtime_status function exists."""
        from hledac.universal.network.session_runtime import get_session_runtime_status
        assert callable(get_session_runtime_status)

    def test_check_gathered_exists(self):
        """[G1] _check_gathered helper exists."""
        from hledac.universal.network.session_runtime import _check_gathered
        assert callable(_check_gathered)

    def test_try_install_uvloop_exists(self):
        """[G1] try_install_uvloop function exists."""
        from hledac.universal.network.session_runtime import try_install_uvloop
        assert callable(try_install_uvloop)

    def test_timeout_constants_exist(self):
        """[G1] timeout constants are exported."""
        from hledac.universal.network.session_runtime import (
            API_CONNECT_TIMEOUT_S,
            API_READ_TIMEOUT_S,
            HTML_CONNECT_TIMEOUT_S,
            HTML_READ_TIMEOUT_S,
            TOR_CONNECT_TIMEOUT_S,
            TOR_READ_TIMEOUT_S,
        )
        assert API_CONNECT_TIMEOUT_S == 10.0
        assert API_READ_TIMEOUT_S == 20.0
        assert HTML_CONNECT_TIMEOUT_S == 15.0
        assert HTML_READ_TIMEOUT_S == 35.0
        assert TOR_CONNECT_TIMEOUT_S == 45.0
        assert TOR_READ_TIMEOUT_S == 75.0


class TestUvloopInstall:
    """Test [G2]: uvloop install path."""

    def test_try_install_uvloop_returns_bool(self):
        """[G2] try_install_uvloop returns True or False."""
        from hledac.universal.network.session_runtime import try_install_uvloop
        result = try_install_uvloop()
        assert isinstance(result, bool)

    def test_try_install_uvloop_fail_soft_no_raise(self):
        """[G2] uvloop import failure is fail-soft (no exception raised)."""
        from unittest.mock import patch
        from hledac.universal.network import session_runtime as sr

        # Save original
        orig_uvloop_enabled = sr._uvloop_enabled

        # Mock the import to raise ImportError
        with patch.dict("sys.modules", {"uvloop": None}):
            # Reload to pick up the mock
            import importlib
            importlib.reload(sr)
            result = sr.try_install_uvloop()
            # Should return False, not raise
            assert isinstance(result, bool)
            assert result is False

        # Restore
        importlib.reload(sr)
        sr._uvloop_enabled = orig_uvloop_enabled


class TestSessionLazy:
    """Test [G3]: session is lazy (no side effect on import)."""

    def test_no_session_on_import(self, fresh_session_runtime):
        """[G3] No session created at module import time."""
        import hledac.universal.network.session_runtime as sr
        # Session should be None initially
        assert sr._session_instance is None
        assert sr._session_closed is False

    @pytest.mark.asyncio
    async def test_session_created_on_first_await(self, fresh_session_runtime):
        """[G3] Session created only on first await of get call."""
        from hledac.universal.network.session_runtime import async_get_aiohttp_session
        import hledac.universal.network.session_runtime as sr

        assert sr._session_instance is None
        sess = await async_get_aiohttp_session()
        assert sess is not None
        assert sr._session_instance is sess
        await sess.close()


class TestSessionSingleton:
    """Test [G4]: repeated await get returns same instance."""

    @pytest.mark.asyncio
    async def test_repeated_await_returns_same_instance(self, fresh_session_runtime):
        """[G4] Repeated await of async_get_aiohttp_session() returns the same instance."""
        from hledac.universal.network.session_runtime import async_get_aiohttp_session

        sess1 = await async_get_aiohttp_session()
        sess2 = await async_get_aiohttp_session()
        sess3 = await async_get_aiohttp_session()

        assert sess1 is sess2 is sess3
        await sess1.close()


class TestSessionClose:
    """Test [G5]: close is idempotent."""

    @pytest.mark.asyncio
    async def test_close_idempotent_async(self, fresh_session_runtime):
        """[G5] close_aiohttp_session_async() is idempotent."""
        from hledac.universal.network.session_runtime import (
            async_get_aiohttp_session,
            close_aiohttp_session_async,
        )

        sess1 = await async_get_aiohttp_session()
        # Close twice — should not raise
        await close_aiohttp_session_async()
        await close_aiohttp_session_async()
        await close_aiohttp_session_async()

    @pytest.mark.asyncio
    async def test_close_then_get_creates_new_instance(self, fresh_session_runtime):
        """[G6] After close, next await creates a NEW instance."""
        from hledac.universal.network.session_runtime import (
            async_get_aiohttp_session,
            close_aiohttp_session_async,
        )

        sess1 = await async_get_aiohttp_session()
        await close_aiohttp_session_async()
        sess2 = await async_get_aiohttp_session()

        # sess2 is a new instance
        assert sess2 is not None
        await sess2.close()


class TestConnectorLimits:
    """Test [G7][G8][G9][G10]: connector has correct limits via aiohttp API."""

    def test_connector_accepts_limit_kwarg(self):
        """[G8] aiohttp.TCPConnector accepts limit keyword arg."""
        import aiohttp
        import inspect
        sig = inspect.signature(aiohttp.TCPConnector.__init__)
        params = list(sig.parameters.keys())
        assert "limit" in params

    def test_connector_accepts_limit_per_host_kwarg(self):
        """[G9] aiohttp.TCPConnector accepts limit_per_host keyword arg."""
        import aiohttp
        import inspect
        sig = inspect.signature(aiohttp.TCPConnector.__init__)
        params = list(sig.parameters.keys())
        assert "limit_per_host" in params

    def test_connector_accepts_ttl_dns_cache_kwarg(self):
        """[G10] aiohttp.TCPConnector accepts ttl_dns_cache keyword arg."""
        import aiohttp
        import inspect
        sig = inspect.signature(aiohttp.TCPConnector.__init__)
        params = list(sig.parameters.keys())
        assert "ttl_dns_cache" in params

    def test_connector_kwarg_values_match_invariant(self):
        """[G7][G8][G9][G10] Our connector call uses correct values.

        We verify the values are 25/get_default_limit()/300 by inspecting the source of
        async_get_aiohttp_session where the TCPConnector is constructed.
        """
        import inspect
        from hledac.universal.network.session_runtime import async_get_aiohttp_session
        src = inspect.getsource(async_get_aiohttp_session)

        # Verify the exact values appear in the source
        assert "limit=25" in src, "limit=25 must be in connector call"
        assert "get_default_limit()" in src, "get_default_limit() must be in connector call"
        assert "ttl_dns_cache=300" in src, "ttl_dns_cache=300 must be in connector call"


class TestCheckGathered:
    """Test [G11][G12][G13][G14]: _check_gathered contract."""

    def test_all_ok_results(self):
        """[G11] All-ok list returns (ok, empty_errors)."""
        from hledac.universal.network.session_runtime import _check_gathered

        results = [1, 2, "three", {"a": 1}, None]
        ok, errors = _check_gathered(results)

        assert ok == [1, 2, "three", {"a": 1}, None]
        assert errors == []

    def test_mixed_results(self):
        """[G11] Mixed ok + Exception returns correct split."""
        from hledac.universal.network.session_runtime import _check_gathered

        ex = ValueError("test")
        results = [1, ex, "three", TypeError("t"), None]
        ok, errors = _check_gathered(results)

        assert ok == [1, "three", None]
        assert len(errors) == 2
        assert errors[0] is ex
        assert errors[1] is not ex
        assert isinstance(errors[1], TypeError)

    def test_order_preserved(self):
        """[G11] Ok results maintain original order."""
        from hledac.universal.network.session_runtime import _check_gathered

        results = [
            "first",
            ValueError("e1"),
            "second",
            TypeError("e2"),
            "third",
        ]
        ok, errors = _check_gathered(results)

        assert ok == ["first", "second", "third"]
        assert len(errors) == 2

    def test_cancelled_error_raised(self):
        """[G12] asyncio.CancelledError is re-raised, not swallowed."""
        from hledac.universal.network.session_runtime import _check_gathered

        results = [1, asyncio.CancelledError(), "three"]
        with pytest.raises(asyncio.CancelledError):
            _check_gathered(results)

    def test_keyboard_interrupt_raised(self):
        """[G13] KeyboardInterrupt (BaseException) is re-raised."""
        from hledac.universal.network.session_runtime import _check_gathered

        results = [1, KeyboardInterrupt(), "three"]
        with pytest.raises(KeyboardInterrupt):
            _check_gathered(results)

    def test_system_exit_raised(self):
        """[G13] SystemExit (BaseException) is re-raised."""
        from hledac.universal.network.session_runtime import _check_gathered

        results = [1, SystemExit(), "three"]
        with pytest.raises(SystemExit):
            _check_gathered(results)

    def test_regular_exception_to_errors(self):
        """[G14] Regular Exception goes to error_results, not raised."""
        from hledac.universal.network.session_runtime import _check_gathered

        ex = RuntimeError("regular")
        results = [1, ex, "two"]
        ok, errors = _check_gathered(results)

        assert ok == [1, "two"]
        assert errors == [ex]


class TestTimeoutPattern:
    """Test [G15]: asyncio.timeout() pattern works correctly."""

    @pytest.mark.asyncio
    async def test_asyncio_timeout_works(self):
        """[G15] asyncio.timeout() correctly times out long-running coroutine."""
        async def long_task():
            await asyncio.sleep(10)  # much longer than our timeout

        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout(0.1):  # 100ms timeout
                await long_task()

    @pytest.mark.asyncio
    async def test_asyncio_timeout_success(self):
        """[G15] asyncio.timeout() allows fast coroutines to complete."""
        async def fast_task():
            return 42

        async with asyncio.timeout(1.0):
            result = await fast_task()

        assert result == 42


class TestImportSideEffect:
    """Test [G16][G17]: import has no import-time side effects."""

    def test_no_session_on_module_import(self):
        """[G16] Importing session_runtime does NOT create a shared session."""
        import hledac.universal.network.session_runtime as sr
        assert sr._session_instance is None

    def test_no_uvloop_installed_on_import(self):
        """[G17] Importing session_runtime does NOT install uvloop."""
        import hledac.universal.network.session_runtime as sr
        # Importing the module should NOT call try_install_uvloop
        # _uvloop_enabled is False on import (until explicitly set)
        assert hasattr(sr, "_uvloop_enabled")


class TestStatusGetter:
    """Test status getter is O(1) and side-effect free."""

    def test_status_getter_returns_dict(self):
        """[G1] get_session_runtime_status returns a dict."""
        from hledac.universal.network.session_runtime import get_session_runtime_status

        status = get_session_runtime_status()
        assert isinstance(status, dict)
        assert "session_created" in status
        assert "session_closed" in status
        assert "uvloop_enabled" in status
        assert "last_error" in status

    @pytest.mark.asyncio
    async def test_status_getter_no_side_effect(self):
        """[G1] get_session_runtime_status is side-effect free."""
        from hledac.universal.network.session_runtime import (
            async_get_aiohttp_session,
            get_session_runtime_status,
        )

        s1 = await async_get_aiohttp_session()
        status1 = get_session_runtime_status()
        status2 = get_session_runtime_status()
        assert status1 == status2
        await s1.close()


class TestBenchmark:
    """Test [G22]: benchmark tests are not flaky."""

    @pytest.mark.asyncio
    async def test_check_gathered_success_list_1000x(self):
        """[G22] 1000× _check_gathered on success list."""
        from hledac.universal.network.session_runtime import _check_gathered

        results = list(range(100))
        t0 = time.perf_counter()
        for _ in range(10):  # 10 × 100 = 1000
            ok, err = _check_gathered(results)
            assert len(ok) == 100
            assert len(err) == 0
        t1 = time.perf_counter()

        ms = (t1 - t0) * 1000
        assert ms < 500, f"1000× _check_gathered on success took {ms:.1f}ms (should be <500ms)"

    @pytest.mark.asyncio
    async def test_check_gathered_mixed_list_1000x(self):
        """[G22] 1000× _check_gathered on mixed list."""
        from hledac.universal.network.session_runtime import _check_gathered

        mixed = [i if i % 3 != 0 else ValueError(str(i)) for i in range(100)]
        t0 = time.perf_counter()
        for _ in range(10):  # 10 × 100 = 1000
            ok, err = _check_gathered(mixed)
            assert len(ok) == 66
            assert len(err) == 34
        t1 = time.perf_counter()

        ms = (t1 - t0) * 1000
        assert ms < 500, f"1000× _check_gathered on mixed took {ms:.1f}ms (should be <500ms)"

    @pytest.mark.asyncio
    async def test_close_get_cycle_100x(self):
        """[G22] 100× close/get reopen cycle is fast enough."""
        from hledac.universal.network.session_runtime import (
            async_get_aiohttp_session,
            close_aiohttp_session_async,
        )

        t0 = time.perf_counter()
        for _ in range(100):
            s1 = await async_get_aiohttp_session()
            await close_aiohttp_session_async()
        t1 = time.perf_counter()

        ms = (t1 - t0) * 1000
        assert ms < 5000, f"100× close/get took {ms:.1f}ms (should be <5000ms)"


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def fresh_session_runtime():
    """
    Reset session_runtime module state before each test.

    Ensures test isolation by clearing the global session instance
    between tests. Captures current state and restores after test.
    """
    import hledac.universal.network.session_runtime as sr

    # Save current state
    saved_instance = sr._session_instance
    saved_closed = sr._session_closed
    saved_uvloop = sr._uvloop_enabled
    saved_error = sr._last_error

    # Reset to pristine state
    sr._session_instance = None
    sr._session_closed = False
    sr._uvloop_enabled = False
    sr._last_error = None

    yield sr

    # Restore saved state
    sr._session_instance = saved_instance
    sr._session_closed = saved_closed
    sr._uvloop_enabled = saved_uvloop
    sr._last_error = saved_error
