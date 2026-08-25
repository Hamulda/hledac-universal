"""
Telemetry Aggregation Rust Integration Wiring
===========================================

Wires rust_extensions/src/telemetry_agg.rs to:
- otel/ metrics collection
- utils/telemetry.py

Purpose:
- Lock-free atomic counters
- HDR histograms for latency percentiles
- MPSC channel for cross-thread telemetry

Integration Point:
- Sprint metrics collection
- Performance monitoring

Usage:
    from rust_extensions.wiring.telemetry_agg_wiring import telemetry_wired
    
    counter = telemetry_wired.create_counter("fetch_requests")
    counter.inc()
    counter.add_bytes(1024)
    
    histogram = telemetry_wired.create_histogram("fetch_latency")
    histogram.record(duration_ns)
    stats = histogram.percentiles()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from dataclasses import dataclass

logger = logging.getLogger(__name__)

from rust_extensions.integrations import TelemetryIntegration, TelemetryCounter, TelemetryHistogram

# Global telemetry instance
_telemetry = TelemetryIntegration()

# Cache of created counters and histograms
_counters: dict[str, TelemetryCounter] = {}
_histograms: dict[str, TelemetryHistogram] = {}

def telemetry_wired() -> TelemetryIntegration:
    """Get the wired telemetry integration."""
    return _telemetry

def get_counter(name: str) -> TelemetryCounter:
    """
    Get or create a named counter.

    Args:
        name: Counter name (e.g., "fetch_requests", "bytes_received")

    Returns:
        TelemetryCounter instance.
    """
    if name not in _counters:
        _counters[name] = _telemetry.create_counter(name)
    return _counters[name]

def get_histogram(
    name: str,
    min_value: int = 1_000_000,  # 1ms
    max_value: int = 3_600_000_000_000,  # 1 hour
) -> TelemetryHistogram:
    """
    Get or create a named histogram.

    Args:
        name: Histogram name (e.g., "fetch_latency", "processing_time")
        min_value: Minimum value in nanoseconds (default 1ms)
        max_value: Maximum value in nanoseconds (default 1 hour)

    Returns:
        TelemetryHistogram instance.
    """
    if name not in _histograms:
        _histograms[name] = _telemetry.create_histogram(name, min_value, max_value)
    return _histograms[name]

# Decorator for automatic telemetry
def tracked(
    counter_name: str | None = None,
    histogram_name: str | None = None,
    duration_field: str = "duration_s",
):
    """
    Decorator to automatically track function calls.

    Args:
        counter_name: Name for call counter (defaults to function name)
        histogram_name: Name for duration histogram (defaults to function name + "_duration")
        duration_field: Field name for duration in returned result

    Usage:
        @tracked(counter_name="my_function_calls", histogram_name="my_function_duration")
        async def my_function():
            ...
    """
    import functools
    import time

    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            c_name = counter_name or func.__name__
            h_name = histogram_name or f"{func.__name__}_duration"

            counter = get_counter(c_name)
            histogram = get_histogram(h_name)

            counter.inc()
            start = time.perf_counter_ns()

            try:
                result = await func(*args, **kwargs)
                duration_ns = time.perf_counter_ns() - start
                histogram.record(duration_ns)
                return result
            except Exception:
                duration_ns = time.perf_counter_ns() - start
                histogram.record(duration_ns)
                raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            c_name = counter_name or func.__name__
            h_name = histogram_name or f"{func.__name__}_duration"

            counter = get_counter(c_name)
            histogram = get_histogram(h_name)

            counter.inc()
            start = time.perf_counter_ns()

            try:
                result = func(*args, **kwargs)
                duration_ns = time.perf_counter_ns() - start
                histogram.record(duration_ns)
                return result
            except Exception:
                duration_ns = time.perf_counter_ns() - start
                histogram.record(duration_ns)
                raise

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator

@dataclass
class TelemetrySnapshot:
    """Snapshot of all telemetry data."""
    counters: dict[str, tuple[int, int]]
    histograms: dict[str, dict[str, float]]

def get_snapshot() -> TelemetrySnapshot:
    """
    Get a snapshot of all telemetry data.

    Returns:
        TelemetrySnapshot with all counter and histogram data.
    """
    counters = {}
    for name, counter in _counters.items():
        counters[name] = counter.get()

    histograms = {}
    for name, histogram in _histograms.items():
        histograms[name] = histogram.percentiles()

    return TelemetrySnapshot(counters=counters, histograms=histograms)

def reset_all() -> None:
    """Reset all counters and histograms."""
    for counter in _counters.values():
        counter._python_count = 0
        counter._python_bytes = 0
    _histograms.clear()

if _telemetry.available:
    logger.info("[Telemetry] Rust telemetry_agg.rs integration: ENABLED")
else:
    logger.info("[Telemetry] Rust telemetry_agg.rs integration: DISABLED (using Python fallback)")
