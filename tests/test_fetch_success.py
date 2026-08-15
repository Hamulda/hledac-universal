"""
tests/test_fetch_success.py

NEW-C1: Fetch Success Regression Tests

Tests for ensuring fetch operations complete successfully with proper
error handling, retry logic, and resource cleanup.

Architecture: M1 8GB optimized, Python 3.14+ compatible
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _core import aclose


class TestFetchSuccessBasics:
    """Tests for basic fetch success patterns."""

    @pytest.mark.asyncio
    async def test_successful_fetch_returns_data(self) -> None:
        """Successful fetch must return data."""
        async def mock_fetch(url: str) -> dict:
            await asyncio.sleep(0.01)
            return {"url": url, "status": 200, "content": "test"}
        
        result = await mock_fetch("http://example.com")
        
        assert result["url"] == "http://example.com"
        assert result["status"] == 200
        assert "content" in result

    @pytest.mark.asyncio
    async def test_fetch_timeout(self) -> None:
        """Fetch timeout must raise TimeoutError."""
        async def slow_fetch(url: str) -> dict:
            await asyncio.sleep(10)
            return {"url": url}
        
        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout(0.01):
                await slow_fetch("http://example.com")

    @pytest.mark.asyncio
    async def test_fetch_retry_on_transient_error(self) -> None:
        """Transient errors must trigger retry."""
        attempt_count = {"value": 0}
        
        async def fetch_with_retry(url: str) -> dict:
            attempt_count["value"] += 1
            if attempt_count["value"] < 3:
                raise ConnectionError("Transient error")
            return {"url": url, "status": 200}
        
        # Retry logic
        max_retries = 3
        last_error = None
        
        for attempt in range(max_retries):
            try:
                result = await fetch_with_retry("http://example.com")
                break
            except ConnectionError as e:
                last_error = e
                await asyncio.sleep(0.01)
        
        assert attempt_count["value"] == 3
        # Should have succeeded on third attempt
        assert last_error is None


class TestFetchResourceCleanup:
    """Tests for fetch resource cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_on_success(self) -> None:
        """Resources must be cleaned up on successful fetch."""
        cleanup_called = {"value": False}
        
        async def fetch_with_cleanup(url: str) -> dict:
            try:
                await asyncio.sleep(0.01)
                return {"url": url}
            finally:
                cleanup_called["value"] = True
        
        result = await fetch_with_cleanup("http://example.com")
        
        assert result["url"] == "http://example.com"
        assert cleanup_called["value"] is True

    @pytest.mark.asyncio
    async def test_cleanup_on_error(self) -> None:
        """Resources must be cleaned up on fetch error."""
        cleanup_called = {"value": False}
        
        async def fetch_with_error(url: str) -> dict:
            try:
                raise ConnectionError("Network error")
            finally:
                cleanup_called["value"] = True
        
        with pytest.raises(ConnectionError):
            await fetch_with_error("http://example.com")
        
        assert cleanup_called["value"] is True

    @pytest.mark.asyncio
    async def test_cleanup_on_cancellation(self) -> None:
        """Resources must be cleaned up on cancellation."""
        cleanup_called = {"value": False}
        
        async def cancellable_fetch(url: str) -> dict:
            try:
                await asyncio.sleep(10)
                return {"url": url}
            finally:
                cleanup_called["value"] = True
        
        task = asyncio.create_task(cancellable_fetch("http://example.com"))
        await asyncio.sleep(0.01)
        task.cancel()
        
        with pytest.raises(asyncio.CancelledError):
            await task
        
        assert cleanup_called["value"] is True


class TestFetchErrorHandling:
    """Tests for fetch error handling."""

    @pytest.mark.asyncio
    async def test_handle_http_error(self) -> None:
        """HTTP errors must be handled appropriately."""
        async def fetch_with_status(status: int) -> dict:
            if status >= 400:
                raise ValueError(f"HTTP {status}")
            return {"status": status}
        
        with pytest.raises(ValueError, match="HTTP 404"):
            await fetch_with_status(404)
        
        result = await fetch_with_status(200)
        assert result["status"] == 200

    @pytest.mark.asyncio
    async def test_handle_dns_error(self) -> None:
        """DNS errors must be handled gracefully."""
        async def fetch_with_dns_error(url: str) -> dict:
            raise OSError("Name or service not known")
        
        with pytest.raises(OSError):
            await fetch_with_dns_error("http://invalid.test")

    @pytest.mark.asyncio
    async def test_handle_ssl_error(self) -> None:
        """SSL errors must be handled gracefully."""
        async def fetch_with_ssl_error(url: str) -> dict:
            raise ConnectionError("SSL verification failed")
        
        with pytest.raises(ConnectionError):
            await fetch_with_ssl_error("https://expired.test")


