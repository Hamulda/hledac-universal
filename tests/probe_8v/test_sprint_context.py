"""
Sprint 8V — sprint_context + exceptions + signal teardown
==========================================================

Tests cover:
- SprintContext datacontainer and ContextVar helpers
- Exception hierarchy
- __main__.py signal hookup
"""


import asyncio
import signal
import sys
import time
from unittest import mock

import pytest

# =============================================================================
# sprint_context tests
# =============================================================================


class TestSprintContext:
    """Tests for utils/sprint_context.py"""

    def test_set_and_get(self):
        from hledac.universal.utils.sprint_context import (
            SprintContext,
            get_sprint_context,
            set_sprint_context,
            clear_sprint_context,
        )

        ctx = SprintContext(
            sprint_id="8v",
            target="test",
            start_time=1234.5,
            phase="boot",
            transport="curl_cffi",
        )
        set_sprint_context(ctx)
        result = get_sprint_context()
        assert result is ctx
        assert result.sprint_id == "8v"
        assert result.target == "test"
        assert result.phase == "boot"
        assert result.transport == "curl_cffi"
        clear_sprint_context()

    def test_clear(self):
        from hledac.universal.utils.sprint_context import (
            SprintContext,
            get_sprint_context,
            set_sprint_context,
            clear_sprint_context,
        )

        ctx = SprintContext(sprint_id="8v", target="test", phase="boot")
        set_sprint_context(ctx)
        clear_sprint_context()
        assert get_sprint_context() is None

    def test_default_is_none(self):
        from hledac.universal.utils.sprint_context import (
            get_sprint_context,
            clear_sprint_context,
        )

        clear_sprint_context()
        assert get_sprint_context() is None

    def test_scope_restores_previous(self):
        from hledac.universal.utils.sprint_context import (
            SprintContext,
            get_sprint_context,
            set_sprint_context,
            clear_sprint_context,
            sprint_scope,
        )

        clear_sprint_context()
        ctx1 = SprintContext(sprint_id="outer", target="t", phase="boot")
        set_sprint_context(ctx1)

        ctx2 = SprintContext(sprint_id="inner", target="t", phase="active")
        with sprint_scope(ctx2):
            assert get_sprint_context() is ctx2
            assert get_sprint_context().sprint_id == "inner"
        # After scope exit, previous context is restored
        assert get_sprint_context() is ctx1

        clear_sprint_context()

    def test_nested_scope(self):
        from hledac.universal.utils.sprint_context import (
            SprintContext,
            get_sprint_context,
            clear_sprint_context,
            sprint_scope,
        )

        clear_sprint_context()
        outer = SprintContext(sprint_id="outer", target="t", phase="boot")
        middle = SprintContext(sprint_id="middle", target="t", phase="warmup")
        inner = SprintContext(sprint_id="inner", target="t", phase="active")

        with sprint_scope(outer):
            assert get_sprint_context().sprint_id == "outer"
            with sprint_scope(middle):
                assert get_sprint_context().sprint_id == "middle"
                with sprint_scope(inner):
                    assert get_sprint_context().sprint_id == "inner"
                assert get_sprint_context().sprint_id == "middle"
            assert get_sprint_context().sprint_id == "outer"

        assert get_sprint_context() is None

    def test_scope_exception_safe(self):
        from hledac.universal.utils.sprint_context import (
            SprintContext,
            get_sprint_context,
            set_sprint_context,
            clear_sprint_context,
            sprint_scope,
        )

        clear_sprint_context()
        outer = SprintContext(sprint_id="outer", target="t", phase="boot")
        set_sprint_context(outer)

        with pytest.raises(ValueError):
            with sprint_scope(SprintContext(sprint_id="inner", target="t", phase="active")):
                assert get_sprint_context().sprint_id == "inner"
                raise ValueError("boom")

        # outer context is restored even after exception
        assert get_sprint_context() is outer
        clear_sprint_context()

    def test_context_is_frozen(self):
        from hledac.universal.utils.sprint_context import SprintContext

        ctx = SprintContext(sprint_id="8v", target="test", phase="boot")
        with pytest.raises(Exception):  # frozen struct - any mutation attempt fails
            ctx.sprint_id = "changed"  # type: ignore

    def test_is_unfinished(self):
        from hledac.universal.utils.sprint_context import SprintContext

        ctx_boot = SprintContext(sprint_id="8v", target="t", phase="boot")
        ctx_export = SprintContext(sprint_id="8v", target="t", phase="export")
        ctx_teardown = SprintContext(sprint_id="8v", target="t", phase="teardown")

        assert ctx_boot.is_unfinished() is True
        assert ctx_export.is_unfinished() is False
        assert ctx_teardown.is_unfinished() is False

    def test_alias_equivalence(self):
        from hledac.universal.utils.sprint_context import (
            get_current_context,
            get_sprint_context,
        )

        assert get_sprint_context is get_current_context


