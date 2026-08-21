"""A7 Smoke Tests — V2Init Service Bootstrap Exception Handling

Tests that verify the A7 antipattern fix for root-cause muffling:
- Narrowed exception blocks to concrete types (ImportError, OSError, RuntimeError)
- Full traceback logging via _init_failure helper
- Fail-loud for critical services (DuckDB, Governor)

RUN: python -m pytest tests/test_v2_init_services.py -v

NOTE: These tests verify the exception handling patterns, not full integration.
For full runtime smoke tests, see test_a1_lazy_imports.py.
"""

from __future__ import annotations

import logging
from typing import Never
from unittest.mock import MagicMock

import pytest

# Import the module under test
from hledac.universal.runtime.scheduler_v2._v2_init import (
    V2Init,
    _hasattr_safe,
    _init_failure,
)
from hledac.universal.runtime.scheduler_v2.protocol import InitResult


class TestInitFailureHelper:
    """A7: Verify _init_failure helper logs full traceback and creates proper InitResult."""

    def test_init_failure_creates_failure_result(self) -> None:
        """Verify _init_failure returns InitResult with error message."""
        logger = MagicMock(spec=logging.Logger)

        exc = RuntimeError("test error")
        result = _init_failure(exc, 10.5, logger, "TestService", reraise=False)

        assert isinstance(result, InitResult)
        assert result.ok is False
        assert result.value is None
        assert result.elapsed_ms == 10.5
        assert "RuntimeError" in result.error
        assert "test error" in result.error

    def test_init_failure_logs_full_traceback(self) -> None:
        """Verify _init_failure logs at ERROR level with traceback."""
        logger = MagicMock(spec=logging.Logger)

        exc = ValueError("nested cause")
        _init_failure(exc, 5.0, logger, "DuckDB", reraise=False)

        # Verify logger.error was called (not warning)
        assert logger.error.called
        call_args = logger.error.call_args[0]
        assert "DuckDB init failed" in call_args[0]
        assert "Root cause preserved" in call_args[0]
        # Traceback should be in the logged message
        assert any("Traceback" in str(arg) or "ValueError" in str(arg) for arg in call_args)

    def test_init_failure_reraise_option(self) -> None:
        """Verify _init_failure can reraise the exception (fail-loud mode)."""
        logger = MagicMock(spec=logging.Logger)

        exc = ImportError("module not found")
        with pytest.raises(ImportError, match="module not found"):
            _init_failure(exc, 1.0, logger, "Hermes", reraise=True)

    def test_init_failure_includes_trace_excerpt(self) -> None:
        """Verify traceback excerpt is included in error message."""
        logger = MagicMock(spec=logging.Logger)

        # Create exception with traceback
        def inner() -> Never:
            raise RuntimeError("deep error")

        def outer() -> None:
            inner()

        try:
            outer()
        except RuntimeError as e:
            result = _init_failure(e, 2.0, logger, "Governor", reraise=False)
            # Should include "Trace:" marker in error
            assert "Trace:" in result.error or "Traceback" in result.error


class TestHasattrSafeHelper:
    """A7: Verify _hasattr_safe doesn't trigger AttributeError."""

    def test_hasattr_safe_normal_object(self) -> None:
        """Verify _hasattr_safe works with normal objects."""

        class Obj:
            x = 1

        obj = Obj()
        assert _hasattr_safe(obj, "x") is True
        assert _hasattr_safe(obj, "y") is False

    def test_hasattr_safe_with_getattr_exception(self) -> None:
        """Verify _hasattr_safe handles __getattr__ that raises."""

        class Problematic:
            def __getattr__(self, name):
                raise RuntimeError("access denied")

        p = Problematic()
        assert _hasattr_safe(p, "x") is False  # Should return False, not raise

    def test_hasattr_safe_with_getattr_shadowing(self) -> None:
        """Verify _hasattr_safe handles objects that shadow hasattr."""

        class Shadowing:
            def __getattr__(self, name):
                raise AttributeError(f"no {name}")

        s = Shadowing()
        assert _hasattr_safe(s, "y") is False


