"""
metrics_registry/_core.py — Shared Components
==========================================

Core components extracted from the original metrics_registry.py for
shared use across the package. These are the building blocks:
- LRUCache/TTLCache: Bounded storage with eviction
- MetricSnapshot: Compact metric representation
- _AsyncBatchFlusher: Non-blocking disk writes
- Constants and validators

M1 8GB Safety:
- Bounded LRUCache (max 128 entries)
- TTL-based expiration (5 min default)
- Async batch-flush (non-blocking)
- __slots__ throughout for minimal memory

Sprint ISSUE-16 (2026-08-18)
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from compat.msgspec_gc_compat import Struct

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# Bounded metric storage (M1 8GB safe)
_MAX_COUNTER_CACHE_SIZE = 128
_MAX_GAUGE_CACHE_SIZE = 128
_TTL_SECONDS = 300.0  # 5 minutes - stale metrics expire

# Flush cadence
FLUSH_EVENTS = 100
FLUSH_SECONDS = 60

# Ring buffer - MUST remain bounded
MAX_SNAPSHOTS = 100
MAX_SPRINT_EVENTS = 100

# ── Predefined metric names (security bound) ───────────────────────────────────

METRIC_NAMES = frozenset(
    [
        # Orchestrator
        "orchestrator_rss_mb",
        "orchestrator_frontier_size",
        "orchestrator_evidence_ring_len",
        "orchestrator_tool_exec_events",
        "orchestrator_budget_remaining_tokens",
        "orchestrator_budget_remaining_time",
        "orchestrator_budget_remaining_api_calls",
        # Cache
        "cache_http_size",
        "cache_snapshot_size",
        "cache_frontier_size",
        # Memory
        "memory_open_fds",
        "memory_rss_mb",
        "memory_vms_mb",
        # ML
        "mlx_cache_hits",
        "mlx_cache_misses",
        "mlx_cache_size_bytes",
        "mlx_active_memory_bytes",
        "mlx_peak_memory_bytes",
        "mlx_cache_fragmentation_ratio",
        "mlx_kernel_compilation_time_ms",
        "mlx_kernel_cache_hit_rate",
        # Model
        "model_load_duration_ms",
        "model_unload_count",
        "model_load_failures",
        # Action
        "action_latency_ms",
        "thermal_throttle_events",
        "thermal_recovery_events",
        # Memory zones
        "memory_zone_normal_seconds",
        "memory_zone_high_seconds",
        "memory_zone_critical_seconds",
        # Circuit breaker
        "circuit_breaker_state_transitions",
        "circuit_breaker_open_count",
        "circuit_breaker_half_open_count",
        "circuit_breaker_closed_count",
        "circuit_breaker_recovery_success",
        "circuit_breaker_open_duration_s",
        "circuit_breaker_closed_duration_s",
        # Dark surface
        "dark_surface_pivots_attempted",
        "dark_surface_pivots_successful",
        "cover_traffic_fired",
        "alert_warning_circuit_breaker_open_over_30s",
        "memory_pressure_vs_finding_yield",
        "windup_entry_count",
        # Sprint
        "sprint_budget_elapsed_ms",
        "sprint_budget_remaining_ms",
        "sprint_budget_phase",
        "sprint_phase_duration_avg_ms",
        "sprint_phase_duration_p50_ms",
        "sprint_phase_duration_p95_ms",
        # Storage
        "duckdb_ingest_latency_ms",
        "duckdb_query_latency_ms",
        # Bounded gather
        "bounded_gather_tasks_gathered",
        "bounded_gather_tasks_errors",
        "bounded_gather_errors_suppressed",
        "memory_layer_pressure_pct",
        "fetch_coordinator_active",
        "fetch_coordinator_blocked_domains",
        "fetch_coordinator_circuit_open",
        # Pipeline (ISSUE-12)
        "stage_latency_ms",
        "stage_items_in",
        "stage_items_out",
        "stage_errors",
        "pipeline_stage_count",
        "pipeline_total_latency_ms",
        # Memory pressure correlation (ISSUE-12)
        "m1_memory_pressure",
        "m1_memory_available_gib",
        "m1_memory_rss_gib",
        # HTTP (ISSUE-16)
        "http_request_count",
        "http_request_latency_ms",
        "http_error_count",
        "http_circuit_breaker_state",
        "http_blocked_domains",
    ]
)

_GRAMMAR_KEYS = frozenset(["run_id", "branch_id", "provider_id", "action_id"])

# ── Data Structures ────────────────────────────────────────────────────────────


class MetricSnapshot(Struct):
    """A single metric snapshot - compact for M1 8GB."""

    ts: datetime
    name: str
    value: float
    labels: dict[str, str] | None = None
    correlation: dict[str, str | None] | None = None


@dataclass(slots=True)
class _BoundedCounter:
    """Bounded counter with access tracking for LRU eviction."""

    value: int
    last_update: float  # monotonic timestamp


# ── Async Batch Flusher ───────────────────────────────────────────────────────


class _AsyncBatchFlusher:
    """
    Background thread for async batch-flush to disk.

    Decouples disk I/O from metric collection to avoid blocking.
    Uses a queue-based producer/consumer pattern.
    """

    __slots__ = (
        "_queue",
        "_thread",
        "_running",
        "_persist_file",
        "_persist_file_path",
        "_orjson_available",
        "_flush_count",
        "_error_count",
        "_last_error",
        "_orjson",
    )

    def __init__(self, persist_file_path: Path | None = None) -> None:
        self._queue: queue.Queue[list[dict[str, Any]]] = queue.Queue(maxsize=10)
        self._thread: threading.Thread | None = None
        self._running = False
        self._persist_file: Any = None
        self._persist_file_path = persist_file_path
        self._flush_count = 0
        self._error_count = 0
        self._last_error: str | None = None

        # FIX-9: Import orjson once at init (not per-call)
        self._orjson: Any = None
        try:
            import orjson

            self._orjson = orjson
            self._orjson_available = True
        except ImportError:
            self._orjson_available = False

    def start(self) -> None:
        """Start the background flusher thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="metrics-flusher",
            daemon=True,
        )
        self._thread.start()
        logger.debug("[_AsyncBatchFlusher] started")

    def _run_loop(self) -> None:
        """Main loop - runs in background thread."""
        try:
            # Open persist file in this thread
            if self._persist_file_path:
                try:
                    self._persist_file = open(self._persist_file_path, "ab")
                except Exception as e:
                    logger.warning(f"[_AsyncBatchFlusher] Failed to open persist file: {e}")
                    self._persist_file = None
        except Exception:
            pass

        while self._running:
            try:
                # Block with timeout for graceful shutdown
                batch = self._queue.get(timeout=1.0)
                self._write_batch(batch)
            except queue.Empty:
                continue
            except Exception as e:
                self._error_count += 1
                self._last_error = str(e)
                logger.debug(f"[_AsyncBatchFlusher] queue error: {e}")

        # Drain queue on shutdown
        self._drain_queue()
        self._close()

    def _write_batch(self, batch: list[dict[str, Any]]) -> None:
        """Write batch to disk (thread-safe)."""
        if not self._persist_file:
            return

        try:
            # FIX-9: Use pre-imported orjson (avoids repeated import overhead)
            if self._orjson_available and self._orjson:
                for metric in batch:
                    line = self._orjson.dumps(metric, option=self._orjson.OPT_APPEND_NEWLINE)
                    self._persist_file.write(line)
            else:
                # Fallback: import stdlib json (should never happen in production)
                import json as _stdlib_json

                for metric in batch:
                    line = _stdlib_json.dumps(metric).encode("utf-8") + b"\n"
                    self._persist_file.write(line)
            self._persist_file.flush()
            os.fsync(self._persist_file.fileno())
            self._flush_count += len(batch)
        except Exception as e:
            self._error_count += 1
            self._last_error = str(e)
            logger.debug(f"[_AsyncBatchFlusher] write error: {e}")

    def _drain_queue(self) -> None:
        """Drain remaining items on shutdown."""
        while True:
            try:
                batch = self._queue.get_nowait()
                self._write_batch(batch)
            except queue.Empty:
                break

    def _close(self) -> None:
        """Close persist file."""
        if self._persist_file:
            try:
                self._persist_file.flush()
                os.fsync(self._persist_file.fileno())
                self._persist_file.close()
            except Exception:
                pass
            self._persist_file = None

    def enqueue(self, batch: list[dict[str, Any]]) -> bool:
        """
        Enqueue batch for async flush.

        Returns True if enqueued, False if queue is full (drop oldest).
        """
        try:
            self._queue.put_nowait(batch)
            return True
        except queue.Full:
            # Drop oldest batch if queue is full (M1 8GB safety)
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(batch)
                return True
            except queue.Empty:
                return False

    def stop(self) -> None:
        """Stop the flusher thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.debug(f"[_AsyncBatchFlusher] stopped (flushes={self._flush_count}, errors={self._error_count})")

    @property
    def stats(self) -> dict[str, Any]:
        """Return flusher statistics."""
        return {
            "running": self._running,
            "flush_count": self._flush_count,
            "error_count": self._error_count,
            "last_error": self._last_error,
            "queue_size": self._queue.qsize(),
        }