# =============================================================================
# exceptions tests
# =============================================================================


class TestExceptionHierarchy:
    """Tests for utils/exceptions.py"""

    def test_base_catches_all(self):
        from hledac.universal.utils.exceptions import GhostBaseException

        for exc_cls in (
            GhostBaseException,
        ):
            assert isinstance(exc_cls(), GhostBaseException)

    def test_all_subclasses_instantiate(self):
        from hledac.universal.utils.exceptions import (
            GhostBaseException,
            TransportException,
            TimeoutException,
            ParseException,
            CheckpointCorruptException,
            SprintTimeoutException,
            BootstrapError,
            TeardownError,
            RuntimeInitError,
            SignalHandlingError,
        )

        classes = [
            GhostBaseException,
            TransportException,
            TimeoutException,
            ParseException,
            CheckpointCorruptException,
            SprintTimeoutException,
            BootstrapError,
            TeardownError,
            RuntimeInitError,
            SignalHandlingError,
        ]
        for cls in classes:
            exc = cls("test message")
            assert isinstance(exc, GhostBaseException)
            assert str(exc) == "test message"

    def test_hierarchy_sanity(self):
        from hledac.universal.utils.exceptions import (
            GhostBaseException,
            BootstrapError,
            TeardownError,
            RuntimeInitError,
            SignalHandlingError,
        )

        # All new exceptions inherit from GhostBaseException
        for cls in (BootstrapError, TeardownError, RuntimeInitError, SignalHandlingError):
            assert issubclass(cls, GhostBaseException)
            assert issubclass(cls, Exception)

    def test_context_payload(self):
        from hledac.universal.utils.exceptions import GhostBaseException

        exc = GhostBaseException("msg", {"sprint_id": "8v"})
        assert exc.args[0] == "msg"
        assert exc.args[1] == {"sprint_id": "8v"}


# =============================================================================
# __main__.py signal teardown tests
# =============================================================================


class TestSignalTeardown:
    """Tests for __main__.py signal hookup"""

    def test_signal_flag_function_exists(self):
        from hledac.universal.__main__ import (
            _get_and_clear_signal_flag,
            _install_signal_teardown,
        )

        assert callable(_get_and_clear_signal_flag)
        assert callable(_install_signal_teardown)

    def test_flag_default_false(self):
        from hledac.universal.__main__ import _get_and_clear_signal_flag

        # start fresh — flag should be False
        flag_val = _get_and_clear_signal_flag()
        assert flag_val is False

    def test_flag_set_and_clear(self):
        from hledac.universal.__main__ import _get_and_clear_signal_flag

        # Simulate signal setting the global
        import hledac.universal.__main__ as main_module

        main_module._signal_teardown_flag = True
        assert _get_and_clear_signal_flag() is True
        # After clear, should be False
        assert _get_and_clear_signal_flag() is False

    def test_install_signal_teardown_idempotent(self):
        import asyncio
        from hledac.universal.__main__ import _install_signal_teardown

        loop = asyncio.new_event_loop()
        try:
            # Should not raise
            _install_signal_teardown(loop)
            _install_signal_teardown(loop)  # idempotent call
        finally:
            loop.close()

    def test_handler_sets_flag_and_stops_loop(self):
        import asyncio
        from hledac.universal.__main__ import _install_signal_teardown

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            _install_signal_teardown(loop)

            # Get the handler that was registered
            import signal as signal_mod

            old_handler = signal_mod.getsignal(signal_mod.SIGINT)
            try:
                handler = signal_mod.getsignal(signal_mod.SIGINT)
                assert callable(handler)

                # Simulate SIGINT — should set flag and schedule loop.stop
                with mock.patch.object(loop, "call_soon_threadsafe") as mock_call_threadsafe:
                    handler(signal_mod.SIGINT, None)
                    # Verify flag was set
                    from hledac.universal.__main__ import _signal_teardown_flag

                    assert _signal_teardown_flag is True
                    # Verify loop.stop was called via call_soon_threadsafe
                    mock_call_threadsafe.assert_called_once()
                    args = mock_call_threadsafe.call_args[0]
                    # args[0] should be loop.stop (bound method)
                    assert callable(args[0])
            finally:
                signal_mod.signal(signal_mod.SIGINT, old_handler)
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass

    def test_signal_handler_does_not_use_signal_signal_as_main_async_path(self):
        # Verify the handler uses loop.call_soon_threadsafe, not awaiting
        # This is a documentation-invariant check
        import inspect
        from hledac.universal.__main__ import _install_signal_teardown

        source = inspect.getsource(_install_signal_teardown)
        # The preferred async pattern uses call_soon_threadsafe
        assert "call_soon_threadsafe" in source
        # signal.signal() is used but only for handler registration,
        # not as the primary async teardown mechanism

    def test_import_no_regression(self):
        # Import must not raise
        import hledac.universal.__main__  # noqa: F401

    def test_module_exports(self):
        from hledac.universal.__main__ import (
            AsyncSessionFactory,
            _install_signal_teardown,
            _get_and_clear_signal_flag,
            main,
        )

        assert callable(main)
        assert callable(_install_signal_teardown)
        assert callable(_get_and_clear_signal_flag)
        assert AsyncSessionFactory is not None