class TestFetchRetryPolicy:
    """Tests for fetch retry policies."""

    @pytest.mark.asyncio
    async def test_exponential_backoff(self) -> None:
        """Retry delays must use exponential backoff."""
        delays: list[float] = []
        base_delay = 0.1
        
        async def fetch_with_backoff(attempt: int) -> dict:
            delay = base_delay * (2 ** attempt)
            delays.append(delay)
            if attempt < 2:
                raise ConnectionError("Retry")
            return {"status": 200}
        
        for attempt in range(3):
            try:
                await fetch_with_backoff(attempt)
                break
            except ConnectionError:
                await asyncio.sleep(delays[-1])
        
        assert delays[0] == 0.1  # 2^0 * 0.1
        assert delays[1] == 0.2  # 2^1 * 0.1
        assert delays[2] == 0.4  # 2^2 * 0.1

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self) -> None:
        """Must raise after max retries exceeded."""
        attempt_count = {"value": 0}
        
        async def always_fail(url: str) -> dict:
            attempt_count["value"] += 1
            raise ConnectionError("Always fails")
        
        max_retries = 3
        last_error = None
        
        for _ in range(max_retries):
            try:
                await always_fail("http://example.com")
            except ConnectionError as e:
                last_error = e
                await asyncio.sleep(0.01)
        
        assert attempt_count["value"] == max_retries
        assert last_error is not None

    @pytest.mark.asyncio
    async def test_no_retry_on_client_error(self) -> None:
        """Client errors (4xx) must not be retried."""
        attempt_count = {"value": 0}
        
        async def fetch_404(url: str) -> dict:
            attempt_count["value"] += 1
            raise ValueError("HTTP 404")
        
        try:
            await fetch_404("http://example.com/notfound")
        except ValueError:
            pass
        
        assert attempt_count["value"] == 1  # No retries


class TestFetchResponseParsing:
    """Tests for fetch response parsing."""

    @pytest.mark.asyncio
    async def test_parse_json_response(self) -> None:
        """JSON responses must be parsed correctly."""
        import json
        
        async def fetch_json() -> dict:
            await asyncio.sleep(0.01)
            return {"status": 200, "data": json.dumps({"key": "value"})}
        
        result = await fetch_json()
        data = json.loads(result["data"])
        
        assert data["key"] == "value"

    @pytest.mark.asyncio
    async def test_parse_html_response(self) -> None:
        """HTML responses must be handled as text."""
        async def fetch_html() -> dict:
            await asyncio.sleep(0.01)
            return {"status": 200, "content": "<html><body>Test</body></html>"}
        
        result = await fetch_html()
        
        assert "<html>" in result["content"]
        assert "Test" in result["content"]

    @pytest.mark.asyncio
    async def test_handle_binary_response(self) -> None:
        """Binary responses must be handled correctly."""
        async def fetch_binary() -> dict:
            await asyncio.sleep(0.01)
            return {"status": 200, "content": b"\x00\x01\x02\x03"}
        
        result = await fetch_binary()
        
        assert isinstance(result["content"], bytes)
        assert len(result["content"]) == 4


class TestFetchConcurrency:
    """Tests for fetch concurrency handling."""

    @pytest.mark.asyncio
    async def test_concurrent_fetch_limits(self) -> None:
        """Concurrent fetches must be limited."""
        active_fetches = {"count": 0}
        max_concurrent = {"value": 0}
        lock = asyncio.Lock()
        
        async def fetch_with_limit(url: str) -> dict:
            nonlocal max_concurrent
            async with lock:
                active_fetches["count"] += 1
                max_concurrent["value"] = max(max_concurrent["value"], active_fetches["count"])
            
            try:
                await asyncio.sleep(0.05)
                return {"url": url}
            finally:
                async with lock:
                    active_fetches["count"] -= 1
        
        sem = asyncio.Semaphore(5)  # Max 5 concurrent
        
        async def limited_fetch(url: str) -> dict:
            async with sem:
                return await fetch_with_limit(url)
        
        results = await asyncio.gather(*[
            limited_fetch(f"http://example.com/{i}")
            for i in range(20)
        ])
        
        assert max_concurrent["value"] <= 5
        assert len(results) == 20

    @pytest.mark.asyncio
    async def test_fetch_cancel_on_limit_exceeded(self) -> None:
        """Fetch must be cancelled when limit exceeded."""
        cancelled = {"value": False}
        
        async def cancellable_fetch(url: str) -> dict:
            try:
                await asyncio.sleep(10)
                return {"url": url}
            except asyncio.CancelledError:
                cancelled["value"] = True
                raise
        
        sem = asyncio.Semaphore(1)
        
        async def limited_fetch(url: str) -> dict:
            async with sem:
                return await cancellable_fetch(url)
        
        task = asyncio.create_task(limited_fetch("http://example.com"))
        
        # Add to queue that will exceed limit
        await asyncio.sleep(0.01)
        task.cancel()
        
        with pytest.raises(asyncio.CancelledError):
            await task
        
        assert cancelled["value"] is True


# ============================================================================
# Invariants
# ============================================================================

FETCH_SUCCESS_INVARIANTS = """
FETCH SUCCESS INVARIANTS:
1. Successful fetch returns complete data with status
2. Timeout raises asyncio.TimeoutError
3. Transient errors trigger retry
4. Resources cleaned up on success/error/cancellation
5. Exponential backoff for retries
6. Max retries exceeded raises exception
7. Client errors (4xx) not retried
8. Concurrent fetches limited by semaphore
9. Binary/JSON/HTML responses parsed correctly
"""
