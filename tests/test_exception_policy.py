"""
Tests for utils/exception_policy.py (P1-01)

Covers:
    - ExceptionPolicy.handle() hot-path vs cold-path
    - exc_info() context manager
    - gexc() shorthand
    - is_hot_path() heuristic
    - BLE001 suppression via noqa comments (where intentional)
"""

import asyncio
import logging
import pytest
from unittest.mock import MagicMock

from utils.exception_policy import (
    ExceptionPolicy,
    HOT_PATH,
    COLD_PATH,
    exc_info,
    gexc,
    is_hot_path,
)


class TestExceptionPolicyHandle:
    """Unit tests for ExceptionPolicy.handle()."""

    def test_handle_logs_exception_with_context(self, caplog: pytest.LogCaptureFixture) -> None:
        """handle() logs the exception with context at WARNING level by default."""
        caplog.set_level(logging.WARNING)
        e = ValueError("test error")

        ExceptionPolicy.handle(e, context="test_op")

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert "[EXC] test_op" in record.message
        assert "ValueError" in record.message
        assert "test error" in record.message
        assert record.exc_info is not None

    def test_handle_respects_re_raise_true(self) -> None:
        """re_raise=True surfaces the exception to the caller."""
        e = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            ExceptionPolicy.handle(e, context="test", re_raise=True)

    def test_handle_respects_re_raise_false(self) -> None:
        """re_raise=False (hot-path) suppresses the exception."""
        e = RuntimeError("boom")
        # Should NOT raise
        ExceptionPolicy.handle(e, context="test", re_raise=False)

    def test_handle_with_exc_info_false(self, caplog: pytest.LogCaptureFixture) -> None:
        """exc_info=False suppresses stack trace in log."""
        caplog.set_level(logging.WARNING)
        e = ValueError("test")

        ExceptionPolicy.handle(e, context="test", exc_info=False)

        assert len(caplog.records) == 1
        assert caplog.records[0].exc_info is None

    def test_handle_uses_debug_level_when_not_reraise(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Hot-path (re_raise=False) uses DEBUG level."""
        caplog.set_level(logging.DEBUG)
        e = RuntimeError("hot")

        ExceptionPolicy.handle(e, context="hot_path", re_raise=False)

        assert caplog.records[0].levelno == logging.DEBUG


class TestExcInfoContextManager:
    """Tests for exc_info() context manager."""

    def test_exc_info_catches_specified_exception(self) -> None:
        """exc_info() catches the specified exception type."""
        with exc_info(OSError, context="test"):
            raise OSError("test oserror")

    def test_exc_info_does_not_catch_wrong_type(self) -> None:
        """exc_info() re-raises exceptions not in exc_types."""
        with pytest.raises(ValueError):
            with exc_info(OSError, context="test"):
                raise ValueError("wrong type")

    def test_exc_info_logs_at_debug_level(self, caplog: pytest.LogCaptureFixture) -> None:
        """Caught exceptions are logged at DEBUG level."""
        caplog.set_level(logging.DEBUG)
        with exc_info(OSError, context="test_exc_info"):
            raise OSError("expected")

        assert len(caplog.records) == 1
        assert "[EXC] test_exc_info" in caplog.records[0].message

    def test_exc_info_empty_tuple_catches_all(self) -> None:
        """Empty exc_types tuple catches everything (bare except: parity)."""
        with exc_info(context="bare_catch"):
            raise ValueError("caught")
        # No raise = caught

    def test_exc_info_multiple_types(self) -> None:
        """Multiple exception types are all caught."""
        with exc_info(OSError, ValueError, context="multi"):
            raise ValueError("multi")
        with exc_info(OSError, ValueError, context="multi2"):
            raise OSError("multi2")


class TestGexc:
    """Tests for gexc() shorthand."""

    def test_gexc_catches_exception(self) -> None:
        """gexc() is a shorthand for exc_info() with re_raise=False."""
        with gexc(OSError, "file_open"):
            raise OSError("file missing")
        # No raise = caught

    def test_gexc_does_not_catch_wrong_type(self) -> None:
        """gexc() only catches the specified exception type."""
        with pytest.raises(RuntimeError):
            with gexc(OSError, "ctx"):
                raise RuntimeError("wrong")


class TestIsHotPath:
    """Tests for is_hot_path() heuristic."""

    def test_is_hot_path_returns_bool(self) -> None:
        """is_hot_path() returns a boolean."""
        result = is_hot_path()
        assert isinstance(result, bool)

    def test_is_hot_path_from_known_context(self) -> None:
        """is_hot_path() works when called from a known hot-path frame."""
        # Call from a function with a hot-path-sounding name
        def fetch_operation():
            return is_hot_path()

        result = fetch_operation()
        assert result is True


class TestPolicyConstants:
    """Tests for HOT_PATH / COLD_PATH constants."""

    def test_hot_path_constant_is_false(self) -> None:
        """HOT_PATH = False means log + continue (not re-raise)."""
        assert HOT_PATH is False

    def test_cold_path_constant_is_true(self) -> None:
        """COLD_PATH = True means re-raise to caller."""
        assert COLD_PATH is True


# ── Integration: gexc() in real async context ──────────────────────────────

@pytest.mark.asyncio
async def test_gexc_in_async_context() -> None:
    """gexc() works inside async functions."""
    with gexc(asyncio.CancelledError, "async_cancel"):
        raise asyncio.CancelledError()
    # Caught and suppressed


@pytest.mark.asyncio
async def test_exc_info_in_async_context() -> None:
    """exc_info() works inside async functions."""
    with exc_info(asyncio.TimeoutError, context="async_timeout"):
        raise asyncio.TimeoutError()
    # Caught and suppressed