# =============================================================================
# benchmark tests
# =============================================================================


class TestSprintContextBenchmarks:
    """Lightweight benchmarks for sprint_context operations"""

    def test_set_get_clear_benchmark(self):
        from hledac.universal.utils.sprint_context import (
            SprintContext,
            get_sprint_context,
            set_sprint_context,
            clear_sprint_context,
        )

        ctx = SprintContext(sprint_id="bench", target="perf", phase="boot")
        iterations = 100_000

        t0 = time.perf_counter()
        for _ in range(iterations):
            set_sprint_context(ctx)
            get_sprint_context()
            clear_sprint_context()
        t1 = time.perf_counter()
        elapsed = t1 - t0
        ops = iterations * 3  # set+get+clear = 3 ops per iteration

        assert elapsed < 1.0, f"set/get/clear {ops} ops took {elapsed:.3f}s (>1s threshold)"
        print(f"\n  set/get/clear {ops} ops: {elapsed:.3f}s ({ops/elapsed:.0f} ops/s)")

    def test_scope_overhead(self):
        from hledac.universal.utils.sprint_context import (
            SprintContext,
            clear_sprint_context,
            sprint_scope,
        )

        ctx = SprintContext(sprint_id="bench", target="perf", phase="boot")
        iterations = 100_000

        t0 = time.perf_counter()
        for _ in range(iterations):
            with sprint_scope(ctx):
                pass
        t1 = time.perf_counter()
        elapsed = t1 - t0

        assert elapsed < 2.0, f"{iterations} scope ops took {elapsed:.3f}s (>2s threshold)"
        print(f"\n  {iterations} scope ops: {elapsed:.3f}s ({iterations/elapsed:.0f} ops/s)")

    def test_nested_scope_benchmark(self):
        from hledac.universal.utils.sprint_context import (
            SprintContext,
            clear_sprint_context,
            sprint_scope,
        )

        outer = SprintContext(sprint_id="o", target="t", phase="boot")
        inner = SprintContext(sprint_id="i", target="t", phase="active")
        iterations = 50_000

        t0 = time.perf_counter()
        for _ in range(iterations):
            with sprint_scope(outer):
                with sprint_scope(inner):
                    pass
        t1 = time.perf_counter()
        elapsed = t1 - t0

        assert elapsed < 2.0
        print(f"\n  {iterations} nested scope ops: {elapsed:.3f}s ({iterations/elapsed:.0f} ops/s)")


class TestSignalInstallBenchmark:
    """Benchmark for signal install/uninstall"""

    def test_install_overhead(self):
        import asyncio
        from hledac.universal.__main__ import _install_signal_teardown

        iterations = 1000
        loop = asyncio.new_event_loop()

        t0 = time.perf_counter()
        for _ in range(iterations):
            _install_signal_teardown(loop)
        t1 = time.perf_counter()
        elapsed = t1 - t0

        loop.close()
        assert elapsed < 1.0, f"{iterations} installs took {elapsed:.3f}s"
        print(f"\n  {iterations} signal installs: {elapsed:.3f}s ({iterations/elapsed:.0f} ops/s)")