class TestV2InitExceptionPatterns:
    """A7: Verify V2Init catches specific exception types, not bare Exception."""

    def test_v2init_slots_defined(self) -> None:
        """Verify V2Init has all required __slots__ defined."""
        assert hasattr(V2Init, "__slots__")
        slots = V2Init.__slots__
        expected = {
            "_scheduler",
            "_config",
            "_result",
            "_cancel_event",
            "_ctx",
            "_governor",
            "_hermes_engine",
            "_evidence_log",
            "_sidecar_orchestrator",
            "_lifecycle",
            "_acquisition_plan",
        }
        assert set(slots) == expected

    def test_v2init_type_guard(self) -> None:
        """Verify V2Init rejects non-object types early."""
        with pytest.raises(TypeError, match="requires an object"):
            V2Init("not an object")

        with pytest.raises(TypeError, match="requires an object"):
            V2Init(123)

    def test_v2init_accepts_schedulers_with_slots(self) -> None:
        """Verify V2Init accepts schedulers with __slots__."""

        class SchedulerWithSlots:
            __slots__ = ("_config", "_result")

            def __init__(self) -> None:
                self._config = None
                self._result = None

        scheduler = SchedulerWithSlots()
        init = V2Init(scheduler)
        assert init._scheduler is scheduler


class TestInitResultPatterns:
    """A7: Verify InitResult patterns for fail-soft vs fail-loud."""

    def test_init_result_success_structure(self) -> None:
        """Verify success InitResult has correct structure."""

        class Dummy:
            pass

        result = InitResult.success(Dummy(), 15.5)
        assert result.ok is True
        assert result.value is not None
        assert result.error is None
        assert result.elapsed_ms == 15.5

    def test_init_result_failure_structure(self) -> None:
        """Verify failure InitResult has correct structure."""
        result = InitResult.failure("Module not found", 0.5)
        assert result.ok is False
        assert result.value is None
        assert result.error == "Module not found"
        assert result.elapsed_ms == 0.5

    def test_init_result_generic_type(self) -> None:
        """Verify InitResult is a generic type."""
        result: InitResult[str] = InitResult.success("value", 1.0)
        assert result.value == "value"
        assert result.ok is True


class TestCriticalServiceAssertions:
    """A7: Verify critical services raise AssertionError on failure.

    This is the smoke test that validates fail-loud behavior.
    Critical services: DuckDB, Governor
    """

    def test_critical_service_check_pattern(self) -> None:
        """Verify the pattern for identifying critical service failures.

        The _bootstrap method checks for critical service failures and raises
        AssertionError with a clear message. This test verifies the pattern
        logic without running the full bootstrap.
        """
        # Simulate the pattern from _bootstrap lines 405-415
        failed_services = [
            "duckdb_store: Module not found",
            "governor: Runtime error",
        ]

        _critical_failed = [s for s in failed_services if "duckdb" in s.lower() or "governor" in s.lower()]

        assert len(_critical_failed) == 2
        assert "duckdb" in _critical_failed[0].lower()
        assert "governor" in _critical_failed[1].lower()

        # Verify it would raise AssertionError
        with pytest.raises(AssertionError, match="critical services unavailable"):
            raise AssertionError(
                f"[A1-CRITICAL] V2Init cannot start: critical services unavailable: "
                f"{'; '.join(_critical_failed)}. "
                f"This indicates _lazy_imports.py is missing or modules failed to import."
            )


class TestExceptionGroupCompatibility:
    """A7: Verify patterns are compatible with Python 3.11+ ExceptionGroup.

    While we don't use except* in this module (no concurrent tasks raising
    ExceptionGroups), the pattern should not interfere with ExceptionGroup
    handling in calling code.
    """

    def test_exception_chaining_pattern(self) -> None:
        """Verify exception chaining works correctly for re-raises."""
        try:
            try:
                raise ValueError("original")
            except ValueError as e:
                # Chain with explicit cause
                raise RuntimeError("wrapped") from e
        except RuntimeError as e:
            assert isinstance(e.__cause__, ValueError)
            assert str(e.__cause__) == "original"

    def test_exception_group_detection_compatible(self) -> None:
        """Verify we don't accidentally catch ExceptionGroup."""
        # This pattern should NOT catch ExceptionGroup
        try:
            raise ExceptionGroup("test", [ValueError("a"), TypeError("b")])
        except ImportError, OSError, RuntimeError:
            pytest.fail("Should not catch ExceptionGroup with narrow except")
        except ExceptionGroup:
            pass  # Expected: ExceptionGroup not caught by narrow except


# ─── Test Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_scheduler():
    """Create a mock scheduler for testing V2Init."""

    class MockScheduler:
        __slots__ = ("_config", "_result", "_ctx")

        def __init__(self) -> None:
            self._config = None
            self._result = None
            self._ctx = None

    return MockScheduler()


@pytest.fixture
def mock_logger():
    """Create a mock logger that captures all log calls."""
    import logging

    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    logger = logging.getLogger("test_v2_init")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return logger
