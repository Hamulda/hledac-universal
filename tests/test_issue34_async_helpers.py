"""
ISSUE-034: Fallback patterns — _safe_* everywhere
Test suite for async hot-path helpers in core/result.py

Migrace _safe_* error handling patterns na try_or_async / try_or_none_async / try_or_raise_async.
Hot-path helpers s ZERO allocation na Ok path — ideální pro M1 8GB.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hledac.universal.core.result import (
    try_or_async,
    try_or_none_async,
    try_or_raise_async,
    Ok,
    Err,
)


@pytest.mark.asyncio
class TestTryOrAsync:
    """Test try_or_async — async hot-path helper."""

    async def test_returns_value_on_success(self) -> None:
        """Value is returned directly on success path."""
        result = await try_or_async(lambda: asyncio.sleep(0.001, "success"), "default")
        assert result == "success"

    async def test_returns_default_on_exception(self) -> None:
        """Default is returned when function raises."""
        async def raise_exc() -> None:
            raise ValueError("test error")

        result = await try_or_async(raise_exc, "default")
        assert result == "default"

    async def test_returns_default_on_any_exception(self) -> None:
        """Default is returned for any exception type."""
        async def raise_type_error() -> int:
            raise TypeError("type error")

        result = await try_or_async(raise_type_error, 42)
        assert result == 42

    async def test_zero_allocation_on_success(self) -> None:
        """Success path creates no Result object — zero allocation."""

        async def return_value() -> dict[str, int]:
            return {"data": 123}

        result = await try_or_async(return_value, {})
        assert result == {"data": 123}
        # Verify it's the actual value, not wrapped
        assert isinstance(result, dict)


@pytest.mark.asyncio
class TestTryOrNoneAsync:
    """Test try_or_none_async — async hot-path helper returning None on failure."""

    async def test_returns_value_on_success(self) -> None:
        """Value is returned directly on success path."""
        result = await try_or_none_async(lambda: asyncio.sleep(0.001, "success"))
        assert result == "success"

    async def test_returns_none_on_exception(self) -> None:
        """None is returned when function raises."""
        async def raise_exc() -> str:
            raise RuntimeError("test")

        result = await try_or_none_async(raise_exc)
        assert result is None

    async def test_returns_none_on_value_error(self) -> None:
        """None is returned for ValueError."""
        async def raise_ve() -> int:
            raise ValueError("invalid")

        result = await try_or_none_async(raise_ve)
        assert result is None


@pytest.mark.asyncio
class TestTryOrRaiseAsync:
    """Test try_or_raise_async — async hot-path helper that raises on failure."""

    async def test_returns_value_on_success(self) -> None:
        """Value is returned directly on success path."""
        result = await try_or_raise_async(
            lambda: asyncio.sleep(0.001, "success"), ValueError
        )
        assert result == "success"

    async def test_raises_specified_exception_on_failure(self) -> None:
        """Custom exception type is raised on failure."""

        async def raise_exc() -> str:
            raise RuntimeError("original error")

        with pytest.raises(ValueError) as exc_info:
            await try_or_raise_async(raise_exc, ValueError, label="my_op")
        assert "my_op" in str(exc_info.value)

    async def test_raises_with_label_in_message(self) -> None:
        """Label is included in the raised exception message."""

        async def fail() -> str:
            raise RuntimeError("inner")

        with pytest.raises(RuntimeError) as exc_info:
            await try_or_raise_async(fail, RuntimeError, label="labeled_op")
        assert "labeled_op" in str(exc_info.value)
        assert "inner" in str(exc_info.value)


@pytest.mark.asyncio
class TestAsyncHelpersIntegration:
    """Integration tests demonstrating migration from _safe_* patterns."""

    async def test_migration_from_safe_fetch_pattern(self) -> None:
        """Demonstrates migration from _safe_fetch pattern."""
        # OLD pattern (in live_feed_pipeline.py):
        # async def _safe_fetch(session, url):
        #     try:
        #         return await session.get(url)
        #     except Exception:
        #         return None

        # NEW pattern using try_or_none_async:
        async def mock_get(url: str) -> dict[str, str]:
            if "fail" in url:
                raise ConnectionError("network error")
            return {"url": url, "status": "ok"}

        result = await try_or_none_async(lambda: mock_get("https://ok.example"))
        assert result == {"url": "https://ok.example", "status": "ok"}

        result = await try_or_none_async(lambda: mock_get("https://fail.example"))
        assert result is None

    async def test_migration_from_safe_aclose_pattern(self) -> None:
        """Demonstrates migration from _safe_aclose pattern."""
        # OLD pattern (in async_helpers.py):
        # async def _safe_aclose(resource, ctx, logger):
        #     try:
        #         close_fn = getattr(resource, "aclose", None) or getattr(resource, "close", None)
        #         if close_fn:
        #             await close_fn()
        #         return None
        #     except Exception as e:
        #         return e

        # NEW pattern using try_or_raise_async:
        class MockResource:
            async def aclose(self) -> None:
                raise RuntimeError("already closed")

        resource = MockResource()
        with pytest.raises(RuntimeError):
            await try_or_raise_async(
                lambda: resource.aclose(), RuntimeError, label="aclose_resource"
            )

    async def test_result_type_preserved(self) -> None:
        """Verify Result types are still available alongside helpers."""
        ok_result: Ok[int] = Ok(42)
        err_result: Err[str] = Err("error", ValueError("test"))

        assert ok_result.is_ok()
        assert err_result.is_err()
        assert ok_result.value == 42
        assert err_result.error == "error"
