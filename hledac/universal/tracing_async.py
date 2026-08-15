"""
Async Span Context Manager for Python asyncio ↔ Rust tokio bridging.

MODERN-CROSS-2: Provides async-aware span wrapper for Python asyncio operations.

Usage:
    from hledac.universal.tracing_async import AsyncSpan
    
    async def my_async_fn():
        with AsyncSpan("my_operation") as span:
            result = await some_async_operation()
            return result
    
    # Or manually:
    async def manual_span():
        trace_id, span_id, span_key = async_span_enter("manual_operation")
        try:
            result = await some_async_operation()
            return result
        finally:
            async_span_exit(span_key, trace_id, span_id)

Environment variables:
    HLEDAC_TRACING_ENABLED=1  # Enable/disable tracing (default: 1)
"""

from __future__ import annotations

import asyncio
import functools
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar
from collections.abc import Callable
from _core import aclose

if TYPE_CHECKING:
    from typing import ParamSpec

    P = ParamSpec("P")
    T = TypeVar("T")

# Try to import Rust tracing functions
try:
    from hledac.universal.rust_extensions.tracing import (
        async_span_enter as _rust_enter,
        async_span_exit as _rust_exit,
        is_tracing_active as _is_active,
        get_active_async_span_count as _get_span_count,
        get_active_async_spans as _get_spans,
    )
    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False


def _noop_enter(name: str) -> tuple[str, str, str]:
    """No-op enter when Rust is unavailable."""
    return ("", "", "")


def _noop_exit(span_key: str, trace_id: str, span_id: str) -> None:
    """No-op exit when Rust is unavailable."""
    pass


# Use Rust implementation if available, otherwise no-op
_async_span_enter = _rust_enter if _RUST_AVAILABLE else _noop_enter
_async_span_exit = _rust_exit if _RUST_AVAILABLE else _noop_exit


class AsyncSpan:
    """
    Context manager for async spans that properly handles await points.
    
    This wraps the Rust async_span_enter/exit to provide a Pythonic
    context manager interface for async operations.
    
    Usage:
        async def fetch_url(url: str) -> str:
            with AsyncSpan("fetch", url=url) as span:
                content = await async_fetch(url)
                span.add_attribute("content_length", len(content))
                return content
    
    Attributes:
        name: Operation name
        trace_id: W3C trace ID (hex string)
        span_id: W3C span ID (hex string)
        span_key: Internal key for tracking
        is_active: Whether tracing is enabled
        attributes: Dict of span attributes
    """
    
    __slots__ = (
        "_name",
        "_trace_id",
        "_span_id",
        "_span_key",
        "_is_active",
        "_attributes",
        "_entered",
    )
    
    def __init__(
        self,
        name: str,
        **attributes: str | int | float | bool,
    ) -> None:
        """
        Initialize an async span.
        
        Args:
            name: Operation name (will be prefixed with "async:")
            **attributes: Initial span attributes
        """
        self._name = name
        self._trace_id: str = ""
        self._span_id: str = ""
        self._span_key: str = ""
        self._is_active: bool = False
        self._attributes: dict = dict(attributes)
        self._entered = False
    
    def __enter__(self) -> AsyncSpan:
        """Enter the span synchronously (before any await)."""
        if _RUST_AVAILABLE:
            self._trace_id, self._span_id, self._span_key = _async_span_enter(self._name)
            self._is_active = bool(self._trace_id)
        else:
            self._is_active = False
        self._entered = True
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the span (after all awaits complete)."""
        if self._entered and _RUST_AVAILABLE:
            _async_span_exit(self._span_key, self._trace_id, self._span_id)
        self._entered = False
    
    def add_attribute(self, key: str, value: str | int | float | bool) -> None:
        """Add an attribute to the span."""
        self._attributes[key] = value
    
    @property
    def name(self) -> str:
        """Get the operation name."""
        return self._name
    
    @property
    def trace_id(self) -> str:
        """Get the W3C trace ID."""
        return self._trace_id
    
    @property
    def span_id(self) -> str:
        """Get the W3C span ID."""
        return self._span_id
    
    @property
    def is_active(self) -> bool:
        """Check if tracing is active."""
        return self._is_active
    
    @property
    def attributes(self) -> dict:
        """Get span attributes."""
        return self._attributes.copy()
    
    def __repr__(self) -> str:
        return (
            f"AsyncSpan(name={self._name!r}, "
            f"trace_id={self._trace_id[:8]!r}..., "
            f"is_active={self._is_active})"
        )


def traced_async(
    name: str | None = None,
) -> Callable[[Callable[P, asyncio.Future[T]]], Callable[P, asyncio.Future[T]]]:
    """
    Decorator that wraps an async function with an AsyncSpan.
    
    Usage:
        @traced_async()
        async def fetch_url(url: str) -> str:
            return await async_fetch(url)
        
        @traced_async("custom_name")
        async def process(data: bytes) -> dict:
            return parse(data)
    
    Args:
        name: Optional custom span name (defaults to function.__name__)
    """
    def decorator(func: Callable[P, asyncio.Future[T]]) -> Callable[P, asyncio.Future[T]]:
        span_name = name or func.__name__
        
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            with AsyncSpan(span_name, function=func.__name__) as span:
                try:
                    result = await func(*args, **kwargs)
                    span.add_attribute("success", True)
                    return result
                except Exception as e:
                    span.add_attribute("success", False)
                    span.add_attribute("error", type(e).__name__)
                    raise
        
        return wrapper
    return decorator


def get_active_spans() -> list[tuple[str, str, int]]:
    """
    Get all currently active async spans.
    
    Returns:
        List of tuples: (span_key, operation_name, elapsed_ms)
    """
    if not _RUST_AVAILABLE:
        return []
    
    try:
        return _get_spans() or []
    except Exception:
        return []


def get_active_span_count() -> int:
    """
    Get the count of currently active async spans.
    
    Returns:
        Number of active spans
    """
    if not _RUST_AVAILABLE:
        return 0
    
    try:
        return _get_span_count()
    except Exception:
        return 0


def is_tracing_enabled() -> bool:
    """
    Check if tracing is enabled and active.
    
    Returns:
        True if tracing is enabled
    """
    if not _RUST_AVAILABLE:
        return False
    
    try:
        return _is_active()
    except Exception:
        return False


@contextmanager
def span_context(name: str, **attributes):
    """
    Context manager that works for both sync and async code.
    
    For async code, prefer AsyncSpan directly for proper await handling.
    This is a convenience wrapper for code that may be called from
    both sync and async contexts.
    
    Usage:
        with span_context("my_operation", key="value") as span:
            if span.is_async:
                await async_work()
            else:
                sync_work()
    """
    # MODERN-CROSS-2 FIX: Fixed syntax error - conditional import must be separate
    _sync_start = None
    _sync_enter = None
    _sync_exit = None
    
    if _RUST_AVAILABLE:
        try:
            from hledac.universal.rust_extensions.tracing import (
                start_span as _sync_start,
                span_enter as _sync_enter,
                span_exit as _sync_exit,
            )
            trace_id, span_id = _sync_start("", name)
            if trace_id:
                _sync_enter(trace_id, span_id)
        except ImportError:
            pass
    
    class SyncSpan:
        is_async = False
        attributes = attributes
        
        def add_attribute(self, key, value):
            self.attributes[key] = value
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            if _sync_exit:
                _sync_exit()
    
    try:
        yield SyncSpan()
    finally:
        pass


__all__ = [
    "AsyncSpan",
    "traced_async",
    "get_active_spans",
    "get_active_span_count",
    "is_tracing_enabled",
    "span_context",
]
