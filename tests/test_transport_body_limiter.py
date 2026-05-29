"""
Tests for transport/body_limiter.py — pure async body cap helper.

No network I/O. No router involvement.
"""

from __future__ import annotations

import asyncio

import pytest

from transport.body_limiter import read_body_with_cap


class TestReadBodyWithCap:
    """Invariant table for body_limiter helper."""

    async def test_empty_stream(self):
        """Invariant: empty async stream returns empty bytes, truncated=False."""
        chunks = anext_iter([])

        result, truncated = await read_body_with_cap(chunks, max_bytes=100)

        assert result == b""
        assert truncated is False

    async def test_under_cap(self):
        """Invariant: stream total < max_bytes returns full content, truncated=False."""
        chunks = anext_iter([b"hello", b" ", b"world"])

        result, truncated = await read_body_with_cap(chunks, max_bytes=100)

        assert result == b"hello world"
        assert truncated is False

    async def test_exactly_at_cap(self):
        """Invariant: stream total == max_bytes returns full content, truncated=False."""
        chunks = anext_iter([b"abc"])

        result, truncated = await read_body_with_cap(chunks, max_bytes=3)

        assert result == b"abc"
        assert truncated is False

    async def test_over_cap_single_chunk(self):
        """Invariant: single chunk exceeding cap returns truncated content, truncated=True."""
        chunks = anext_iter([b"hello world"])

        result, truncated = await read_body_with_cap(chunks, max_bytes=5)

        assert result == b"hello"
        assert truncated is True

    async def test_over_cap_multiple_chunks(self):
        """Invariant: multi-chunk stream exceeding cap stops at boundary, truncated=True."""
        chunks = anext_iter([b"0123456789", b"ABCDEFGHIJ", b"extra"])

        result, truncated = await read_body_with_cap(chunks, max_bytes=15)

        assert result == b"0123456789ABCDE"
        assert truncated is True

    async def test_max_bytes_zero_no_cap(self):
        """Invariant: max_bytes=0 means no cap, collects entire stream."""
        chunks = anext_iter([b"x"] * 100)

        result, truncated = await read_body_with_cap(chunks, max_bytes=0)

        assert len(result) == 100
        assert truncated is False

    async def test_chunk_exception_propagates(self):
        """Invariant: exceptions from chunk iterator propagate (except CancelledError)."""
        async def failing_chunks():
            yield b"prefix"
            raise RuntimeError("chunk iterator failure")

        with pytest.raises(RuntimeError, match="chunk iterator failure"):
            await read_body_with_cap(failing_chunks(), max_bytes=1000)

    async def test_cancelled_error_re_raises(self):
        """Invariant: asyncio.CancelledError is re-raised, not caught."""
        async def cancelled_chunks():
            yield b"prefix"
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await read_body_with_cap(cancelled_chunks(), max_bytes=1000)


# ---------------------------------------------------------------------------
# HTTPX lane integration tests
# Simulate httpx-style async chunked iterator via aiter_chunked()
# ---------------------------------------------------------------------------

class TestHttpxLaneBodyCap:
    """HTTPX lane body cap via read_body_with_cap + aiter_chunked."""

    async def test_httpx_under_cap(self):
        """HTTPX lane: body under cap returns full content, truncated=False."""
        # Simulate httpx response.aiter_chunked() yielding byte chunks
        chunks = anext_iter([b"<!DOCTYPE html>", b"<html>", b"</html>"])

        result, truncated = await read_body_with_cap(chunks, max_bytes=8192)

        assert result == b"<!DOCTYPE html><html></html>"
        assert truncated is False

    async def test_httpx_exactly_at_cap(self):
        """HTTPX lane: body exactly at cap returns full content, truncated=False."""
        chunks = anext_iter([b"exactly32bytes=================="])  # 32 bytes

        result, truncated = await read_body_with_cap(chunks, max_bytes=32)

        assert result == b"exactly32bytes=================="
        assert truncated is False

    async def test_httpx_over_cap_truncates(self):
        """HTTPX lane: body over cap is truncated, truncated=True."""
        # Simulate a large HTML body split across multiple chunks
        chunks = anext_iter([
            b"<html><head><title>Big Page</title></head>",
            b"<body><p>Lots of content here...</p>",
            b"<p>More content that should be truncated</p>",
            b"<footer>Footer content</footer></body></html>",
        ])

        result, truncated = await read_body_with_cap(chunks, max_bytes=50)

        assert len(result) == 50
        assert truncated is True
        # Cap stops mid-chunk
        assert result == b"<html><head><title>Big Page</title></head><body><p"

    async def test_httpx_cancelled_error_re_raises(self):
        """HTTPX lane: CancelledError from aiter_chunked re-raised."""
        async def httpx_cancelled_chunks():
            yield b"prefix"
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await read_body_with_cap(httpx_cancelled_chunks(), max_bytes=1000)

    async def test_httpx_network_exception_fails_soft(self):
        """HTTPX lane: network exception propagates (fail-soft, not caught by cap)."""
        async def network_error_chunks():
            yield b"prefix"
            raise ConnectionResetError("connection reset by peer")

        with pytest.raises(ConnectionResetError, match="connection reset by peer"):
            await read_body_with_cap(network_error_chunks(), max_bytes=1000)

    async def test_httpx_empty_body(self):
        """HTTPX lane: empty response body returns b'', truncated=False."""
        chunks = anext_iter([b""])

        result, truncated = await read_body_with_cap(chunks, max_bytes=8192)

        assert result == b""
        assert truncated is False

    async def test_httpx_single_empty_chunk(self):
        """HTTPX lane: single empty chunk returns b'', truncated=False."""
        chunks = anext_iter([])

        result, truncated = await read_body_with_cap(chunks, max_bytes=8192)

        assert result == b""
        assert truncated is False


# ---------------------------------------------------------------------------
# Helper: build async iterator from a list of chunks
# ---------------------------------------------------------------------------

async def anext_iter(chunks: list[bytes]):
    """Yield chunks from a list using an async iterator."""
    for chunk in chunks:
        yield chunk
